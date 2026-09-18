#!/usr/bin/env python3
"""Account for every series translation unit this platform produces no object from.

The V1 gate used to call all of them "absent", which folded two unlike facts
into one passing badge:

  * base/system/sys_info_win.cc is not in a Linux build graph because it is
    Windows-only. Skipping it is correct, and saying so is the whole point of
    reporting it.
  * third_party/ffmpeg/chromium/config/Chrome/linux/x64/libavcodec/codec_list.c
    is not in any build graph because it is never a translation unit at all:
    libavcodec/allcodecs.c includes it textually. Skipping it meant patch
    0061's codec registration was compiled by nothing the gate checked, while
    the gate reported 129 compiles / 3 absent and a green badge.

"Does not apply here" and "we did not check this" are not the same claim, so
absence is classified now, and an absence nothing accounts for fails the gate:

  include-only      an object this gate compiled recorded the file as one of
                    its own inputs, so the patched bytes reached a compiler.
                    Verified, not skipped.
  absent-platform   declared in the absences file as platform-scoped, with the
                    GN condition that scopes it as the evidence.
  absent-config     declared there as excluded by this build's configuration.
  unexplained       none of the above. The gate fails and names the file.

WHY THE INCLUDER'S EXISTENCE IS NOT ENOUGH

"The includer is compiled, therefore the include is covered" is wrong, and the
ffmpeg list files are the case that proves it. third_party/ffmpeg/BUILD.gn puts
chromium/config/$ffmpeg_branding/$os_config/$ffmpeg_arch on the include path,
and ffmpeg_options.gni:63 sets `os_config = current_os` with $ffmpeg_arch
following current_cpu, so allcodecs.c reads a *different* codec_list.c on
every target. Measured on macos-arm64 at
152.0.7977.83:

    $ ninja -t deps obj/third_party/ffmpeg/ffmpeg_internal/allcodecs.o
    obj/.../allcodecs.o: #deps 32, deps mtime ... (VALID)
        ../../third_party/ffmpeg/chromium/config/Chrome/mac/arm64/libavcodec/codec_list.c

allcodecs.c is compiled on Windows too, and there it never reads the linux/x64
file patch 0061 edits. Keying verification on the includer's own recorded
dependencies tells those two cases apart. Keying it on the includer merely
existing would pin the same unearned passing badge on an unread file, one level
further from where anyone would look for it.
"""

from __future__ import annotations

import argparse
import fnmatch
import os
import posixpath
import re
import sys

# Statuses this module assigns. The gate's own "compiles" and "fails" are
# decided from objects and are not produced here.
STATUS_INCLUDE_ONLY = "include-only"
STATUS_ABSENT_PLATFORM = "absent-platform"
STATUS_ABSENT_CONFIG = "absent-config"
STATUS_UNEXPLAINED = "unexplained"

# A declared absence states which kind it is, and the status follows from the
# category so a reader of the report never has to open the declarations file to
# learn what kind of skip they are looking at.
CATEGORY_STATUS = {
    "platform": STATUS_ABSENT_PLATFORM,
    "config": STATUS_ABSENT_CONFIG,
}

# Statuses that mean the patched bytes reached a compiler on this platform.
COVERED_STATUSES = frozenset({"compiles", STATUS_INCLUDE_ONLY})
# Statuses that fail a run.
FAILING_STATUSES = frozenset({"fails", STATUS_UNEXPLAINED})

# Files that can textually include another file. Deliberately wider than the
# gate's translation-unit suffixes: the includer of a .c fragment is usually a
# .c, but an .asm fragment is included by an .asm and a .inc by a header, and a
# discovery pass that could only find one of those would answer "no includer"
# for a file that plainly has one.
SCAN_SUFFIXES = (
    ".c", ".cc", ".cpp", ".cxx", ".m", ".mm",
    ".h", ".hh", ".hpp", ".hxx", ".inc",
    ".asm", ".S", ".s",
)

# `#include "x"`, `#include <x>` and nasm's `%include "x"`. Anchored at the
# start of the line so a mention inside a string or a comment body does not
# match; the operand is whatever the preprocessor would be handed.
INCLUDE_RE = re.compile(rb'^[ \t]*[#%][ \t]*include[ \t]*[<"]([^">]+)[">]')

# `obj/foo/bar.o: #deps 32, deps mtime 123 (VALID)` from `ninja -t deps`.
DEPS_HEADER_RE = re.compile(r"^(\S.*?): #deps (\d+), deps mtime \S+ \((\w+)\)\s*$")


# --------------------------------------------------------------- declarations
def load_declarations(path):
    """Read the absences file: path, target globs, category, evidence.

    Tab-separated because a GN condition is the evidence field and GN
    conditions contain spaces, quotes, commas and parentheses. Comments and
    blank lines are skipped the way patches/series does it, so the two files
    behave the same for anyone editing them.
    """
    declarations = []
    with open(path, encoding="utf-8") as handle:
        for number, raw in enumerate(handle, 1):
            line = raw.split("#", 1)[0].strip()
            if not line:
                continue
            fields = line.split("\t")
            fields = [field.strip() for field in fields if field.strip()]
            if len(fields) != 4:
                raise SystemExit(
                    f"{path}:{number}: expected 4 tab-separated fields "
                    f"(path, targets, category, evidence), got {len(fields)}"
                )
            unit, targets, category, evidence = fields
            if category not in CATEGORY_STATUS:
                raise SystemExit(
                    f"{path}:{number}: unknown category {category!r}; "
                    f"expected one of {', '.join(sorted(CATEGORY_STATUS))}"
                )
            declarations.append({
                "path": unit,
                "targets": [glob for glob in targets.split(",") if glob],
                "category": category,
                "evidence": evidence,
                "line": number,
            })
    return declarations


def declaration_for(declarations, unit, target):
    """The declaration covering this unit on this target, or None."""
    for declaration in declarations:
        if declaration["path"] != unit:
            continue
        if any(fnmatch.fnmatchcase(target, glob) for glob in declaration["targets"]):
            return declaration
    return None


def stale_declarations(declarations, statuses, target):
    """Declarations that claim an absence this run disproves.

    A hand-maintained list is only safe if both directions are checked. A
    missing entry already fails the run as unexplained; this is the other
    direction, so an entry for a file that now compiles, or for a file the
    series no longer touches, cannot sit there quietly asserting something
    untrue about a platform it still matches.
    """
    stale = []
    for declaration in declarations:
        if not any(fnmatch.fnmatchcase(target, glob) for glob in declaration["targets"]):
            continue
        unit = declaration["path"]
        if unit not in statuses:
            stale.append((declaration, "the series does not touch this file"))
        elif statuses[unit] in COVERED_STATUSES:
            stale.append((declaration, f"this target reports it as {statuses[unit]}"))
    return stale


# ----------------------------------------------------------------- discovery
def include_scope(root, unit):
    """Nearest ancestor directory of `unit` holding a BUILD.gn, or None.

    The search for an includer has to be bounded by something, and the choice
    is between a bound and a scan of a 50 GB checkout per absent file. A GN
    build file marks the module a source belongs to, which is the same boundary
    the build itself uses, and it is where a textual include of a sibling
    fragment comes from in every case this gate has met: the ffmpeg list files
    resolve to third_party/ffmpeg, 4k files and a fraction of a second, and no
    absent unit measured here scopes to more than third_party/blink/renderer/
    platform. Missing an includer costs a false `unexplained`, which fails the
    run and names the file, so the bound cannot turn into a silent pass.
    """
    directory = posixpath.dirname(unit)
    while directory:
        if os.path.isfile(os.path.join(root, directory, "BUILD.gn")):
            return directory
        directory = posixpath.dirname(directory)
    return None


def _strip_updirs(operand):
    operand = operand.replace("\\", "/")
    while operand.startswith("./") or operand.startswith("../"):
        operand = operand[2:] if operand.startswith("./") else operand[3:]
    return operand


def _names_unit(unit, operand):
    """Does this include operand name `unit`?

    Suffix match on whole path components. An include is written relative to
    some directory on the search path -- allcodecs.c says
    "libavcodec/codec_list.c" for a file that lives under
    chromium/config/Chrome/linux/x64/ -- so the operand is a tail of the unit
    path, never the whole of it.
    """
    operand = _strip_updirs(operand)
    if not operand:
        return False
    return unit == operand or unit.endswith("/" + operand)


def discover_includers(root, unit):
    """Every file in `unit`'s module that textually includes it.

    No filename is special-cased: the operand is matched against the unit's
    path, so a future include-only file is found by the same pass that finds
    today's.
    """
    scope = include_scope(root, unit)
    if scope is None:
        return scope, []
    basename = posixpath.basename(unit).encode("utf-8")
    found = []
    for dirpath, dirnames, filenames in os.walk(os.path.join(root, scope)):
        # Build output and version-control metadata are not sources, and out/
        # holds a copy of half the generated tree.
        dirnames[:] = [d for d in dirnames if not d.startswith(".") and d != "out"]
        for filename in filenames:
            if not filename.endswith(SCAN_SUFFIXES):
                continue
            full = os.path.join(dirpath, filename)
            try:
                with open(full, "rb") as handle:
                    blob = handle.read()
            except OSError:
                continue
            # Cheap prefilter: the basename must appear somewhere at all before
            # the file is worth splitting into lines.
            if basename not in blob:
                continue
            relative = os.path.relpath(full, root).replace(os.sep, "/")
            if relative == unit:
                continue
            for number, line in enumerate(blob.split(b"\n"), 1):
                match = INCLUDE_RE.match(line)
                if not match:
                    continue
                operand = match.group(1).decode("utf-8", "replace")
                if _names_unit(unit, operand):
                    found.append({"path": relative, "line": number, "operand": operand})
    found.sort(key=lambda hit: (hit["path"], hit["line"]))
    return scope, found


# ------------------------------------------------------------------- parsing
def parse_source_index(path):
    """source<TAB>object object ... as written by the gate's compdb pass."""
    index = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            source, _, objects = raw.rstrip("\r\n").partition("\t")
            if source and not source.startswith("#"):
                index[source] = objects.split()
    return index


def parse_includers(path):
    """unit<TAB>includer<TAB>line<TAB>operand<TAB>object ... from `discover`."""
    includers = {}
    with open(path, encoding="utf-8") as handle:
        for raw in handle:
            fields = raw.rstrip("\r\n").split("\t")
            if len(fields) < 4 or not fields[0] or fields[0].startswith("#"):
                continue
            unit, includer, line, operand = fields[:4]
            objects = fields[4].split() if len(fields) > 4 else []
            includers.setdefault(unit, []).append({
                "path": includer,
                "line": int(line) if line.isdigit() else 0,
                "operand": operand,
                "objects": objects,
            })
    return includers


def parse_ninja_deps(text, prefix=""):
    """object -> set of source paths, from `ninja -t deps` output.

    Only (VALID) blocks are kept. A stale entry describes a build that is no
    longer the one on disk, and accepting it would let a dependency recorded
    before a patch was applied vouch for the patch.
    """
    deps = {}
    current = None
    for line in text.splitlines():
        header = DEPS_HEADER_RE.match(line.rstrip("\r"))
        if header:
            current = deps.setdefault(header.group(1), set()) if header.group(3) == "VALID" else None
            continue
        if current is None:
            continue
        if not line.startswith((" ", "\t")):
            current = None
            continue
        dep = line.strip().rstrip("\r").replace("\\", "/")
        if prefix and dep.startswith(prefix):
            dep = dep[len(prefix):]
        current.add(dep)
    return deps


# -------------------------------------------------------------- classification
def classify_absent(unit, target, includers, deps, declarations, compiled=True):
    """Classify one unit this platform produced no object from.

    `includers` is the list discovered for this unit, each with the objects its
    own source compiles into; `deps` maps object to the set of sources the
    compiler recorded reading. `compiled` is False for a membership-only run,
    where an includer with no recorded dependencies has simply not been built
    yet and saying "unexplained" would be a statement about the run rather
    than about the series.
    """
    verified = []
    unbuilt = []
    for includer in includers:
        hit = False
        for obj in includer["objects"]:
            if unit in deps.get(obj, ()):
                verified.append({
                    "path": includer["path"],
                    "line": includer["line"],
                    "operand": includer["operand"],
                    "object": obj,
                })
                hit = True
        if not hit:
            unbuilt.append(includer)

    if verified:
        first = verified[0]
        return {
            "status": STATUS_INCLUDE_ONLY,
            "category": "include-only",
            "reason": (
                f"no object of its own; compiled as part of {first['path']} "
                f"(line {first['line']}: include \"{first['operand']}\"), which "
                f"{first['object']} recorded reading"
            ),
            "included_by": verified,
        }

    declaration = declaration_for(declarations, unit, target)
    if declaration:
        return {
            "status": CATEGORY_STATUS[declaration["category"]],
            "category": declaration["category"],
            "reason": declaration["evidence"],
            "declared_at": declaration["line"],
        }

    # An includer object that ninja has a dependency record for has already
    # answered: it read some set of files, and this unit is not among them.
    # That answer stands whether or not this invocation compiled anything, so
    # only a total absence of records is indeterminate. Getting this backwards
    # made a membership-only run report `unchecked` for a file whose includer
    # demonstrably read a different target's copy, which is a real negative
    # wearing the one word that excuses itself.
    recorded = [obj for inc in includers for obj in inc["objects"] if obj in deps]
    if unbuilt and not compiled and not recorded:
        return {
            "status": "unchecked",
            "category": "include-only",
            "reason": (
                f"include-only candidate: {unbuilt[0]['path']}:{unbuilt[0]['line']} includes it; "
                "membership-only run compiled nothing and ninja holds no dependency "
                "record for the includer, so nothing proves it either way yet"
            ),
            "candidate_includers": unbuilt,
        }

    if unbuilt:
        names = ", ".join(
            f"{i['path']}:{i['line']}" + (f" -> {' '.join(i['objects'])}" if i["objects"] else " (no object here)")
            for i in unbuilt
        )
        return {
            "status": STATUS_UNEXPLAINED,
            "category": "unexplained",
            "reason": (
                f"include-only: {names} includes it, but no object of that includer "
                f"recorded reading it on {target}, so nothing proves the patched bytes "
                "were compiled. Put the includer in this platform's build graph, or "
                "declare the absence with its GN condition"
            ),
            "candidate_includers": unbuilt,
        }

    return {
        "status": STATUS_UNEXPLAINED,
        "category": "unexplained",
        "reason": (
            f"no object on {target}, no file includes it, and no declared absence "
            "accounts for it. A patched file the gate cannot account for is an "
            "unchecked patch, not a skip"
        ),
    }


# ---------------------------------------------------------------------- main
def main(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    sub = parser.add_subparsers(dest="command", required=True)

    discover = sub.add_parser(
        "discover",
        help="find the includers of files that produce no object, and the objects those includers produce",
    )
    discover.add_argument("--root", required=True, help="Chromium checkout root")
    discover.add_argument("--source-index", help="source<TAB>objects index from ninja -t compdb")
    discover.add_argument("units", nargs="*", help="unit paths that produced no object")

    args = parser.parse_args(argv)

    if args.command == "discover":
        # Written with an explicit "\n" so a Windows runner does not put a
        # carriage return on the last field of every row, which is the defect
        # the gate already fixed once at its other writer.
        if hasattr(sys.stdout, "reconfigure"):
            sys.stdout.reconfigure(newline="\n")
        index = parse_source_index(args.source_index) if args.source_index else {}
        for unit in args.units:
            if not unit:
                continue
            _, includers = discover_includers(args.root, unit)
            for includer in includers:
                objects = " ".join(index.get(includer["path"], []))
                print(f"{unit}\t{includer['path']}\t{includer['line']}\t{includer['operand']}\t{objects}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
