#!/usr/bin/env python3
"""V0 gate: a profile key is declared, read, or a stated exception — never inert.

Five times this project shipped an artifact that described a key nothing read.
`fonts.enumeration_allowlist` sat in the schema, was composed into every profile,
and was read by no C++ for its entire life. `fonts.provisioned_directory` was
read by no C++ at all. Patches 0097 and 0099 were committed and left out of
`patches/series`, so they were in the repository and in no build. And
`ledger/surfaces.jsonl`, the artifact that is supposed to derive the schema, named
its profile fields in a vocabulary of its own: 43 of its 61 distinct
`profile_field` values matched no declared key, so `ua.brand_version_list` stood
where the schema says `browser.brands` and nothing could tell the two apart from a
typo. That is very likely why the first two went unnoticed for years — with the
two vocabularies disjoint, no tool could reconcile them, and "the schema declares
a key the ledger never asked for" was unaskable. Each was found by hand, late,
after the documentation had been promising the feature for weeks. This is the
automated catch.

Four checks, all of one class — an artifact claiming something the build does
not do:

  1. DECLARED BUT NEVER READ  a key in config/profile.schema.json that no
                              sequenced patch reads. The page never sees it.
  2. READ BUT NOT DECLARED    a key C++ reads that the schema does not define.
                              A profile setting it fails validation, the user
                              cannot discover it, and --fingerprint-explain
                              cannot mention it.
  3. SERIES / DISK            every patches/*.patch appears in patches/series
                              and every series entry exists on disk. That file
                              is ordered by dependency rather than by number,
                              so ordering is deliberately not checked.
  4. NAMED BUT NOT DECLARED   a ledger row's profile_field that names no
                              declared key. The row points at a field no
                              profile can carry, so the surface it describes is
                              inherited from the host however the row reads.

    python3 scripts/check-schema-wiring.py            # report, exit 1 on a finding
    python3 scripts/check-schema-wiring.py --verbose  # plus every resolved read

Dependency-free, like the other V0 gates: it runs on a bare checkout with no
install step.


Why the consumed set comes from patches/, not from .workspace/src
-----------------------------------------------------------------

`.workspace/src` is gitignored, 36 GB of somebody else's code, and shared: it is
pristine while mapping is in flight and patched while patch work is. A gate
reading it would answer a different question on every machine, and would answer
"nothing is wired at all" on CI, which has no checkout. `patches/` plus
`patches/series` is in git, is the fork's actual source of truth, and encodes for
free the rule that already bit this project: code in an unsequenced patch is
code in no build, so it consumes nothing.

The cost is that a patch shows fragments rather than files, which is what the
hunk handling below is for.


Why accessor matching, and which accessors
------------------------------------------

There is no registry of profile fields to compare against. The loader is
hand-written C++, so the only evidence that a key is read is the code reading
it. base/apostate/profile.cc reads every field through base::Value::Dict, and it
is uniform about it:

    if (const DictValue* screen = dict->FindDict("screen")) {
      if (std::optional<int> v = screen->FindInt("avail_left")) {

So a read is `RECEIVER->Find*("LITERAL")`, or `.Find*`, or `contains`. A dotted
path is assembled by following the receiver back to the profile root; matching
literal dotted strings would find nothing at all, because no dotted path appears
anywhere in the source. Hence a small dataflow pass over four binding forms: a
dict/list Find that names a variable, a GetIf* that re-types one, a range-for
that iterates one, and FindByDottedPath, which carries its own dots.

Roots are derived, not listed. A receiver is a profile root when two or more
distinct top-level schema section names are read off it in the same file. That
is what makes `dict` in profile.cc and `profile` in compose.cc roots, and what
keeps the Vulkan ICD manifest dict in gpu/apostate_dawn_software_check.cc from
being mistaken for one.

Bindings are hunk-scoped first and file-scoped only as a fallback, because a
variable name is not unique inside a file — profile.cc binds `fonts` to both
`fonts` and `theme.system_fonts`. Where a fallback leaves several candidates,
the one that names a declared key wins, and a receiver that stays ambiguous is
reported rather than guessed at.

Tests are not consumers. `*_unittest.cc` and friends are excluded, because a key
read only by a unit test is a key the browser does not read, and letting a test
satisfy this check is exactly how `fonts.enumeration_allowlist` would have
passed it.

Two idioms read a key without writing its name in the Find call, and both are
resolved from evidence in the same file rather than waved through:

  * Wholesale iteration. `for (const auto [name, value] : *gl)` genuinely reads
    every key present under `gl_limits`. Where the schema names children
    explicitly instead of by additionalProperties, the loop's own validation
    decides which names survive, so each named child must also appear as a
    string literal in the consuming file. `fonts.generic_family_map.serif` is
    read because the loop rejects any generic it does not name.
  * A dynamic key. `media->FindInt(key)` over a literal table reads
    `media.audioinput_count` without ever spelling it inside the parentheses, so
    a declared child of a receiver with a dynamic lookup counts as read when its
    name appears as a literal in that file.

Anything this pass cannot resolve is reported as a finding, never dropped. An
unresolved receiver is the one failure mode that could hide the defect the check
exists to catch.


Why exceptions are annotations on the thing they excuse
-------------------------------------------------------

Some keys are legitimately consumed without a named lookup, and some reads are
legitimately outside the profile schema. Those need a way to say so, and the
obvious mechanism — a list of key names inside this script — is the defect
itself: a second artifact asserting something about code, sitting far from it,
going stale in silence. That is precisely how a schema field kept promising a
font-provisioning path no C++ read.

So an exception is carried next to the thing it excuses, and it is falsifiable:

  * A declared key nothing reads carries `x-wiring` on its own schema property,
    naming a reason, a sentence of why, and a repository path as evidence.
    Whoever edits the key sees it.
  * A read outside the schema carries `// wiring-exempt: <key> - <why>` in the
    patch, on the line of the read or just above it. Whoever edits the read
    sees it.
  * A ledger row whose surface has no declared key at all carries
    `profile_field_exception` beside its `profile_field`, with the same three
    parts. Whoever edits the row sees it.

All three fail when the exception goes stale: an `x-wiring` on a key that is now
read is an error, a `wiring-exempt` for a key that is now declared, or whose read
is gone, is an error, and a `profile_field_exception` on a row whose
`profile_field` now names a declared key is an error. A stale exception is the
same defect wearing the opposite sign.

Three more rules keep an annotation from being a bare assertion. Its `reason`
comes from a fixed vocabulary (see X_WIRING_REASONS and
PROFILE_FIELD_EXCEPTION_REASONS), so "why is this inert" has an answer rather
than a paragraph. Its `evidence` must name a file in this repository that
actually mentions the key — a path nobody can check is not evidence. And
`derived-upstream`, the one reason whose staleness no other check could see, is
verified against the dispersion tables: it claims the emitted value cannot
disagree with the profile's, which stops being true the moment a table offers
two values for the key.


How the ledger names a key, and why the spelling is not normalised
-----------------------------------------------------------------

`ledger/surfaces.jsonl` is where the schema is supposed to come from: the surface
schema says each `profile_field` becomes one property of
`config/profile.schema.json`. Check 4 is that sentence, enforced. A row's
`profile_field` is therefore spelled exactly as `load_declared()` spells a path —
dotted, `[]` for an array item, `*` for an additionalProperties child, so
`browser.brands[].brand` rather than `browser.brands.brand`. Nothing is
normalised on the way in, because a checker that quietly accepted both spellings
would be asserting an equivalence nobody had verified, which is the defect class
this file exists to catch.

Two shapes are allowed, and neither is a loophole. A string names one key. A
sorted array names several, for the surfaces that genuinely are served by more
than one — `screen.avail-dimensions` is served by the four work-area insets and
by the four absolute `avail_*` coordinates the loader derives from them, and
truncating that to one key would be a smaller lie than a wrong name but a lie all
the same. Every element is checked, so an array cannot smuggle an undeclared name
past the gate.

A row naming a section rather than a leaf (`battery`, `gl_limits`,
`media.devices`) passes, because an intermediate object is a declared path too
and a surface really can be the whole section. Consumption is deliberately not
required: `browser.brands` and `platform.wow64` are declared, correctly named,
and annotated `x-wiring` because no C++ reads them. Demanding a read here would
report a second time what check 1 has already explained.
"""

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "config" / "profile.schema.json"
PATCH_DIR = ROOT / "patches"
SERIES = PATCH_DIR / "series"
LEDGER = ROOT / "ledger" / "surfaces.jsonl"

CPP_SUFFIXES = (".cc", ".cpp", ".mm", ".h")
TEST_MARKERS = ("_unittest.", "_test.", "_browsertest.", "_fuzzer.")

# A schema object with additionalProperties carries free-form child names; the
# wildcard stands in for all of them, so that "the loader iterates this dict" is
# expressible as a consumed path.
WILDCARD = "*"

X_WIRING = "x-wiring"
X_WIRING_REASONS = {
    # Consumed, but by a path with no named lookup: merged wholesale into a
    # fragment, folded into another field, or stripped before emission.
    "consumed-indirectly",
    # Not a browser surface at all: provenance or bookkeeping, read by the
    # Python and Node launchers and deliberately never by C++.
    "not-a-browser-surface",
    # Stock Chromium already derives this value from a field that IS wired, so
    # a loader read could only agree with what the page is shown or contradict
    # it. The key records the reference measurement; it does not drive it.
    # Checked further below: the composed value must not vary.
    "derived-upstream",
    # The browser does not implement the claim. The key is accepted and inert,
    # and the evidence must be the page that says so, because a schema field
    # promising a behaviour no C++ has is the defect this gate exists to catch.
    "declared-not-implemented",
}

PROFILE_FIELD_EXCEPTION = "profile_field_exception"
PROFILE_FIELD_EXCEPTION_REASONS = {
    # A deployment or launch choice that travels beside the device profile
    # rather than inside it: the proxy endpoint on the command line, its
    # credentials in the launch envelope next to device_profile.
    "not-a-device-property",
    # Measured into the anchors for the V3 diff and deliberately left out of
    # the profile fragment, so there is nothing for a loader to read.
    "conformance-target-only",
    # The row is about the profile transport itself rather than one field, so
    # no single key can name it.
    "whole-profile",
    # A spoof surface the schema declares no key for. The profile cannot carry
    # the value at all, so the surface stays inherited from the host until a
    # key exists — which is a finding this gate surfaces rather than hides.
    "undeclared",
}

# The token half of an `evidence` reference has to be something a reader can
# search for. A dotted or dashed identifier is; the wildcard is not, so a row
# whose profile_field ends in one must spell its token out.
TOKEN_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.\-]*")

# Where a composed profile's field values come from. `derived-upstream` claims
# the emitted value cannot disagree with the profile's; that stops being true
# the moment a table offers two values for the key, so the claim is checked
# against the tables rather than taken on trust.
DISPERSION = ROOT / "resources" / "profiles" / "dispersion"

# `// wiring-exempt: proxy_credentials - kept out of the device-profile schema`
EXEMPT_RE = re.compile(
    r"//\s*wiring-exempt:\s*(?P<keys>[A-Za-z0-9_.,\[\]\s*]+?)\s*(?:-|\u2014)\s+(?P<why>\S.*)$"
)

# The accessor idiom. `contains` is a read too: it observes the key's presence.
ACCESSOR = r"Find(?:Dict|String|Int|Bool|Double|List)?(?:ByDottedPath)?|contains"
RECEIVER = r"[A-Za-z_][A-Za-z0-9_]*(?:\.[A-Za-z_][A-Za-z0-9_]*)*"
READ_RE = re.compile(
    rf"(?P<recv>{RECEIVER})\s*(?:->|\.)\s*(?P<acc>{ACCESSOR})\(\s*\"(?P<key>[^\"]*)\"\s*\)"
)
# The same call with a variable in place of the literal, which is how the media
# device counts are read.
DYNAMIC_RE = re.compile(
    rf"(?P<recv>{RECEIVER})\s*(?:->|\.)\s*(?P<acc>{ACCESSOR})"
    r"\(\s*(?P<arg>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
)
# Only a dict/list lookup yields something further keys can be read off.
NAVIGABLE = {"Find", "FindDict", "FindList", "FindByDottedPath"}
# `const DictValue* screen = ` immediately left of a read.
BIND_RE = re.compile(r"(?:\*|&)?\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*$")
# `entry = value.GetIfDict()` re-types a bound value without moving its path.
RETYPE_RE = re.compile(
    r"(?:\*|&)?\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*=\s*"
    r"(?P<src>[A-Za-z_][A-Za-z0-9_]*)\s*(?:->|\.)\s*GetIf\w+\(\s*\)"
)
# `for (const auto [k, v] : *dict)` before `for (const Value& v : *list)`: the
# destructuring form also matches the plain one on its second name.
FOR_PAIR_RE = re.compile(
    r"\bfor\s*\(\s*[^;:]*?\[\s*(?P<keyvar>[A-Za-z_][A-Za-z0-9_]*)\s*,"
    r"\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)\s*\]\s*:\s*\*?(?P<src>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
)
FOR_LIST_RE = re.compile(
    r"\bfor\s*\(\s*[^;:\[\]]*?(?:\*|&)?\s*(?P<var>[A-Za-z_][A-Za-z0-9_]*)"
    r"\s*:\s*\*?(?P<src>[A-Za-z_][A-Za-z0-9_]*)\s*\)"
)

FILE_RE = re.compile(r"^\+\+\+ b/(.+)$")
HUNK_RE = re.compile(r"^@@ -\d+(?:,\d+)? \+(\d+)(?:,\d+)? @@")


# ---------------------------------------------------------------------------
# The declared set
# ---------------------------------------------------------------------------


class Declared:
    __slots__ = ("path", "location", "kind", "required", "children", "named_children", "wiring")

    def __init__(self, path, location, kind, required, wiring):
        self.path = path
        self.location = location
        self.kind = kind  # "object" | "array" | "leaf"
        self.required = required
        self.children = []
        self.named_children = False
        self.wiring = wiring


def schema_kind(node):
    names = node.get("type")
    names = names if isinstance(names, list) else [names]
    if "object" in names or "properties" in node or "additionalProperties" in node:
        return "object"
    if "array" in names or "items" in node:
        return "array"
    return "leaf"


def walk_schema(node, prefix, location, required, out, root, seen_refs):
    """Collect every dotted path a profile may set."""
    if not isinstance(node, dict):
        return
    if "$ref" in node:
        ref = node["$ref"]
        if not ref.startswith("#/"):
            raise NotImplementedError(f"{location}: external $ref not supported: {ref}")
        if ref in seen_refs:
            return
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        walk_schema(target, prefix, location, required, out, root, seen_refs | {ref})
        return

    entry = out.get(prefix)
    if prefix and entry is None:
        entry = Declared(prefix, location, schema_kind(node), required, node.get(X_WIRING))
        out[prefix] = entry

    required_children = set(node.get("required", []))
    for name, child in node.get("properties", {}).items():
        path = f"{prefix}.{name}" if prefix else name
        walk_schema(child, path, f"{location}/properties/{name}", name in required_children,
                    out, root, seen_refs)
        if entry is not None:
            entry.children.append(path)
            entry.named_children = True

    extra = node.get("additionalProperties")
    if isinstance(extra, dict):
        path = f"{prefix}.{WILDCARD}" if prefix else WILDCARD
        walk_schema(extra, path, f"{location}/additionalProperties", False, out, root, seen_refs)
        if entry is not None:
            entry.children.append(path)

    items = node.get("items")
    if isinstance(items, dict):
        path = f"{prefix}[]"
        walk_schema(items, path, f"{location}/items", False, out, root, seen_refs)
        if entry is not None:
            entry.children.append(path)


def load_declared():
    schema = json.loads(SCHEMA.read_text(encoding="utf-8"))
    out = {}
    walk_schema(schema, "", "#", False, out, schema, frozenset())
    return out


def parent_of(path):
    """-> (parent_path, child_name); child_name is None for an array item."""
    if path.endswith("[]"):
        return path[:-2], None
    head, sep, name = path.rpartition(".")
    return (head, name) if sep else ("", path)


# ---------------------------------------------------------------------------
# The consumed set
# ---------------------------------------------------------------------------


def series_entries():
    entries, errors = [], []
    for lineno, raw in enumerate(SERIES.read_text(encoding="utf-8").splitlines(), 1):
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line in entries:
            errors.append(f"patches/series:{lineno}: {line} is listed twice")
            continue
        entries.append(line)
    return entries, errors


def parse_patch(text):
    """-> ([(path, [(lineno, text, added)])], {path: {removed_text: count}}).

    Context lines are kept. A child lookup added by one patch often sits under a
    parent binding another patch added, and that parent then appears only as
    context; dropping it would leave the receiver unresolvable.

    Removals are collected because the union of every hunk is not the tree. A
    read one patch adds and a later patch deletes is a read the build does not
    contain, and counting it would let this gate demand a schema key for code
    that is gone — the same stale claim in the opposite direction.
    """
    hunks, removed, path, current, lineno = [], {}, None, None, 0
    for raw in text.splitlines():
        header = FILE_RE.match(raw)
        if header:
            path, current = header.group(1), None
            continue
        hunk = HUNK_RE.match(raw)
        if hunk and path:
            lineno = int(hunk.group(1))
            current = []
            hunks.append((path, current))
            continue
        if current is None:
            continue
        if raw.startswith("+"):
            current.append((lineno, raw[1:], True))
            lineno += 1
        elif raw.startswith(" ") or raw == "":
            current.append((lineno, raw[1:] if raw else "", False))
            lineno += 1
        elif raw.startswith("-"):
            gone = removed.setdefault(path, {})
            gone[raw[1:]] = gone.get(raw[1:], 0) + 1
        else:
            current = None  # "diff --git", "\\ No newline at end of file", trailer
    return hunks, removed


def collect_hunks(entries, skip_tests=True):
    """-> ({path: [(patch, lines)]}, {path: {text: net_additions}})."""
    per_file, added, removed = {}, {}, {}
    for name in entries:
        patch = PATCH_DIR / name
        if not patch.is_file():
            continue
        hunks, gone = parse_patch(patch.read_text(encoding="utf-8", errors="replace"))
        for path, counts in gone.items():
            target = removed.setdefault(path, {})
            for text, count in counts.items():
                target[text] = target.get(text, 0) + count
        for path, lines in hunks:
            if not path.endswith(CPP_SUFFIXES):
                continue
            if skip_tests and any(marker in path for marker in TEST_MARKERS):
                continue
            per_file.setdefault(path, []).append((name, lines))
            counts = added.setdefault(path, {})
            for _, text, is_added in lines:
                if is_added:
                    counts[text] = counts.get(text, 0) + 1
    live = {
        path: {text: count - removed.get(path, {}).get(text, 0)
               for text, count in counts.items()}
        for path, counts in added.items()
    }
    return per_file, live



def find_roots(hunks, top_level):
    """A receiver is the profile root when it names two or more sections."""
    seen = {}
    for _, lines in hunks:
        for _, text, _ in lines:
            for match in READ_RE.finditer(text):
                if match.group("key") in top_level:
                    seen.setdefault(match.group("recv"), set()).add(match.group("key"))
    return {recv for recv, keys in seen.items() if len(keys) >= 2}


def join(parent, key):
    return f"{parent}.{key}" if parent else key


def close_bindings(lines, roots, fallback=None):
    """Variable -> candidate paths derived from this hunk's lines.

    `fallback` supplies bindings made in other hunks of the same file, so that a
    child lookup one patch added can chain off a parent another patch bound. It
    is read, never returned: keeping hunk-local bindings separate is what makes
    `fonts` resolve to `fonts` in the font-preferences hunk and to
    `theme.system_fonts` in the system-fonts one.
    """
    bindings = {}
    fallback = fallback or {}

    def paths_of(name):
        if name in roots:
            return {""}
        return set(bindings.get(name) or fallback.get(name) or ())

    for _ in range(4):  # root -> section -> item -> field is the deepest chain
        size = sum(len(v) for v in bindings.values())
        for _, text, _ in lines:
            for match in READ_RE.finditer(text):
                if match.group("acc") not in NAVIGABLE:
                    continue
                bind = BIND_RE.search(text[: match.start()])
                if not bind:
                    continue
                parents = paths_of(match.group("recv"))
                if parents:
                    bindings.setdefault(bind.group("var"), set()).update(
                        join(parent, match.group("key")) for parent in parents
                    )
            for match in RETYPE_RE.finditer(text):
                source = paths_of(match.group("src"))
                if source:
                    bindings.setdefault(match.group("var"), set()).update(source)
            spans = []
            for match in FOR_PAIR_RE.finditer(text):
                spans.append(match.span())
                source = paths_of(match.group("src"))
                if source:
                    bindings.setdefault(match.group("var"), set()).update(
                        join(path, WILDCARD) for path in source
                    )
            for match in FOR_LIST_RE.finditer(text):
                if any(start <= match.start() < end for start, end in spans):
                    continue
                source = paths_of(match.group("src"))
                if source:
                    bindings.setdefault(match.group("var"), set()).update(
                        f"{path}[]" for path in source
                    )
        if sum(len(v) for v in bindings.values()) == size:
            break
    return bindings


def iterated_paths(lines, resolve, live):
    """Paths a range-for reads wholesale, from this hunk."""
    out = set()
    for _, text, added in lines:
        if not (added and live.get(text, 0) > 0):
            continue
        spans = []
        for match in FOR_PAIR_RE.finditer(text):
            spans.append(match.span())
            out.update(resolve(match.group("src")))
        for match in FOR_LIST_RE.finditer(text):
            if any(start <= match.start() < end for start, end in spans):
                continue
            out.update(resolve(match.group("src")))
    return {path for path in out if path}


def consumed_paths(per_file, live_lines, declared):
    """-> (consumed, iterated, dynamic, unresolved, reading_files)"""
    top_level = {p for p in declared if "." not in p and "[" not in p}
    consumed, iterated, dynamic, unresolved, reading_files = {}, {}, {}, [], set()

    for path, hunks in sorted(per_file.items()):
        roots = find_roots(hunks, top_level)
        if not roots:
            continue
        reading_files.add(path)

        # A file-wide map, so a child lookup one patch added can resolve a
        # receiver only another patch's hunk bound. Built to a fixed point
        # because those chains cross hunks in both directions; hunk-local
        # bindings still win over it when a read is resolved.
        file_bindings = {}
        for _ in range(4):
            size = sum(len(v) for v in file_bindings.values())
            for _, lines in hunks:
                for var, paths in close_bindings(lines, roots, file_bindings).items():
                    file_bindings.setdefault(var, set()).update(paths)
            if sum(len(v) for v in file_bindings.values()) == size:
                break

        for patch, lines in hunks:
            local = close_bindings(lines, roots, file_bindings)
            # Evidence for an indirect read has to sit in the same hunk as the
            # read, not merely in the same file. A name that appears elsewhere
            # in the translation unit for an unrelated reason is not evidence
            # that a loop or a dynamic lookup consumed that key.
            live = live_lines.get(path, {})
            site = (path, patch, "\n".join(text for _, text, _ in lines))

            def resolve(recv, key):
                if recv in roots:
                    return {join("", key)}, None
                candidates = local.get(recv) or file_bindings.get(recv) or set()
                if not candidates:
                    return set(), "receiver is bound nowhere in this file"
                resolved = {join(parent, key) for parent in candidates}
                if len(resolved) == 1:
                    return resolved, None
                narrowed = {p for p in resolved if p in declared}
                if len(narrowed) == 1:
                    return narrowed, None
                return set(), (f"receiver resolves {len(resolved)} ways and "
                               f"{len(narrowed)} of them name a declared key")

            def all_paths(recv):
                if recv in roots:
                    return {""}
                return local.get(recv) or file_bindings.get(recv) or set()

            for lineno, text, added in lines:
                # Only a line the series actually leaves in the tree is a read.
                # A `+` line one patch adds and a later patch deletes is not in
                # the build, and context lines are another patch's `+` line
                # seen again, counted there.
                if not (added and live.get(text, 0) > 0):
                    continue
                for match in READ_RE.finditer(text):
                    resolved, problem = resolve(match.group("recv"), match.group("key"))
                    if problem:
                        unresolved.append((path, lineno, match.group("recv"),
                                           match.group("key"), patch, problem))
                        continue
                    for entry in resolved:
                        consumed.setdefault(entry, []).append((path, lineno, patch))
                for match in DYNAMIC_RE.finditer(text):
                    for entry in all_paths(match.group("recv")):
                        if entry in declared:
                            dynamic.setdefault(entry, []).append((lineno,) + site)

            for entry in iterated_paths(lines, all_paths, live):
                if entry in declared:
                    iterated.setdefault(entry, []).append((0,) + site)

    return consumed, iterated, dynamic, unresolved, reading_files


def is_consumed(path, declared, consumed, iterated, dynamic):
    """Is this declared path read by some sequenced patch?"""
    if path in consumed:
        return True
    parent, name = parent_of(path)

    # An array item or an additionalProperties child is read when its parent is
    # iterated wholesale; there is no name for a Find call to carry.
    if name is None or name == WILDCARD:
        return parent in iterated or parent in dynamic

    sites = iterated.get(parent, []) + dynamic.get(parent, [])
    if not sites:
        return False
    owner = declared.get(parent)
    if owner is not None and not owner.named_children:
        return True
    # The schema names this child explicitly, so the consuming loop's own
    # validation decides whether the name survives. It has to appear there.
    return any(f'"{name}"' in text for _, _, _, text in sites)


def shallowest(paths):
    """Keep only the highest path of each unread subtree."""
    out = []
    for path in paths:
        parent = path
        while True:
            parent, _ = parent_of(parent)
            if not parent:
                out.append(path)
                break
            if parent in paths:
                break
    return out


# ---------------------------------------------------------------------------
# Exceptions carried in the patches
# ---------------------------------------------------------------------------


def collect_exempt(entries):
    """-> {key: [(patch, path, lineno, why)]} from `// wiring-exempt:` markers."""
    out = {}
    for name in entries:
        patch = PATCH_DIR / name
        if not patch.is_file():
            continue
        hunks, _ = parse_patch(patch.read_text(encoding="utf-8", errors="replace"))
        for path, lines in hunks:
            for lineno, text, added in lines:
                if not added:
                    continue  # another patch's marker, counted where it was added
                match = EXEMPT_RE.search(text)
                if not match:
                    continue
                why = match.group("why").strip()
                for key in match.group("keys").split(","):
                    key = key.strip()
                    if key:
                        out.setdefault(key, []).append((name, path, lineno, why))
    return out


def composed_values(path):
    """Every distinct value the dispersion tables compose for a dotted key."""
    parts = path.split(".")
    found = []

    def dig(node, depth):
        if depth == len(parts):
            found.append(json.dumps(node, sort_keys=True))
            return
        if isinstance(node, dict) and parts[depth] in node:
            dig(node[parts[depth]], depth + 1)

    for table in sorted(DISPERSION.glob("*.json")):
        axis = json.loads(table.read_text(encoding="utf-8"))
        for option_set in axis.get("option_sets", []):
            for option in option_set.get("options", []):
                dig(option.get("value", {}), 0)
    return sorted(set(found))


def wiring_problem(key, wiring):
    """Is this x-wiring annotation usable as evidence, and still true?"""
    if not isinstance(wiring, dict):
        return f"{X_WIRING} must be an object carrying reason, why and evidence"
    missing = [f for f in ("reason", "why", "evidence") if not wiring.get(f)]
    if missing:
        return f"{X_WIRING} is missing {', '.join(missing)}"
    if wiring["reason"] not in X_WIRING_REASONS:
        return (f"{X_WIRING}.reason is {wiring['reason']!r}; expected one of "
                f"{sorted(X_WIRING_REASONS)}")

    # `path` or `path:token`. The file must exist and must mention the thing
    # being excused, so that the annotation points at something a reader can
    # check rather than merely asserting a conclusion.
    evidence, _, token = str(wiring["evidence"]).partition(":")
    target = ROOT / evidence
    if not target.is_file():
        return f"{X_WIRING}.evidence names {evidence}, which is not a file in this repository"
    token = token or parent_of(key)[1] or key
    if token not in target.read_text(encoding="utf-8", errors="replace"):
        return (f"{X_WIRING}.evidence is {evidence}, which does not mention {token!r}: "
                f"it cannot be the evidence for this key")

    if wiring["reason"] == "derived-upstream":
        values = composed_values(key)
        if len(values) > 1:
            return (f"{X_WIRING}.reason is derived-upstream, but the dispersion tables now "
                    f"compose {len(values)} different values for it ({', '.join(values[:4])}). "
                    f"A value that varies is a value the profile is trying to choose, so it "
                    f"has to be read.")
    return None


# ---------------------------------------------------------------------------
# The named set: what ledger/surfaces.jsonl says supplies each surface
# ---------------------------------------------------------------------------


def ledger_rows():
    """-> ([(lineno, id, [name], exception)], errors).

    Read line by line rather than through validate-ledger.py: this gate stays
    dependency-free and must still say something useful about a file that gate
    would reject outright.
    """
    rows, errors = [], []
    if not LEDGER.is_file():
        return rows, [f"{LEDGER.relative_to(ROOT)} is missing, so check 4 cannot run"]
    for lineno, line in enumerate(LEDGER.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            errors.append(f"ledger/surfaces.jsonl:{lineno} is not valid JSON: {exc}")
            continue
        field = row.get("profile_field")
        if field is None:
            names = []
        elif isinstance(field, str):
            names = [field]
        elif isinstance(field, list) and all(isinstance(n, str) for n in field):
            names = list(field)
        else:
            errors.append(
                f"ledger/surfaces.jsonl:{lineno} profile_field must be a string, a list of "
                f"strings, or null"
            )
            continue
        rows.append((lineno, row.get("id", "?"), names, row.get(PROFILE_FIELD_EXCEPTION)))
    return rows, errors


def profile_field_problem(names, exception):
    """Is this profile_field_exception usable as evidence, and still needed?"""
    if not isinstance(exception, dict):
        return f"{PROFILE_FIELD_EXCEPTION} must be an object carrying reason, why and evidence"
    missing = [f for f in ("reason", "why", "evidence") if not exception.get(f)]
    if missing:
        return f"{PROFILE_FIELD_EXCEPTION} is missing {', '.join(missing)}"
    if exception["reason"] not in PROFILE_FIELD_EXCEPTION_REASONS:
        return (f"{PROFILE_FIELD_EXCEPTION}.reason is {exception['reason']!r}; expected one of "
                f"{sorted(PROFILE_FIELD_EXCEPTION_REASONS)}")
    if not names:
        return (f"{PROFILE_FIELD_EXCEPTION} sits on a row whose profile_field is null, so "
                f"there is no name for it to excuse")

    # `path` or `path:token`, the same shape x-wiring uses, so that the
    # annotation points at something a reader can check.
    evidence, _, token = str(exception["evidence"]).partition(":")
    target = ROOT / evidence
    if not target.is_file():
        return (f"{PROFILE_FIELD_EXCEPTION}.evidence names {evidence}, which is not a file "
                f"in this repository")
    token = token or parent_of(names[-1])[1] or names[-1]
    if not TOKEN_RE.fullmatch(token):
        return (f"{PROFILE_FIELD_EXCEPTION}.evidence has to spell its token as 'path:token'; "
                f"{token!r} is not something a reader can search for")
    if token not in target.read_text(encoding="utf-8", errors="replace"):
        return (f"{PROFILE_FIELD_EXCEPTION}.evidence is {evidence}, which does not mention "
                f"{token!r}: it cannot be the evidence for this row")
    return None


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def check_series(entries, series_errors):
    findings = list(series_errors)
    on_disk = sorted(p.name for p in PATCH_DIR.glob("*.patch"))
    listed = set(entries)
    for name in on_disk:
        if name not in listed:
            findings.append(
                f"patches/{name} is on disk and not in patches/series: it is in the "
                f"repository and in no build"
            )
    for name in entries:
        if not (PATCH_DIR / name).is_file():
            findings.append(f"patches/series names {name}, which is not on disk")
    return findings, on_disk


def main() -> int:
    verbose = "--verbose" in sys.argv[1:]
    findings = []

    entries, series_errors = series_entries()
    series_findings, on_disk = check_series(entries, series_errors)
    print("patch series")
    for finding in series_findings:
        print(f"  FAIL {finding}")
    if not series_findings:
        print(f"  ok   {len(entries)} sequenced entr(ies), {len(on_disk)} patch file(s) on "
              f"disk, both directions agree")
    findings.extend(series_findings)

    declared = load_declared()
    per_file, live_lines = collect_hunks(entries)
    consumed, iterated, dynamic, unresolved, reading_files = consumed_paths(
        per_file, live_lines, declared)
    exempt = collect_exempt(entries)

    print("\nprofile keys")
    print(f"  {len(declared)} declared in config/profile.schema.json; "
          f"{len(consumed)} read by name in {len(reading_files)} sequenced non-test "
          f"translation unit(s): {', '.join(sorted(reading_files))}")

    unread = {
        path for path in declared
        if not is_consumed(path, declared, consumed, iterated, dynamic)
    }
    # An object whose children are read is wired through them; the parent lookup
    # is how they were reached.
    unread = {
        path for path in unread
        if not any(child not in unread for child in declared[path].children)
    }

    declared_findings = []
    for path in sorted(shallowest(unread)):
        entry = declared[path]
        if entry.wiring is None:
            hidden = sum(1 for other in unread
                         if other.startswith(f"{path}.") or other.startswith(f"{path}["))
            extra = f" (and {hidden} key(s) below it)" if hidden else ""
            declared_findings.append(
                f"DECLARED BUT NEVER READ  {path}{extra}\n"
                f"        schema {entry.location}"
                + ("  (the schema marks it required)" if entry.required else "") + "\n"
                f"        consuming C++: none. No sequenced patch reads this key, so a "
                f"profile that sets it changes nothing a page can see."
            )
            continue
        problem = wiring_problem(path, entry.wiring)
        if problem:
            declared_findings.append(
                f"BAD EXCEPTION            {path}\n        schema {entry.location}: {problem}"
            )
        elif verbose:
            print(f"  note {path}: exempt ({entry.wiring['reason']}) — {entry.wiring['why']}")

    for path, entry in sorted(declared.items()):
        if entry.wiring is None or path in unread:
            continue
        where = consumed.get(path) or iterated.get(path) or dynamic.get(path) or []
        site = f"{where[0][0]}:{where[0][1]}" if where else "a read this pass resolved"
        declared_findings.append(
            f"STALE EXCEPTION          {path}\n"
            f"        schema {entry.location} carries {X_WIRING}, but the key is read at "
            f"{site}.\n"
            f"        The exception outlived the problem it described; delete it."
        )

    for finding in declared_findings:
        print(f"  FAIL {finding}")
    findings.extend(declared_findings)

    undeclared_findings = []
    for path, sites in sorted(consumed.items()):
        if path in declared:
            continue
        parent, _ = parent_of(path)
        if parent and parent not in declared:
            continue  # report the top of a divergence, not every leaf under it
        excuse = exempt.get(path)
        if excuse:
            if verbose:
                print(f"  note {path}: wiring-exempt in {excuse[0][0]} — {excuse[0][3]}")
            continue
        where = ", ".join(f"{f}:{n}" for f, n, _ in sites[:3])
        undeclared_findings.append(
            f"READ BUT NOT DECLARED    {path}\n"
            f"        consuming C++: {where}  (patch {sites[0][2]})\n"
            f"        schema: absent. A profile setting it fails validation, and nothing "
            f"documents it."
        )

    for key, sites in sorted(exempt.items()):
        if key in declared:
            undeclared_findings.append(
                f"STALE EXCEPTION          {key}\n"
                f"        {sites[0][0]} marks it wiring-exempt, but config/profile.schema.json "
                f"now declares it at {declared[key].location}."
            )
        elif key not in consumed:
            undeclared_findings.append(
                f"STALE EXCEPTION          {key}\n"
                f"        {sites[0][0]} marks it wiring-exempt, and no sequenced patch reads "
                f"it any more."
            )

    for finding in undeclared_findings:
        print(f"  FAIL {finding}")
    findings.extend(undeclared_findings)

    for path, lineno, recv, key, patch, problem in unresolved:
        finding = (
            f'UNRESOLVED RECEIVER      {recv}->...("{key}")\n'
            f"        {path}:{lineno}  (patch {patch})\n"
            f"        {problem}, so this pass cannot say which profile key that read names "
            f"or whether it is wired. Bind the receiver in the same hunk, or teach this "
            f"script the idiom."
        )
        print(f"  FAIL {finding}")
        findings.append(finding)

    if not declared_findings and not undeclared_findings and not unresolved:
        print("  ok   every declared key is read or carries a stated exception")
        print("  ok   every read names a declared key or carries a stated exception")

    rows, ledger_findings = ledger_rows()
    named = {name for _, _, names, _ in rows for name in names}
    print("\nledger profile fields")
    print(f"  {len(rows)} row(s) in ledger/surfaces.jsonl name {len(named)} distinct "
          f"profile field(s)")

    for lineno, surface, names, exception in rows:
        absent = [name for name in names if name not in declared]
        if exception is None:
            if absent:
                ledger_findings.append(
                    f"NAMED BUT NOT DECLARED   {', '.join(absent)}\n"
                    f"        ledger/surfaces.jsonl:{lineno}  ({surface})\n"
                    f"        schema: absent. The row points at a field no profile can carry, "
                    f"so the surface stays inherited from the host however the row reads."
                )
            continue
        if names and not absent:
            ledger_findings.append(
                f"STALE EXCEPTION          {', '.join(names)}\n"
                f"        ledger/surfaces.jsonl:{lineno} ({surface}) carries "
                f"{PROFILE_FIELD_EXCEPTION}, but config/profile.schema.json declares every "
                f"name it gives.\n"
                f"        The exception outlived the problem it described; delete it."
            )
            continue
        problem = profile_field_problem(names, exception)
        if problem:
            ledger_findings.append(
                f"BAD EXCEPTION            {', '.join(names) or '(null)'}\n"
                f"        ledger/surfaces.jsonl:{lineno} ({surface}): {problem}"
            )
        elif verbose:
            print(f"  note {surface}: {', '.join(names)} exempt "
                  f"({exception['reason']}) — {exception['why']}")

    for finding in ledger_findings:
        print(f"  FAIL {finding}")
    findings.extend(ledger_findings)
    if not ledger_findings:
        print("  ok   every profile_field names a declared key or carries a stated exception")

    if verbose:
        print("\nresolved reads")
        for path in sorted(consumed):
            sites = ", ".join(f"{f}:{n}" for f, n, _ in consumed[path])
            print(f"  {path:48} {sites}")
        for path in sorted(iterated):
            lineno, where, patch, _ = iterated[path][0]
            print(f"  {path:48} iterated wholesale in {where}")
        for path in sorted(dynamic):
            lineno, where, patch, _ = dynamic[path][0]
            print(f"  {path:48} dynamic key at {where}:{lineno}")

    print(f"\n{len(declared)} declared key(s), {len(entries)} sequenced patch(es), "
          f"{len(rows)} ledger row(s), {len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
