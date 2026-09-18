#!/usr/bin/env python3
"""V0 gate: a profile key is declared, read, or a stated exception — never inert.

Seven times this project shipped an artifact that described something the build
does not do. `fonts.enumeration_allowlist` sat in the schema, was composed into
every profile, and was read by no C++ for its entire life.
`fonts.provisioned_directory` was read by no C++ at all. Patches 0097 and 0099
were committed and left out of `patches/series`, so they were in the repository
and in no build. `ledger/surfaces.jsonl`, the artifact that is supposed to
derive the schema, named its profile fields in a vocabulary of its own: 43 of
its 61 distinct `profile_field` values matched no declared key, so
`ua.brand_version_list` stood where the schema says `browser.brands` and nothing
could tell the two apart from a typo. That is very likely why the first two went
unnoticed for years — with the two vocabularies disjoint, no tool could
reconcile them, and "the schema declares a key the ledger never asked for" was
unaskable. `keyboard.layout_map` was the same defect in the remaining direction:
declared, read wholesale by base/apostate/profile.cc, served by patch 0021, and
named by no ledger row at all, so a fully wired surface carried no verification
tier, no evidence and no coherence edge. And 110 of the 111 coherence edges
carried a `check` expression that nothing evaluates, while the enforcement that
does run is hand-written Python in scripts/profile_resolver.py with no link back
to the edge it enforces. And four ledger rows cited a Chromium source path that
is not in the pinned checkout — `base/ieee754.cc` for V8's transcendentals,
which are at `v8/src/base/ieee754.cc`, `skia/font_cache_skia.cc` for a Blink
file thirty characters deeper, `ui/display/screen_ozone.cc` for a file in
`ui/aura`, and a directory that does not exist at all. The whole method rests on
a third party being able to open the evidence, so a path that cannot be opened
is not weaker evidence but none. Each was found by hand, late, after the
documentation had been promising the feature for weeks. This is the automated
catch.

Seven checks, all of one class — an artifact claiming something the build does
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
  5. ENFORCED BY NOTHING      a coherence edge whose `enforced_by` names a site
                              that does not exist: a resolver function absent,
                              uncalled or no longer marked for that edge, a
                              patch not in patches/series, or a V3 probe the
                              collector does not register. An edge enforced by
                              nothing at all is COUNTED, not failed.
  6. DECLARED BUT UNNAMED     a declared key no ledger row names. The schema is
                              supposed to be derived FROM the ledger, so a key
                              no row asked for is a surface with no verification
                              tier, no evidence and no coherence edge.
  7. CITED PATH IS ABSENT     a Chromium source path this repository cites that
                              .workspace/src does not have, no sequenced patch
                              creates, and no unsynced DEPS checkout could
                              explain. Nobody can open the evidence. Read from
                              ledger/*.jsonl, scripts/*.tsv, docs/**/*.md code
                              spans, and patches/*.patch header prose -- never
                              from a diff body. Its sibling finding, CITED AS
                              UPSTREAM, is a path that resolves only because
                              the series is applied.

    python3 scripts/check-schema-wiring.py            # report, exit 1 on a finding
    python3 scripts/check-schema-wiring.py --verbose  # plus every resolved read

Dependency-free, like the other V0 gates: it runs on a bare checkout with no
install step. Checks 1 to 6 read nothing outside this repository. Check 7 reads
the pinned checkout and shells out to git once per repository it touches, and
degrades to a printed SKIP rather than a silent pass when either is absent.


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


The other direction: a declared key no row names
------------------------------------------------

Check 4 walks ledger to schema and can only see keys some row names. A key no
row names is invisible to it, and that is the direction `keyboard.layout_map`
escaped through: declared, iterated wholesale by the loader, served by a
sequenced patch, and described by no row. Check 6 walks the same relation the
other way, so the sentence "each profile_field becomes one property of the
schema" is enforced from both ends and the pair cannot drift.

A key counts as named when a row names it, an ancestor of it, or a descendant of
it. All three are one relation seen from different depths: a row naming
`battery` describes `battery.level` too, and a row naming `screen.avail_top`
could not describe it without `screen`. Only the shallowest uncovered path of a
subtree is reported, as in check 1, because a section nobody asked for is one
finding and not thirty.

Being read by C++ deliberately does NOT satisfy this check, and that is the
whole point rather than an oversight. `keyboard.layout_map` is read — patch 0021
iterates it and serves it to `navigator.keyboard.getLayoutMap()` — so a check
that accepted a read would have passed the exact defect it exists to catch.
Consumption is what check 1 measures; this check measures whether anything
recorded the surface, its verification tier, its evidence and its coherence
edges. A key can be perfectly wired and completely unaccounted for, which is the
state that lets a surface ship unmeasured.

The exception is an `x-ledger` block on the key's own schema property, the same
three parts as `x-wiring` and checked the same way, with the same staleness rule:
an `x-ledger` on a key some row now names is an error. Two reasons, because there
are two honest ways for a declared key to have no row of its own:
`not-a-surface` for loader and resolver bookkeeping no detector can read, and
`row-names-no-field` for a key whose surface DOES have a row that legitimately
names no `profile_field` — an `inherit` or `escalate` row. That second one is
verified rather than asserted: the evidence token must be a row id present in
`ledger/surfaces.jsonl`, and that row's verdict must still be one that names no
field, so flipping it to `spoof` without naming the key fails here.


Why check 5 verifies enforcement sites and not `check` expressions
------------------------------------------------------------------

`ledger/coherence.jsonl` carries a `check` string on 110 of its 111 edges, and
the schema calls it an executable predicate over a collected fingerprint. It is
not executed. Only `scripts/validate-ledger.py` and `scripts/merge-inbox.py`
read that file, neither of them reads `check`, and the coherence enforcement that
really runs is `_coherence_check` in `scripts/profile_resolver.py` — about thirty
hand-written conditions that never consult the ledger. Asserting that the
expressions resolve would verify the spelling of prose nothing evaluates, which
is the defect class this file exists to catch rather than a check against it.

So each edge carries `enforced_by`, an array naming the sites that really
enforce it, and this check resolves every site against the tree:

  * `resolver` — `scripts/profile_resolver.py:<function>`. The function must
    exist, must be called somewhere in that file (a dead function enforces
    nothing), and the file must carry a `# coh: <edge id>` marker, because six
    edges name the same function and deleting one of its conditions would
    otherwise leave all six still resolving. The marker is checked in reverse
    too: a `# coh: X` for an unknown edge, or for an edge that does not claim a
    resolver, is stale and fails.
  * `patch` — a bare `patches/series` entry, which must be sequenced and on
    disk. Same rule as check 3: a patch outside the series is in no build.
  * `v3-probe` — `capture/collector/collector.js:<probe id>`, which must be a
    probe the collector actually registers.

`enforced_by: []` is the explicit "nothing enforces this yet" form. It is
counted and printed every run and never failed, because a stated gap is
legitimate and most of these edges are in it; what is not legitimate is the gap
having no number attached, which is how 110 unevaluated predicates went
unnoticed. The field is REQUIRED on every row for the same reason — an edge that
could omit it would vanish from the count instead of appearing in it.

`undeclared_dependency` is the third direction of the `x-wiring` idea, carried on
a coherence row: the invariant legitimately names a profile key the schema does
not declare. Its key must still fail to resolve, so an annotation whose key has
since been declared is stale and fails.


Why check 7 reads .workspace/src, and the four ways a path is allowed to be
absent from it
---------------------------------------------------------------------------

This is the one check that must read the pinned checkout, because the claim it
verifies is about that checkout: every `source` in the ledger is a promise that
somebody else can open the file and see the code the row describes. The header
above explains why a gate reading `.workspace/src` is a hazard, and the answer
here is not to avoid the read but to make the verdict independent of the state
the read finds the tree in. Four absences are legitimate, and each is resolved
from something in git rather than from the tree:

  * OURS. A path that resolves in this repository is not a Chromium citation at
    all. `build/args/common.gni` is the fork's own GN args file, and the first
    hand audit of these citations called it a broken Chromium path because it
    looked for it only under `.workspace/src`.
  * FORK-OWNED. `base/apostate/profile.cc` exists in the checkout only while
    `patches/series` is applied. Resolving it against the tree would pass on a
    patched checkout and report a broken citation on a pristine one, which is a
    verdict that depends on state unrelated to the claim — worse than no check,
    because it teaches everyone to ignore the output. So it resolves against
    `patches/`, from the `--- /dev/null` hunks that show what the fork creates.
    Do not "simplify" this into a tree lookup.
  * UNSYNCED. A DEPS sub-repository nobody synced is an empty directory, and
    every path inside it is absent for a reason that has nothing to do with the
    citation. This project has already once asserted a file did not exist on the
    strength of a grep over one of those. Counted and printed, never failed.
  * BUILD-GENERATED. A path under `out/` is emitted by a GN action and exists
    only for the targets a given machine built, so this check never counts it as
    present even when it happens to be on disk. The row carries
    `generated_path_exception` naming the GENERATOR INPUT, which is source and
    can be opened, and it is checked the way the other annotations are: the
    input must exist and must mention the token, an exception whose path now
    resolves is stale, and so is one whose row no longer cites the path.

The inverse of the fork-owned rule is a finding of its own. A path present on
disk, absent from the pinned revision, and created by no sequenced patch is
being cited as upstream evidence while resolving only because this checkout was
mutated — so `git ls-tree HEAD` separates "on disk" from "at the pinned
revision", batched one call per repository, read-only, and skipped with a note
if git cannot answer. Eight cited paths are fork-owned today and every one of
them is created by a sequenced patch.

Extraction is the other half, and it is where the first audit went wrong: seven
of its eleven findings were its own bugs. A googlesource permalink ends in
something shaped exactly like a repository path, and an extension alternation
ordered `gn|gni` truncates `build.gni` to `build.gn` and
`runtime_enabled_features.json5` to `.json`, inventing two files to report as
missing. A gate that cries wolf at that rate gets ignored, which is worse than
not running it, so the URL exclusion, the longest-first end-anchored extension
match and the "first segment names an entry at the checkout root" test are all
three present and all three load-bearing.
"""

import json
import pathlib
import re
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA = ROOT / "config" / "profile.schema.json"
PATCH_DIR = ROOT / "patches"
SERIES = PATCH_DIR / "series"
LEDGER = ROOT / "ledger" / "surfaces.jsonl"
COHERENCE = ROOT / "ledger" / "coherence.jsonl"
RESOLVER = ROOT / "scripts" / "profile_resolver.py"
COLLECTOR = ROOT / "capture" / "collector" / "collector.js"
# The single-binary coherence gate, instrument for the "coherence-gate" kind.
GATE = ROOT / "capture" / "derive" / "coherence.py"

# Check 7 reads the pinned mapping checkout, and reads nothing else from it.
WORKSPACE = ROOT / ".workspace" / "src"
LEDGER_DIR = ROOT / "ledger"
# Check 7's other three citation sources. Declarations, docs and patch headers
# carry paths a reader is expected to open, and until now nothing resolved them.
SCRIPTS_DIR = ROOT / "scripts"
DOCS_DIR = ROOT / "docs"
# Chromium's build directory. Everything under it is generated, and which of it
# exists depends on what this machine has built, so it is never source.
BUILD_ROOT = "out"

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

X_LEDGER = "x-ledger"
X_LEDGER_REASONS = {
    # Loader or resolver bookkeeping: the key identifies or annotates the
    # profile itself, so no detector can read it and no surface row can exist.
    # Being read by C++ does not make a key a surface.
    "not-a-surface",
    # The surface DOES have a row, and that row's verdict legitimately names no
    # profile_field — an inherit or escalate row. Verified rather than asserted:
    # the evidence token must be a row id, and that row must still carry a
    # verdict that names no field.
    "row-names-no-field",
}

# Coherence edges: what really enforces each one. `check` is prose nothing
# evaluates (see the header), so the field this gate resolves is the site.
ENFORCED_BY = "enforced_by"
ENFORCEMENT_KINDS = ("resolver", "patch", "v3-probe", "coherence-gate")
UNBUILT_DEPENDENCY = "unbuilt_dependency"
UNBUILT_DEPENDENCY_REASONS = {
    # The invariant names a profile key config/profile.schema.json does not
    # declare, so no profile can carry the value the edge is about.
    "undeclared",
    # The key is declared, but no dispersion table varies it, so every composed
    # profile carries the same value and the edge has nothing to compare.
    "unconditioned",
}

# A ledger row citing a path the build generates. The path cannot be in source,
# so what the annotation has to produce is the GENERATOR INPUT, which can be.
GENERATED_PATH_EXCEPTION = "generated_path_exception"
GENERATED_PATH_REASONS = {
    # A GN action or template emits the file into the build directory, so it
    # lives only under out/ and only for targets this machine has built.
    "build-generated",
}

# Extensions a ledger citation actually carries, plus the obvious neighbours.
# Sorted longest-first below, because an alternation ordered `gn|gni` matches
# `build.gni` as `build.gn` and `runtime_enabled_features.json5` as `.json`, and
# then reports a file nobody cited as missing. Seven of the eleven findings in
# the first hand audit of these citations were that bug and the URL one below.
CITED_SUFFIXES = (
    "asm", "cc", "cfg", "chromium", "cpp", "css", "dat", "filelist", "gn", "gni",
    "grd", "gyp", "gypi", "h", "hpp", "html", "idl", "inc", "java", "js", "json",
    "json5", "md", "mjs", "mm", "mojom", "patch", "proto", "py", "pyl", "rs", "S",
    "sh", "ts", "txt", "typemap", "xml",
)
CITED_PATH_RE = re.compile(
    r"(?<![\w/.+-])((?:[A-Za-z0-9_.+-]+/)+[A-Za-z0-9_.+-]+\."
    + "(?:" + "|".join(sorted(CITED_SUFFIXES, key=lambda s: (-len(s), s))) + ")"
    + r")(?![A-Za-z0-9_])"
)
# `https://chromium.googlesource.com/angle/angle/+/<sha>/src/libANGLE/Shader.cpp`
# ends in something shaped exactly like a repository path. Both the URL span and
# the `/+/` infix are excluded: a permalink may appear without its scheme, and a
# scheme may precede a path that has no `/+/` in it.
URL_RE = re.compile(r"[A-Za-z][A-Za-z0-9+.-]*://\S+")
# `  'src/third_party/angle': {` in the pinned DEPS.
DEPS_KEY_RE = re.compile(r"\s{2}'src/(?P<path>[^']+)'\s*:")
# A patch hunk that creates a file, which is how the fork declares what it owns.
PATCH_ADDS_RE = re.compile(r"^--- /dev/null\n\+\+\+ b/(.+)$", re.M)
# Markdown code, which is where this repository's docs put a path a reader is
# meant to open. Both fence styles, and inline spans matched non-greedily so
# two spans on one line stay two.
FENCE_RE = re.compile(r"^\s*(?:```|~~~)")
INLINE_CODE_RE = re.compile(r"`([^`]+)`")

# `probe("keyboard.layout", { deterministic: true }, ...)` in the collector.
PROBE_RE = re.compile(r"\bprobe\(\s*\"([^\"]+)\"")
# `# coh: coh.screen-avail-inset - a work area no display could have is rejected`
# beside the condition that enforces it, in the shape `// wiring-exempt:` uses.
# Matched anywhere in a comment so the marker may sit above the condition or on
# it, and the code half of the line is what the dead-function guard reads.
COH_MARKER_RE = re.compile(r"#\s*coh:\s*(?P<id>[A-Za-z0-9][A-Za-z0-9_.\-]*)")
# `scripts/profile_resolver.py:_coherence_check` -> the function half.
RESOLVER_SITE_RE = re.compile(r"^scripts/profile_resolver\.py:(?P<fn>[A-Za-z_][A-Za-z0-9_]*)$")
# `capture/collector/collector.js:keyboard.layout` -> the probe id half.
PROBE_SITE_RE = re.compile(r"^capture/collector/collector\.js:(?P<probe>\S+)$")
# `capture/derive/coherence.py:coh.window-within-avail-rect` -> the edge id half.
GATE_SITE_RE = re.compile(
    r"^capture/derive/coherence\.py:(?P<edge>coh\.[a-z0-9]+(?:[.-][a-z0-9]+)*)$")
# `check("coh.window-within-avail-rect", ...)` in the gate, same idiom as PROBE_RE.
GATE_CHECK_RE = re.compile(r"\bcheck\(\s*\"([^\"]+)\"")

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
    __slots__ = ("path", "location", "kind", "required", "children", "named_children",
                 "wiring", "ledger")

    def __init__(self, path, location, kind, required, wiring, ledger):
        self.path = path
        self.location = location
        self.kind = kind  # "object" | "array" | "leaf"
        self.required = required
        self.children = []
        self.named_children = False
        self.wiring = wiring   # why no C++ reads it (check 1)
        self.ledger = ledger   # why no ledger row names it (check 6)


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
        entry = Declared(prefix, location, schema_kind(node), required,
                         node.get(X_WIRING), node.get(X_LEDGER))
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
    """-> ([(lineno, id, [name], exception, verdict)], errors).

    Read line by line rather than through validate-ledger.py: this gate stays
    dependency-free and must still say something useful about a file that gate
    would reject outright.
    """
    rows, errors = [], []
    if not LEDGER.is_file():
        return rows, [f"{LEDGER.relative_to(ROOT)} is missing, so checks 4 and 6 cannot run"]
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
        rows.append((lineno, row.get("id", "?"), names, row.get(PROFILE_FIELD_EXCEPTION),
                     row.get("verdict")))
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
# Check 6: the schema-to-ledger direction
# ---------------------------------------------------------------------------


def ledger_covered(declared, named):
    """Declared paths some ledger row names, directly or through the tree.

    One relation seen from three depths. A row naming a leaf reaches it through
    its ancestors, so those are covered; a row naming a section describes
    everything under it, so those are covered too. Names that are not declared
    paths at all are check 4's finding and are ignored here, so one typo is not
    reported twice.
    """
    covered = set()
    for name in named:
        if name not in declared:
            continue
        covered.add(name)
        path = name
        while True:
            path, _ = parent_of(path)
            if not path:
                break
            covered.add(path)
        for other in declared:
            if other.startswith(f"{name}.") or other.startswith(f"{name}["):
                covered.add(other)
    return covered


def ledger_problem(key, annotation, verdicts):
    """Is this x-ledger annotation usable as evidence, and still true?"""
    if not isinstance(annotation, dict):
        return f"{X_LEDGER} must be an object carrying reason, why and evidence"
    missing = [f for f in ("reason", "why", "evidence") if not annotation.get(f)]
    if missing:
        return f"{X_LEDGER} is missing {', '.join(missing)}"
    if annotation["reason"] not in X_LEDGER_REASONS:
        return (f"{X_LEDGER}.reason is {annotation['reason']!r}; expected one of "
                f"{sorted(X_LEDGER_REASONS)}")

    # `path` or `path:token`, the shape the other two annotations use.
    evidence, _, token = str(annotation["evidence"]).partition(":")
    target = ROOT / evidence
    if not target.is_file():
        return f"{X_LEDGER}.evidence names {evidence}, which is not a file in this repository"
    token = token or parent_of(key)[1] or key
    if not TOKEN_RE.fullmatch(token):
        return (f"{X_LEDGER}.evidence has to spell its token as 'path:token'; {token!r} is "
                f"not something a reader can search for")
    if token not in target.read_text(encoding="utf-8", errors="replace"):
        return (f"{X_LEDGER}.evidence is {evidence}, which does not mention {token!r}: it "
                f"cannot be the evidence for this key")

    if annotation["reason"] == "row-names-no-field":
        # The claim is that a row exists and its verdict names no field, so the
        # token has to be that row's id and the verdict has to still be one that
        # names nothing. A row flipped to spoof without naming this key is the
        # staleness no other check could see.
        if token not in verdicts:
            return (f"{X_LEDGER}.reason is row-names-no-field, so its evidence token has to be "
                    f"a row id in ledger/surfaces.jsonl; {token!r} is not one")
        if verdicts[token] == "spoof":
            return (f"{X_LEDGER}.reason is row-names-no-field, but {token} is now verdict "
                    f"'spoof', which the surface schema requires to name a profile_field. "
                    f"Either that row names this key, or the row is claiming a spoof nothing "
                    f"can serve.")
    return None


# ---------------------------------------------------------------------------
# Check 5: what enforces each coherence edge
# ---------------------------------------------------------------------------


def coherence_rows():
    """-> ([(lineno, id, enforced_by, unbuilt)], errors), read line by line."""
    rows, errors = [], []
    if not COHERENCE.is_file():
        return rows, [f"{COHERENCE.relative_to(ROOT)} is missing, so check 5 cannot run"]
    for lineno, line in enumerate(COHERENCE.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError as exc:
            errors.append(f"ledger/coherence.jsonl:{lineno} is not valid JSON: {exc}")
            continue
        rows.append((lineno, row.get("id", "?"), row.get(ENFORCED_BY, "missing"),
                     row.get(UNBUILT_DEPENDENCY)))
    return rows, errors


def resolver_facts():
    """-> ({function}, {called function}, {edge id: [lineno]}) from the resolver."""
    if not RESOLVER.is_file():
        return set(), set(), {}
    text = RESOLVER.read_text(encoding="utf-8", errors="replace")
    defined = set(re.findall(r"^\s*def\s+([A-Za-z_][A-Za-z0-9_]*)\s*\(", text, re.M))
    called = set()
    markers = {}
    for lineno, line in enumerate(text.splitlines(), 1):
        # The comment half carries the markers; the code half is the only thing
        # the dead-function guard may read, so a function named in a comment is
        # not mistaken for a call to it.
        code, _, _comment = line.partition("#")
        match = COH_MARKER_RE.search(line)
        if match:
            markers.setdefault(match.group("id"), []).append(lineno)
        if re.match(r"^\s*def\s", code):
            continue
        for name in re.findall(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(", code):
            called.add(name)
    return defined, called, markers


def registered_probes():
    """-> {probe id} the collector actually registers."""
    if not COLLECTOR.is_file():
        return set()
    return set(PROBE_RE.findall(COLLECTOR.read_text(encoding="utf-8", errors="replace")))


def registered_checks():
    """-> {edge id} the coherence gate actually registers."""
    if not GATE.is_file():
        return set()
    return set(GATE_CHECK_RE.findall(GATE.read_text(encoding="utf-8", errors="replace")))


def enforcement_problem(edge, entry, facts, probes, checks, series):
    """Does this enforced_by entry name a site that exists?"""
    if not isinstance(entry, dict):
        return f"{ENFORCED_BY} entries must be objects carrying kind and site"
    kind, site = entry.get("kind"), entry.get("site")
    if kind not in ENFORCEMENT_KINDS:
        return f"{ENFORCED_BY}.kind is {kind!r}; expected one of {list(ENFORCEMENT_KINDS)}"
    if not isinstance(site, str) or not site.strip():
        return f"{ENFORCED_BY}.site must be a non-empty string for kind {kind!r}"

    if kind == "resolver":
        match = RESOLVER_SITE_RE.match(site)
        if not match:
            return (f"a resolver site is spelled 'scripts/profile_resolver.py:<function>'; "
                    f"{site!r} is not")
        defined, called, markers = facts
        function = match.group("fn")
        if function not in defined:
            return f"scripts/profile_resolver.py defines no {function}()"
        if function not in called:
            return (f"scripts/profile_resolver.py never calls {function}(), so it enforces "
                    f"nothing: a dead function is not a gate")
        if edge not in markers:
            return (f"scripts/profile_resolver.py carries no '# coh: {edge}' marker, so "
                    f"nothing ties {function}() to this edge and deleting the condition "
                    f"would leave the claim standing")
        return None

    if kind == "patch":
        if site not in series:
            return (f"patches/series does not list {site}, so the patch is in no build "
                    f"and enforces nothing")
        if not (PATCH_DIR / site).is_file():
            return f"patches/{site} is not on disk"
        return None

    if kind == "coherence-gate":
        # A relation that must hold inside ONE live browser -- a window rect
        # inside its work area, a rect against computed style, a resolved
        # timezone in the supported set. It cannot be a v3-probe: the
        # assignment rule for that kind requires none of the named observables
        # be excluded by conform.py, and screen.geometry's screenX, screenY,
        # outerWidth, outerHeight, availWidth and availHeight are all in
        # VOLATILE_PATHS -- correctly, because a diff between two captures
        # cannot decide a relation over window-state values. The instrument is
        # a single-binary check, so it gets its own kind.
        match = GATE_SITE_RE.match(site)
        if not match:
            return (f"a coherence-gate site is spelled "
                    f"'capture/derive/coherence.py:<edge id>'; {site!r} is not")
        # The site must name this row's OWN edge. Without it a row can borrow a
        # neighbouring edge's check and the claim survives deleting the very
        # thing it claims -- the same hole the resolver '# coh:' marker closes.
        if match.group("edge") != edge:
            return (f"{site!r} names {match.group('edge')}, not this row's edge {edge}, so "
                    f"the row claims a check that decides a different invariant")
        if not GATE.is_file():
            return "capture/derive/coherence.py is not on disk"
        if match.group("edge") not in checks:
            return (f"capture/derive/coherence.py registers no check for {edge}, so nothing "
                    f"evaluates this invariant")
        return None

    match = PROBE_SITE_RE.match(site)
    if not match:
        return (f"a V3 probe site is spelled 'capture/collector/collector.js:<probe id>'; "
                f"{site!r} is not")
    probe = match.group("probe")
    if probe not in probes:
        return (f"capture/collector/collector.js registers no probe {probe!r}, so no "
                f"collection records what this edge compares")
    return None


def unbuilt_problem(entry, declared):
    """Is this unbuilt_dependency entry usable, and still true?"""
    if not isinstance(entry, dict):
        return f"{UNBUILT_DEPENDENCY} entries must be objects carrying key, reason and why"
    missing = [f for f in ("key", "reason", "why") if not entry.get(f)]
    if missing:
        return f"{UNBUILT_DEPENDENCY} entry is missing {', '.join(missing)}"
    key, reason = entry["key"], entry["reason"]
    if reason not in UNBUILT_DEPENDENCY_REASONS:
        return (f"{UNBUILT_DEPENDENCY}.reason is {reason!r}; expected one of "
                f"{sorted(UNBUILT_DEPENDENCY_REASONS)}")
    if reason == "undeclared":
        if key in declared:
            return (f"{UNBUILT_DEPENDENCY} calls {key} undeclared, but "
                    f"config/profile.schema.json now declares it at {declared[key].location}. "
                    f"The exception outlived the problem it described.")
        return None
    if key not in declared:
        return (f"{UNBUILT_DEPENDENCY} calls {key} unconditioned, but the schema declares no "
                f"such key at all, so the reason is 'undeclared'")
    values = composed_values(key)
    if len(values) > 1:
        return (f"{UNBUILT_DEPENDENCY} calls {key} unconditioned, but the dispersion tables "
                f"now compose {len(values)} different values for it "
                f"({', '.join(values[:4])}). The axis it was waiting for exists.")
    return None


# ---------------------------------------------------------------------------
# Check 7: a cited Chromium source path exists
# ---------------------------------------------------------------------------


def ledger_lines():
    """-> [(relative path, lineno, raw line, parsed row or None)] over ledger/*.jsonl.

    All three files, not just surfaces: emitters name the file that emits a
    value and coherence rows cite source in their evidence, so a wrong path is
    equally unfalsifiable in any of them. A line that does not parse is carried
    through as None rather than dropped, because check 4 is where a parse
    failure is reported and dropping it here would hide the citations on it.
    """
    out = []
    for path in sorted(LEDGER_DIR.glob("*.jsonl")):
        rel = path.relative_to(ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except ValueError:
                row = None
            out.append((rel, lineno, line, row))
    return out


def patch_header_prose(text):
    """-> [(lineno, line)] for the prose above the first diff line.

    The boundary is the whole point. Everything from the first diff line down
    is patch CONTENT: hunk bodies carry `#include "third_party/..."`, context
    lines naming real files, and added source that mentions paths it does not
    cite. None of that is a citation -- it is the code being changed -- and
    extracting from it would produce hundreds of findings against a clean tree,
    at which point the check gets switched off and protects nothing.

    `--- ` and `+++ ` are matched with their trailing space so the bare `---`
    that git format-patch puts before a diffstat cannot end the prose early;
    the diffstat itself is prose worth reading, and its elided
    `.../x64/libavcodec/codec_list.c` form is discarded downstream because
    `...` is not an entry at the checkout root.

    Several patches have no prose at all -- 0061 opens on `diff --git` -- and
    an empty result is the correct answer for those, not a reason to look
    harder.
    """
    out = []
    for lineno, line in enumerate(text.splitlines(), 1):
        if line.startswith(("diff --git ", "--- ", "+++ ", "@@ ")):
            break
        out.append((lineno, line))
    return out


def markdown_citations(text):
    """-> [(lineno, text)] for fenced blocks and inline code spans only.

    Markdown is prose and will contain deliberate non-paths: an illustrative
    `foo/bar.cc`, a filename in a sentence that names a shape rather than a
    file. Scanning bare prose would make the check fire on an example, and a
    check that fires on examples is one people learn to ignore.

    A citation someone expects a reader to open is written as code in this
    repository's docs -- `docs/BUILD.md` cites every script and source path
    that way -- so restricting to code spans costs no real coverage and buys
    the whole false-positive class. The narrower rule was chosen over scanning
    prose for exactly that trade.
    """
    out = []
    fenced = False
    for lineno, line in enumerate(text.splitlines(), 1):
        if FENCE_RE.match(line):
            fenced = not fenced
            continue
        if fenced:
            out.append((lineno, line))
            continue
        spans = INLINE_CODE_RE.findall(line)
        if spans:
            # Joined with a space, which the extractor's lookaround treats as a
            # boundary, so two adjacent spans cannot be read as one path.
            out.append((lineno, " ".join(spans)))
    return out


def prose_lines():
    """-> ([(file, lineno, text, None)], {source: line count}) for the non-ledger sources.

    Same shape ledger_lines() returns, so one extractor and one resolution loop
    serve all four. The fourth element is None because these lines are not JSON
    rows and carry no annotation.
    """
    out = []
    counted = {"tsv": 0, "markdown": 0, "patch": 0}

    def add(rel, pairs, kind):
        for lineno, text in pairs:
            out.append((rel, lineno, text, None))
        counted[kind] += len(pairs)

    for path in sorted(SCRIPTS_DIR.glob("*.tsv")):
        text = path.read_text(encoding="utf-8", errors="replace")
        pairs = list(enumerate(text.splitlines(), 1))
        add(path.relative_to(ROOT).as_posix(), pairs, "tsv")
    for path in sorted(DOCS_DIR.rglob("*.md")):
        text = path.read_text(encoding="utf-8", errors="replace")
        add(path.relative_to(ROOT).as_posix(), markdown_citations(text), "markdown")
    for path in sorted(PATCH_DIR.glob("*.patch")):
        text = path.read_text(encoding="utf-8", errors="replace")
        add(path.relative_to(ROOT).as_posix(), patch_header_prose(text), "patch")
    return out, counted


def cited_paths(lines, roots):
    """-> {path: [(file, lineno)]}, every Chromium source path the ledger cites.

    Three things decide whether a token is a citation of Chromium source, and
    each of them was a false positive in the first hand audit of this file:

      * It must not be inside a URL. `.../angle/angle/+/<sha>/src/libANGLE/
        Context.cpp` is a googlesource permalink, and its tail looks exactly
        like a repository path. Both the URL span and the `/+/` infix are
        excluded, because either one alone leaves the other shape through.
      * Its extension must match whole. An alternation ordered `gn|gni` truncates
        `build.gni` to `build.gn` and `runtime_enabled_features.json5` to
        `.json`, and then reports a file that was never cited as missing. The
        alternation is sorted longest-first and the match is end-anchored, which
        are two independent guards against the same bug.
      * Its first segment must name an entry at the root of the pinned checkout.
        That is what separates `base/ieee754.cc` — a Chromium path, and wrong —
        from `win/font_cache_skia_win.cc`, a fragment relative to something the
        prose named earlier, which this pass cannot and should not resolve.
    """
    out = {}
    for rel, lineno, line, _ in lines:
        spans = [m.span() for m in URL_RE.finditer(line)]
        for match in CITED_PATH_RE.finditer(line):
            if any(start <= match.start() < end for start, end in spans):
                continue
            path = match.group(1)
            if "/+/" in path or "://" in path:
                continue
            if path.split("/", 1)[0] not in roots:
                continue
            out.setdefault(path, []).append((rel, lineno))
    return out


def series_created():
    """Paths the sequenced patches CREATE, from patches/ rather than from the tree.

    This is the fork's own source of truth about what it owns, and reading it
    here rather than the checkout is load-bearing, not a detail to simplify
    away. `base/apostate/profile.cc` exists in .workspace/src only while the
    series is applied: a check that resolved it against the tree would pass on a
    patched checkout and report a broken citation on a pristine one. A gate
    whose verdict depends on state unrelated to what it is checking is worse
    than no gate, because it teaches everyone to ignore its output.

    Only created files, not every touched file: a patch that MODIFIES an
    upstream file is not evidence that the file is ours, and crediting it here
    would let a citation of a deleted upstream path pass.
    """
    created = set()
    for name in series_entries()[0]:
        patch = PATCH_DIR / name
        if not patch.is_file():
            continue
        text = patch.read_text(encoding="utf-8", errors="replace")
        for match in PATCH_ADDS_RE.finditer(text):
            created.add(match.group(1).strip())
    return created


def deps_checkouts():
    """-> ([declared directory], {unpopulated directory}) from the pinned DEPS.

    A DEPS sub-repository that was never synced is an empty directory, and every
    path inside it is absent for a reason that has nothing to do with the
    citation. Reporting those would be the same cry-wolf failure as the regex
    bugs: this project has already once concluded a file did not exist from a
    grep over an unsynced checkout, and stated it as fact.
    """
    deps_file = WORKSPACE / "DEPS"
    if not deps_file.is_file():
        return [], set()
    declared, inside = [], False
    for line in deps_file.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.startswith("deps = {"):
            inside = True
            continue
        if inside and line.startswith("}"):
            break
        if inside:
            match = DEPS_KEY_RE.match(line)
            if match:
                declared.append(match.group(1).rstrip("/"))
    unpopulated = set()
    for rel in declared:
        target = WORKSPACE / rel
        if not target.is_dir() or not any(target.iterdir()):
            unpopulated.add(rel)
    return declared, unpopulated


def deps_owner(path, declared):
    """The deepest DEPS-declared directory containing `path`, or None."""
    best = None
    for rel in declared:
        if path == rel or path.startswith(f"{rel}/"):
            if best is None or len(rel) > len(best):
                best = rel
    return best


def in_pinned_source(path):
    """Does the pinned checkout carry this path as SOURCE?

    A path under out/ is build output. Whether it is on disk depends on which
    targets this machine happened to build, so counting it as present would make
    the gate answer a different question per machine — exactly what the header
    says a gate reading .workspace/src must not do. Such a citation is checkable
    only through its generator input, which is what the exception names.
    """
    if path.split("/", 1)[0] == BUILD_ROOT:
        return False
    return (WORKSPACE / path).exists()


def pinned_revision_paths(paths):
    """Which of `paths` the pinned revision itself contains, or None if unknowable.

    Read-only, and batched: one `ls-tree` per git repository involved, with the
    paths as pathspecs. The main checkout and each synced DEPS sub-repository are
    separate repositories, so a path is asked of the deepest one that owns it.
    """
    groups = {}
    for path in paths:
        parts = path.split("/")
        owner = ""
        for depth in range(len(parts) - 1, 0, -1):
            if (WORKSPACE.joinpath(*parts[:depth]) / ".git").exists():
                owner = "/".join(parts[:depth])
                break
        groups.setdefault(owner, []).append(path)

    present = set()
    for owner, owned in groups.items():
        cwd = WORKSPACE / owner if owner else WORKSPACE
        rels = [path[len(owner) + 1:] if owner else path for path in owned]
        try:
            result = subprocess.run(
                ["git", "--no-optional-locks", "-C", str(cwd), "ls-tree", "-z",
                 "--name-only", "--full-name", "HEAD", "--", *rels],
                capture_output=True, text=True, check=False)
        except OSError:
            return None
        if result.returncode != 0:
            return None
        listed = set(result.stdout.split("\0"))
        present.update(path for path, rel in zip(owned, rels) if rel in listed)
    return present


def generated_exceptions(lines):
    """-> ({path: (file, lineno, row id, entry)}, findings) for the shape errors."""
    out, findings = {}, []
    for rel, lineno, line, row in lines:
        if not isinstance(row, dict):
            continue
        block = row.get(GENERATED_PATH_EXCEPTION)
        if block is None:
            continue
        where = f"{rel}:{lineno} ({row.get('id', '?')})"
        if not isinstance(block, list) or not block:
            findings.append(
                f"BAD EXCEPTION            {row.get('id', '?')}\n"
                f"        {where}: {GENERATED_PATH_EXCEPTION} must be a non-empty array"
            )
            continue
        for entry in block:
            if not isinstance(entry, dict) or not entry.get("path"):
                findings.append(
                    f"BAD EXCEPTION            {row.get('id', '?')}\n"
                    f"        {where}: {GENERATED_PATH_EXCEPTION} entries must be objects "
                    f"carrying path, reason, why and evidence"
                )
                continue
            path = entry["path"]
            if path not in line:
                findings.append(
                    f"STALE EXCEPTION          {path}\n"
                    f"        {where} excuses it, but the row no longer cites it.\n"
                    f"        The exception outlived the citation it described; delete it."
                )
                continue
            out[path] = (rel, lineno, row.get("id", "?"), entry)
    return out, findings


def generated_path_problem(entry):
    """Is this generated_path_exception usable as evidence, and still true?"""
    missing = [f for f in ("path", "reason", "why", "evidence") if not entry.get(f)]
    if missing:
        return f"{GENERATED_PATH_EXCEPTION} is missing {', '.join(missing)}"
    if entry["reason"] not in GENERATED_PATH_REASONS:
        return (f"{GENERATED_PATH_EXCEPTION}.reason is {entry['reason']!r}; expected one of "
                f"{sorted(GENERATED_PATH_REASONS)}")

    # `path` or `path:token`, the shape the other annotations use. The generator
    # input is Chromium source as often as it is ours, so it resolves against
    # either tree — but it must resolve, and it must mention the token, because a
    # generated citation whose generator cannot be opened is not evidence at all.
    evidence, _, token = str(entry["evidence"]).partition(":")
    target = ROOT / evidence
    if not target.is_file():
        target = WORKSPACE / evidence
    if not target.is_file():
        return (f"{GENERATED_PATH_EXCEPTION}.evidence names {evidence}, which is not a file "
                f"in this repository or in the pinned checkout")
    token = token or pathlib.PurePosixPath(entry["path"]).name
    if not TOKEN_RE.fullmatch(token):
        return (f"{GENERATED_PATH_EXCEPTION}.evidence has to spell its token as "
                f"'path:token'; {token!r} is not something a reader can search for")
    if token not in target.read_text(encoding="utf-8", errors="replace"):
        return (f"{GENERATED_PATH_EXCEPTION}.evidence is {evidence}, which does not mention "
                f"{token!r}: it cannot be the evidence for this citation")
    return None


def check_citations():
    """-> (findings, tally). Every Chromium path this repository cites must resolve.

    Four sources, because a citation nothing reads is a citation nothing
    protects. The ledger was the first, and widening to the other three was
    prompted by two demonstrations on one day: a relay that said Windows reads
    chromium/config/Chrome/win-msvc/x64 when build/args/windows-x64.gn sets
    is_clang and so it reads win/x64, and the same error restated in a message.
    Both were caught by a human reading carefully, which is not a control.

      ledger/*.jsonl        every line; rows may also carry an exception
      scripts/*.tsv         every line, comments included -- the evidence
                            column of scripts/series-absences.tsv is nothing
                            but citations, and its header comments cite too
      docs/**/*.md          fenced blocks and inline code spans only, never
                            bare prose; see markdown_citations()
      patches/*.patch       the header prose only, never the diff; see
                            patch_header_prose()

    Skipped, loudly, without a pinned checkout: CI runs these gates with no
    36 GB of somebody else's code, and a check that silently passed there would
    be indistinguishable from one that had nothing to say.
    """
    tally = {
        "skipped": None, "cited": 0, "local": 0, "pinned": 0, "fork": 0,
        "unsynced": [], "excused": 0, "revision": True, "notes": [],
        "sources": {"ledger": 0, "tsv": 0, "markdown": 0, "patch": 0},
    }
    if not WORKSPACE.is_dir():
        tally["skipped"] = (f"no pinned checkout at "
                            f"{WORKSPACE.relative_to(ROOT).as_posix()}")
        return [], tally

    ledger = ledger_lines()
    prose, counted = prose_lines()
    tally["sources"]["ledger"] = len(ledger)
    tally["sources"].update(counted)
    roots = {entry.name for entry in WORKSPACE.iterdir()}
    # One extractor over every source. The guards in cited_paths() were arrived
    # at by fixing seven false positives, and a second extractor for the new
    # sources would drift from them the first time either was touched.
    cited = cited_paths(ledger + prose, roots)
    # Annotations live in ledger rows and only there: a markdown sentence or a
    # patch header has nowhere to carry one, so the prose lines are deliberately
    # not offered to this.
    exceptions, findings = generated_exceptions(ledger)
    declared_deps, unpopulated = deps_checkouts()
    created = series_created()
    tally["cited"] = len(cited)

    # A stale annotation is the same defect wearing the opposite sign, so this
    # runs BEFORE the resolution loop: a path that now resolves has to be
    # reported as an exception that outlived its cause, not quietly credited as
    # a path that exists.
    for path in sorted(exceptions):
        rel, lineno, row_id, _ = exceptions[path]
        if (ROOT / path).exists() or in_pinned_source(path):
            findings.append(
                f"STALE EXCEPTION          {path}\n"
                f"        {rel}:{lineno} ({row_id}) excuses it as generated, but the path "
                f"now resolves.\n"
                f"        The exception outlived the problem it described; delete it."
            )
            del exceptions[path]

    on_disk = [path for path in cited
               if not (ROOT / path).exists() and in_pinned_source(path)]
    at_revision = pinned_revision_paths(on_disk)
    if at_revision is None:
        tally["revision"] = False

    for path, sites in sorted(cited.items()):
        where = ", ".join(f"{name}:{line}" for name, line in sites[:3])
        if (ROOT / path).exists():
            tally["local"] += 1                      # a path of our own, not Chromium's
            continue
        if in_pinned_source(path):
            if at_revision is None or path in at_revision:
                tally["pinned"] += 1
                continue
            if path in created:
                tally["fork"] += 1
                continue
            findings.append(
                f"CITED AS UPSTREAM        {path}\n"
                f"        {where}\n"
                f"        The path is in .workspace/src and is NOT in the pinned revision, "
                f"and no sequenced patch creates it. It resolves only because this checkout "
                f"has been mutated, so the citation would break for anyone else."
            )
            continue
        if path in created:
            tally["fork"] += 1                       # ours; patches/ is the evidence
            continue
        owner = deps_owner(path, declared_deps)
        if owner in unpopulated:
            tally["unsynced"].append(path)
            tally["notes"].append(f"{path}: DEPS checkout {owner} is declared and not "
                                  f"synced, so absence here is not evidence")
            continue
        excuse = exceptions.pop(path, None)
        if excuse is not None:
            rel, lineno, row_id, entry = excuse
            problem = generated_path_problem(entry)
            if problem:
                findings.append(
                    f"BAD EXCEPTION            {path}\n"
                    f"        {rel}:{lineno} ({row_id}): {problem}"
                )
            else:
                tally["excused"] += 1
                tally["notes"].append(f"{path}: {entry['reason']}, checkable through "
                                      f"{entry['evidence']}")
            continue
        findings.append(
            f"CITED PATH IS ABSENT     {path}\n"
            f"        {where}\n"
            f"        Not in .workspace/src, not created by a sequenced patch, and not in an "
            f"unsynced DEPS checkout. Nobody reading the citation can open it. Fix the path, "
            f"or carry {GENERATED_PATH_EXCEPTION} if the build generates it and the citation "
            f"is a ledger row."
        )

    # Whatever is left appears in its row's text but is not a path this check
    # resolves — a first segment that is not a checkout root, or an extension
    # outside CITED_SUFFIXES. The annotation excuses nothing either way.
    for path, (rel, lineno, row_id, _) in sorted(exceptions.items()):
        findings.append(
            f"BAD EXCEPTION            {path}\n"
            f"        {rel}:{lineno} ({row_id}) excuses it, but this check does not read it "
            f"as a Chromium source citation, so the exception excuses nothing.\n"
            f"        Spell the path as the row cites it, relative to the source root."
        )
    return findings, tally

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
    named = {name for _, _, names, _, _ in rows for name in names}
    print("\nledger profile fields")
    print(f"  {len(rows)} row(s) in ledger/surfaces.jsonl name {len(named)} distinct "
          f"profile field(s)")

    for lineno, surface, names, exception, _ in rows:
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

    # ----------------------------------------------------------------- check 6
    verdicts = {surface: verdict for _, surface, _, _, verdict in rows}
    covered = ledger_covered(declared, named)
    orphans = {path for path in declared if path not in covered}

    print("\nschema keys in the ledger")
    print(f"  {len(covered)} of {len(declared)} declared key(s) are named by a row, directly "
          f"or through the tree")

    unnamed_findings = []
    for path in sorted(shallowest(orphans)):
        entry = declared[path]
        annotation = entry.ledger
        if annotation is None:
            hidden = sum(1 for other in orphans
                         if other.startswith(f"{path}.") or other.startswith(f"{path}["))
            extra = f" (and {hidden} key(s) below it)" if hidden else ""
            unnamed_findings.append(
                f"DECLARED BUT UNNAMED     {path}{extra}\n"
                f"        schema {entry.location}\n"
                f"        ledger/surfaces.jsonl: no row names it, nor an ancestor or a "
                f"descendant of it. The schema is derived FROM the ledger, so this key is a "
                f"surface with no verification tier, no evidence and no coherence edge. "
                f"Being read by C++ does not close this: keyboard.layout_map was read."
            )
            continue
        problem = ledger_problem(path, annotation, verdicts)
        if problem:
            unnamed_findings.append(
                f"BAD EXCEPTION            {path}\n        schema {entry.location}: {problem}"
            )
        elif verbose:
            print(f"  note {path}: exempt ({annotation['reason']}) — {annotation['why']}")

    for path, entry in sorted(declared.items()):
        if entry.ledger is None or path in orphans:
            continue
        namer = sorted(name for name in named
                       if name == path or path.startswith(f"{name}.")
                       or path.startswith(f"{name}[") or name.startswith(f"{path}.")
                       or name.startswith(f"{path}["))
        unnamed_findings.append(
            f"STALE EXCEPTION          {path}\n"
            f"        schema {entry.location} carries {X_LEDGER}, but a ledger row now names "
            f"{', '.join(namer[:3]) or 'it'}.\n"
            f"        The exception outlived the problem it described; delete it."
        )

    for finding in unnamed_findings:
        print(f"  FAIL {finding}")
    findings.extend(unnamed_findings)
    if not unnamed_findings:
        print("  ok   every declared key is named by a row or carries a stated exception")

    # ----------------------------------------------------------------- check 5
    edges, coherence_findings = coherence_rows()
    facts = resolver_facts()
    probes = registered_probes()
    checks = registered_checks()
    series = set(entries)
    claimed_edges = set()
    unenforced = []

    for lineno, edge, enforced, unbuilt in edges:
        if enforced == "missing" or not isinstance(enforced, list):
            coherence_findings.append(
                f"NO ENFORCEMENT FIELD     {edge}\n"
                f"        ledger/coherence.jsonl:{lineno} carries no {ENFORCED_BY} array.\n"
                f"        The field is required and may be empty: an edge that can omit it "
                f"vanishes from the count of edges nothing enforces instead of appearing in "
                f"it, which is how 110 unevaluated predicates went unnoticed."
            )
        elif not enforced:
            unenforced.append(edge)
        else:
            for entry in enforced:
                problem = enforcement_problem(edge, entry, facts, probes, checks, series)
                if problem:
                    site = entry.get("site") if isinstance(entry, dict) else entry
                    coherence_findings.append(
                        f"ENFORCED BY NOTHING      {edge}\n"
                        f"        ledger/coherence.jsonl:{lineno} names {site!r}: {problem}"
                    )
                elif isinstance(entry, dict) and entry.get("kind") == "resolver":
                    claimed_edges.add(edge)

        if unbuilt is None:
            continue
        if not isinstance(unbuilt, list) or not unbuilt:
            coherence_findings.append(
                f"BAD EXCEPTION            {edge}\n"
                f"        ledger/coherence.jsonl:{lineno}: {UNBUILT_DEPENDENCY} must be a "
                f"non-empty array when present"
            )
            continue
        for entry in unbuilt:
            problem = unbuilt_problem(entry, declared)
            if problem:
                coherence_findings.append(
                    f"BAD EXCEPTION            {edge}\n"
                    f"        ledger/coherence.jsonl:{lineno}: {problem}"
                )

    # The marker binds a resolver condition to an edge, so it is checked in both
    # directions: a marker for an edge that does not exist, or that no longer
    # claims a resolver, is the same defect wearing the opposite sign.
    edge_ids = {edge for _, edge, _, _ in edges}
    for edge, linenos in sorted(facts[2].items()):
        if edge not in edge_ids:
            coherence_findings.append(
                f"STALE MARKER             {edge}\n"
                f"        scripts/profile_resolver.py:{linenos[0]} marks a condition for "
                f"{edge}, which is not an edge in ledger/coherence.jsonl."
            )
        elif edge not in claimed_edges:
            coherence_findings.append(
                f"STALE MARKER             {edge}\n"
                f"        scripts/profile_resolver.py:{linenos[0]} marks a condition for "
                f"{edge}, but that edge no longer names a resolver site.\n"
                f"        The marker outlived the claim it supported; delete it."
            )

    print("\ncoherence enforcement")
    print(f"  {len(edges)} edge(s) in ledger/coherence.jsonl; {len(unenforced)} enforced by "
          f"nothing")
    for finding in coherence_findings:
        print(f"  FAIL {finding}")
    findings.extend(coherence_findings)
    if not coherence_findings:
        print("  ok   every enforcement site named by an edge exists")
    if verbose and unenforced:
        print("  note enforced by nothing: " + ", ".join(sorted(unenforced)))

    # ----------------------------------------------------------------- check 7
    citation_findings, tally = check_citations()

    print("\nsource citations")
    if tally["skipped"]:
        print(f"  SKIP {tally['skipped']}, so the citations in ledger/*.jsonl, "
              f"scripts/*.tsv, docs/**/*.md and the patch headers were not resolved. "
              f"Nothing here passed; nothing was checked.")
    else:
        print(f"  read {tally['sources']['ledger']} ledger row(s), "
              f"{tally['sources']['tsv']} declaration line(s), "
              f"{tally['sources']['markdown']} markdown code line(s) and "
              f"{tally['sources']['patch']} patch header line(s)")
        print(f"  {tally['cited']} distinct Chromium source path(s) cited across "
              f"ledger/*.jsonl, scripts/*.tsv, docs/**/*.md and patches/*.patch headers: "
              f"{tally['pinned']} in the pinned checkout, {tally['fork']} "
              f"created by a sequenced patch, {tally['local']} of our own, "
              f"{tally['excused']} build-generated, {len(tally['unsynced'])} in an unsynced "
              f"DEPS checkout")
        if not tally["revision"]:
            print("  note git could not read the pinned revision, so 'present on disk' was "
                  "not separated from 'present at the pinned revision'")
    for finding in citation_findings:
        print(f"  FAIL {finding}")
    findings.extend(citation_findings)
    if not tally["skipped"] and not citation_findings:
        print("  ok   every cited path exists, or is ours, or carries a stated exception")
    if verbose:
        for note in tally["notes"]:
            print(f"  note {note}")

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

    citations = (f"the citation check skipped ({tally['skipped']})" if tally["skipped"]
                 else f"{tally['cited']} cited source path(s)")
    print(f"\nseven checks over {len(declared)} declared key(s), {len(entries)} sequenced "
          f"patch(es), {len(rows)} ledger row(s), {len(edges)} coherence edge(s), of which "
          f"{len(unenforced)} edge(s) are enforced by nothing, and {citations}: "
          f"{len(findings)} finding(s)")
    return 1 if findings else 0


if __name__ == "__main__":
    sys.exit(main())
