#!/usr/bin/env python3
"""Classify every series translation unit from one gate run, and write the report.

scripts/checkseries.sh drives ninja; this decides what the results mean and
what the run's verdict is. It is a script rather than a heredoc inside the
driver because the verdict is the part that has to be provably able to tell a
legitimate skip from an unchecked patch, and a classifier embedded in a shell
string can only be exercised by paying for a Chromium build. With it out here,
scripts/test_series_absences.py runs the real thing against fixtures.

Six statuses, and the exit code is 1 for `fails`, for `unexplained`, and for a
declared absence this run disproves. See scripts/series_absences.py for what
the absence statuses mean and scripts/series-absences.tsv for the declarations.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pathlib
import platform
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import series_absences  # noqa: E402  (path set above so the gate can run from anywhere)


def read_if_present(path):
    if not path:
        return ""
    try:
        return pathlib.Path(path).read_text(encoding="utf-8", errors="replace")
    except OSError:
        return ""


def parse_args(argv):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--units", required=True, help="path<TAB>patch,patch from series-translation-units.py")
    parser.add_argument("--map", required=True, help="path<TAB>objects for the series units")
    parser.add_argument("--report", required=True, help="where to write the JSON report")
    parser.add_argument("--target", required=True)
    parser.add_argument("--root", required=True, help="repository root")
    parser.add_argument("--absences", required=True, help="declared absences TSV")
    parser.add_argument("--mode", default="compile")
    parser.add_argument("--ninja-exit", type=int, default=0)
    parser.add_argument("--jobs", type=int, default=0)
    parser.add_argument("--probes", default="", help="path<TAB>ninja -t query evidence")
    parser.add_argument("--build-log", default="")
    parser.add_argument("--dry-log", default="")
    parser.add_argument("--includers", default="", help="unit<TAB>includer<TAB>line<TAB>operand<TAB>objects")
    parser.add_argument("--deps", default="", help="ninja -t deps output for the includer objects")
    parser.add_argument("--prefix", default="", help="out-dir-relative prefix ninja writes on source paths")
    return parser.parse_args(argv)


def main(argv):
    args = parse_args(argv)

    patches = {}
    order = []
    for line in pathlib.Path(args.units).read_text(encoding="utf-8").splitlines():
        path, _, names = line.partition("\t")
        if path:
            order.append(path)
            patches[path] = [n for n in names.split(",") if n]

    objects = {}
    for line in pathlib.Path(args.map).read_text(encoding="utf-8").splitlines():
        path, _, objs = line.partition("\t")
        if path.startswith("#"):
            continue
        objects[path] = objs.split()

    # What `ninja -t query` said about each absent path. It is evidence about
    # the graph node, not the account for the absence, so it rides alongside
    # the classification instead of standing in for one: "ninja has never heard
    # of this path" is equally true of a Windows-only file and of a file nobody
    # checked, which is the confusion this gate no longer makes.
    probes = {}
    for line in read_if_present(args.probes).splitlines():
        path, _, probe = line.partition("\t")
        if path:
            probes[path] = probe

    declarations = series_absences.load_declarations(args.absences)
    includers = series_absences.parse_includers(args.includers) if args.includers and os.path.exists(args.includers) else {}
    deps = series_absences.parse_ninja_deps(read_if_present(args.deps), args.prefix)

    log_lines = read_if_present(args.build_log).splitlines()

    # ninja prints "FAILED: <output> [<output>...]" and then the command and
    # the compiler's diagnostics. The outputs are ninja's own spelling of the
    # edge, so they map back to a translation unit exactly.
    failed_objects = set()
    excerpts = {}
    for index, line in enumerate(log_lines):
        if not line.startswith("FAILED: "):
            continue
        outs = line[len("FAILED: "):].split()
        failed_objects.update(outs)
        body = []
        for follow in log_lines[index + 1:]:
            if follow.startswith("FAILED: ") or follow.startswith("ninja: "):
                break
            if follow.startswith("[") and "] " in follow[:12]:
                break
            body.append(follow)
            if len(body) >= 40:
                break
        for out in outs:
            excerpts[out] = "\n".join(body).strip()

    # Anything ninja would still build is not up to date, so it was not
    # produced.
    dry_tokens = set(read_if_present(args.dry_log).split())

    units = []
    counts = {
        "compiles": 0,
        "fails": 0,
        series_absences.STATUS_INCLUDE_ONLY: 0,
        series_absences.STATUS_ABSENT_PLATFORM: 0,
        series_absences.STATUS_ABSENT_CONFIG: 0,
        series_absences.STATUS_UNEXPLAINED: 0,
        "unchecked": 0,
    }
    for path in order:
        objs = objects.get(path, [])
        unit = {"path": path, "patches": patches[path], "objects": objs}
        if not objs:
            unit.update(series_absences.classify_absent(
                path, args.target, includers.get(path, []), deps, declarations,
                compiled=(args.mode != "list"),
            ))
            if path in probes:
                unit["graph_probe"] = probes[path]
        elif args.mode == "list":
            unit["status"] = "unchecked"
            unit["reason"] = "membership only; --list did not compile"
        else:
            failed = [o for o in objs if o in failed_objects]
            residual = [o for o in objs if o in dry_tokens]
            if failed or residual:
                unit["status"] = "fails"
                unit["failed_objects"] = sorted(set(failed) | set(residual))
                if failed and not residual:
                    unit["reason"] = "ninja reported FAILED for an object that is now up to date; re-run the gate"
                elif residual and not failed:
                    unit["reason"] = "object not produced and no FAILED line names it; a prerequisite failed"
                else:
                    unit["reason"] = "compile failed"
                unit["error"] = "\n".join(excerpts.get(o, "") for o in failed).strip()
            else:
                unit["status"] = "compiles"
        counts[unit["status"]] = counts.get(unit["status"], 0) + 1
        units.append(unit)

    # The other direction of the declarations file. A row nothing needs is a row
    # asserting something untrue about a target it still matches, and leaving
    # those to accumulate is how an accounted-for absence list turns into a
    # blanket exemption.
    stale = series_absences.stale_declarations(
        declarations, {unit["path"]: unit["status"] for unit in units}, args.target,
    )

    series = pathlib.Path(args.root, "patches", "series").read_bytes()
    report = {
        "schema": "apostate-v1-series-gate/2",
        "target": args.target,
        "mode": args.mode,
        "host": f"{platform.system()}-{platform.machine()}",
        "chromium_version": pathlib.Path(args.root, "build", "CHROMIUM_VERSION").read_text(encoding="utf-8").strip(),
        "series_sha256": hashlib.sha256(series).hexdigest(),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "jobs": args.jobs,
        "ninja_exit": args.ninja_exit,
        "counts": counts,
        "stale_declarations": [
            {"path": d["path"], "targets": d["targets"], "line": d["line"], "why": why}
            for d, why in stale
        ],
        "units": units,
    }
    # Sorted keys and compact separators: the report is a generated artifact
    # that two jobs compare, so it is written byte-deterministically rather
    # than prettily.
    pathlib.Path(args.report).write_text(
        json.dumps(report, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8",
    )

    for unit in units:
        if unit["status"] == "fails":
            print(f"  FAIL      {unit['path']}")
            print(f"            {unit['reason']}")
            # The first line after "FAILED:" is the compile command, which for
            # Chromium is a few thousand characters. It stays whole in the
            # report so a failure can be reproduced by hand, and is cut here so
            # the diagnostics after it are the part a reader sees.
            for line in unit.get("error", "").splitlines()[:12]:
                print(f"            {line[:200]}")

    for unit in units:
        if unit["status"] == series_absences.STATUS_INCLUDE_ONLY:
            first = unit["included_by"][0]
            print(f"  verified  {unit['path']}")
            print(f"            include-only; {first['path']}:{first['line']} includes it "
                  f"and {first['object']} recorded reading it")

    for unit in units:
        if unit["status"] == series_absences.STATUS_UNEXPLAINED:
            print(f"  UNEXPLAINED {unit['path']}")
            print(f"            patches: {', '.join(unit['patches'])}")
            print(f"            {unit['reason']}")

    for declaration, why in stale:
        print(f"  STALE     {declaration['path']}")
        print(f"            {args.absences}:{declaration['line']} declares it absent on "
              f"{','.join(declaration['targets'])}, but {why}")

    print()
    print(f"==> V1 series gate  {args.target}  ({report['generated_at']})")
    print(f"  compiles              {counts['compiles']:4d}")
    print(f"  fails                 {counts['fails']:4d}")
    print(f"  include-only verified {counts[series_absences.STATUS_INCLUDE_ONLY]:4d}")
    print(f"  absent (platform)     {counts[series_absences.STATUS_ABSENT_PLATFORM]:4d}")
    print(f"  absent (config)       {counts[series_absences.STATUS_ABSENT_CONFIG]:4d}")
    print(f"  UNEXPLAINED           {counts[series_absences.STATUS_UNEXPLAINED]:4d}")
    if counts["unchecked"]:
        print(f"  unchecked (--list)    {counts['unchecked']:4d}")
    print(f"  total                 {len(units):4d}")
    print(f"  report                {args.report}")

    if counts[series_absences.STATUS_UNEXPLAINED]:
        print()
        print("A patched file this platform builds no object from, that nothing includes and")
        print("no declared absence accounts for, is an unchecked patch wearing a passing")
        print(f"badge. Account for it in {args.absences} with the GN condition that scopes")
        print("it, or put it in this platform's build graph.")
    if stale:
        print()
        print(f"{args.absences} claims an absence this run disproves; delete the row.")

    # A non-zero ninja exit with nothing attributed means the gate cannot say
    # what broke. That is a gate failure, not a pass.
    if args.ninja_exit != 0 and counts["fails"] == 0:
        print("\nninja exited non-zero but no translation unit was attributed a failure;")
        print("treat this run as inconclusive and read the log above.")
        return 1
    if counts["fails"] or counts[series_absences.STATUS_UNEXPLAINED] or stale:
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
