#!/usr/bin/env python3
"""V0 gate: validate ledger files against their schemas, and reconcile the
ledger against the detector-surface catalogue.

Deliberately dependency-free. This runs before anything else in the pipeline, so
it must work on a bare checkout with no install step — a gate that needs setting
up is a gate that gets skipped.

Implements the subset of JSON Schema the ledger schemas actually use, and fails
loudly on any construct it does not implement rather than passing it silently.

    python3 scripts/validate-ledger.py            # gate + reconciliation summary
    python3 scripts/validate-ledger.py --full     # plus every unmapped catalogue key
"""

import json
import pathlib
import re
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
SCHEMA_DIR = ROOT / "ledger" / "schema"
CATALOGUE = ROOT / "resources" / "surfaces.json"

FILES = [
    ("ledger/surfaces.jsonl", "surface.schema.json"),
    ("ledger/emitters.jsonl", "emitter.schema.json"),
    ("ledger/coherence.jsonl", "coherence.schema.json"),
]

SUPPORTED = {
    "type", "properties", "required", "additionalProperties", "enum", "const",
    "items", "minItems", "maxItems", "pattern", "description", "title", "$id",
    "$schema", "allOf", "if", "then", "contains", "propertyNames", "minLength",
    "format", "$defs", "$ref", "$comment",
}

TYPES = {
    "object": dict, "array": list, "string": str, "boolean": bool,
    "integer": int, "number": (int, float), "null": type(None),
}

# The verification vocabulary, in order. docs/METHODOLOGY.md, "## 10.
# Verification", subsection "How far something has been checked". Comparing a
# row's achieved tier against its required one is the whole point of having
# both fields, so it lives here rather than in a human's head.
TIERS = ["V0", "V1", "V2", "V3", "V4"]

SERIES = ROOT / "patches" / "series"

# The last series entry that has actually been built. Everything from 0001
# through this entry is inside the CI build that went green on linux-x64,
# linux-arm64 and macos-arm64, and whose macos-arm64 artifact launches and
# passes the smoke suite — which is exactly what V2 means, so a row implemented
# by one of them may record V2. Entries AFTER it are in the series and apply
# cleanly but have never been compiled, so a row implemented by one of them can
# record V0 and no more. Move this line when a build lands, not when a patch
# lands: series membership is not a build.
#
# V1 sits between the two and is per-patch rather than per-prefix: a row may
# record V1 once EVERY translation unit its patch touches has compiled against
# the pinned build dir (scripts/checkfile.sh). A partially compiled patch is
# still V0 — one green file does not establish that the patch builds — so V1 is
# recorded from the compile gate's per-patch result, not inferred from a count.
BUILT_THROUGH = "0083-loader-composes-when-no-profile-given.patch"


def resolve(schema, root):
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            raise NotImplementedError(f"external $ref not supported: {ref}")
        node = root
        for part in ref[2:].split("/"):
            node = node[part]
        return node
    return schema


def validate(value, schema, root, path, errors):
    schema = resolve(schema, root)

    unknown = set(schema) - SUPPORTED
    if unknown:
        raise NotImplementedError(f"{path}: schema uses unimplemented keywords {sorted(unknown)}")

    if "type" in schema:
        expected = schema["type"]
        names = expected if isinstance(expected, list) else [expected]
        if not any(isinstance(value, TYPES[n]) for n in names):
            # bool is a subclass of int; keep them distinct
            errors.append(f"{path}: expected {expected}, got {type(value).__name__}")
            return
        if "integer" in names and isinstance(value, bool):
            errors.append(f"{path}: expected integer, got boolean")
            return

    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: {value!r} not one of {schema['enum']}")
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected constant {schema['const']!r}, got {value!r}")

    if isinstance(value, str):
        if "pattern" in schema:
            if not re.search(schema["pattern"], value):
                errors.append(f"{path}: {value!r} does not match /{schema['pattern']}/")
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")

    if isinstance(value, list):
        if "minItems" in schema and len(value) < schema["minItems"]:
            errors.append(f"{path}: needs at least {schema['minItems']} item(s)")
        if "maxItems" in schema and len(value) > schema["maxItems"]:
            errors.append(f"{path}: at most {schema['maxItems']} item(s)")
        if "items" in schema:
            for i, item in enumerate(value):
                validate(item, schema["items"], root, f"{path}[{i}]", errors)
        if "contains" in schema:
            def satisfies(item):
                probe = []
                validate(item, schema["contains"], root, path, probe)
                return not probe
            if not any(satisfies(item) for item in value):
                errors.append(f"{path}: no item satisfies the 'contains' constraint")

    if isinstance(value, dict):
        for key in schema.get("required", []):
            if key not in value:
                errors.append(f"{path}: missing required property '{key}'")
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is False:
            for key in value:
                if key not in props:
                    errors.append(f"{path}: unexpected property '{key}'")
        for key, sub in props.items():
            if key in value:
                validate(value[key], sub, root, f"{path}.{key}", errors)

    for sub in schema.get("allOf", []):
        if "if" in sub:
            probe = []
            validate(value, sub["if"], root, path, probe)
            if not probe and "then" in sub:
                validate(value, sub["then"], root, path, errors)
        else:
            validate(value, sub, root, path, errors)

    return errors


def load_rows(root: pathlib.Path, rel: str):
    f = root / rel
    if not f.exists():
        return []
    return [json.loads(l) for l in f.read_text(encoding="utf-8").splitlines() if l.strip()]


def cross_references(root: pathlib.Path):
    """Check that ids referenced between ledger files actually exist.

    A schema validates each row alone, so it cannot see a dangling reference.
    Independent workers producing rows in parallel is exactly the situation that
    generates them, which is why this runs as part of the gate rather than as a
    later cleanup.
    """
    surfaces = load_rows(root, "ledger/surfaces.jsonl")
    emitters = load_rows(root, "ledger/emitters.jsonl")
    coherence = load_rows(root, "ledger/coherence.jsonl")

    sids = {r["id"] for r in surfaces}
    eids = {r["id"] for r in emitters}
    errors = []

    for s in surfaces:
        for e in s.get("emitters", []):
            if e not in eids:
                errors.append(f"surface {s['id']}: unknown emitter '{e}'")
        for c in s.get("coherence_edges", []):
            if c not in {r["id"] for r in coherence}:
                errors.append(f"surface {s['id']}: unknown coherence edge '{c}'")

    for e in emitters:
        for s in e.get("surfaces", []):
            if s not in sids:
                errors.append(f"emitter {e['id']}: unknown surface '{s}'")
        for u in e.get("upstream_of", []):
            if u not in eids:
                errors.append(f"emitter {e['id']}: unknown upstream emitter '{u}'")

    # Coherence members may name surfaces not yet mapped — that is a backlog
    # signal rather than an error, so it is reported without failing the gate.
    pending = set()
    for c in coherence:
        for m in c.get("members", []):
            if m not in sids:
                pending.add(m)

    # A spoof verdict must name at least one emitter, or nothing can implement it.
    for s in surfaces:
        if s.get("verdict") == "spoof" and not s.get("emitters"):
            errors.append(f"surface {s['id']}: verdict 'spoof' with no emitter identified")

    # Every spoofed surface needs a source of truth among its emitters, not only
    # a waypoint — patching a forwarder leaves the real value in place elsewhere.
    by_id = {e["id"]: e for e in emitters}
    for s in surfaces:
        if s.get("verdict") != "spoof":
            continue
        linked = [by_id[e] for e in s.get("emitters", []) if e in by_id]
        if linked and not any(e.get("is_source_of_truth") for e in linked):
            errors.append(f"surface {s['id']}: no emitter marked is_source_of_truth")

    return errors, sorted(pending)


def catalogue_coverage(surfaces, catalogue):
    """Map every catalogue key to the ledger rows that cover it.

    Two ways a row covers a key, both mechanical so the answer does not drift:
    the key appears verbatim in the row's 'observable', or the row declares it in
    'catalogue_keys'. A '*.member' key is credited to a row whose observable
    names '.member' or ' member', which is how the catalogue writes a property
    whose receiver varies.
    """
    mapped = {}
    observables = [(r["id"], r["observable"]) for r in surfaces]
    explicit = {}
    for r in surfaces:
        for k in r.get("catalogue_keys", []):
            explicit.setdefault(k, []).append(r["id"])

    for entry in catalogue:
        key = entry["key"]
        if key in mapped:
            continue
        hits = list(explicit.get(key, []))
        if key.startswith("*."):
            pattern = r"[.\s]" + re.escape(key[2:]) + r"\b"
            hits += [i for i, obs in observables if re.search(pattern, obs) and i not in hits]
        else:
            hits += [i for i, obs in observables if key in obs and i not in hits]
        mapped[key] = sorted(set(hits))
    return mapped


def tier_index(tier):
    return TIERS.index(tier) if tier in TIERS else None


def series_entries():
    if not SERIES.exists():
        return []
    return [l.strip() for l in SERIES.read_text(encoding="utf-8").splitlines()
            if l.strip() and not l.strip().startswith("#")]


def contradictions(surfaces, built):
    """Rows whose status disagrees with what the row itself records.

    Reported rather than failed: the ledger carries a real backlog, and a gate
    that cannot run is a gate nobody runs. The counts are the point.
    """
    out = []
    for r in surfaces:
        rid = r["id"]
        status = r.get("status")
        req, ach = r.get("verification_tier"), r.get("achieved_tier")
        ri, ai = tier_index(req), tier_index(ach)
        patch = r.get("patch_id")

        if ai is not None and ri is not None:
            if ai >= ri and status in ("open", "proposed"):
                out.append(f"{rid}: achieved {ach} meets required {req} but status is '{status}'")
            if ai < ri and status == "resolved":
                out.append(f"{rid}: status 'resolved' but achieved {ach} is below required {req}")
        if status == "resolved" and ach is None and r.get("verdict") == "spoof":
            out.append(f"{rid}: status 'resolved' as a spoof with no achieved tier recorded")
        if status == "resolved" and r.get("verdict") == "spoof" and not patch:
            out.append(f"{rid}: status 'resolved' as a spoof with no patch_id")
        if ach == "V0" and not patch:
            out.append(f"{rid}: achieved V0 recorded with no patch_id to have applied")
        if (status == "resolved" and r.get("verdict") == "spoof"
                and r.get("corpus_coverage") == "absent" and req in ("V3", "V4")):
            out.append(f"{rid}: status 'resolved' at required {req} with corpus_coverage 'absent'")
        # The tiers have different prerequisites and the check has to follow them.
        # V1 is a compile, available for a patch that will not be in the built
        # prefix until the next full build — every patch after the prefix is in
        # exactly that position, so flagging V1 here cried wolf on fifteen correct
        # rows. V2 and above claim a binary was built and shipped with the change,
        # which the prefix is what establishes; claiming it from outside the prefix
        # says a shipped binary contains something no binary was built with.
        if ai is not None and ai >= TIERS.index("V2") and built and patch and patch not in built:
            out.append(f"{rid}: achieved {ach} recorded but patch '{patch}' is not in the built "
                       f"prefix of patches/series (built through {BUILT_THROUGH}) \u2014 V2 and above "
                       f"claim a built binary")
    return sorted(out)


def reconcile(root: pathlib.Path, full: bool):
    """Report the catalogue/ledger reconciliation. Returns hard errors only."""
    surfaces = load_rows(root, "ledger/surfaces.jsonl")
    if not CATALOGUE.exists():
        print(f"\n  --  {CATALOGUE.relative_to(root)} (not present; reconciliation skipped)")
        return []
    catalogue = json.loads(CATALOGUE.read_text(encoding="utf-8"))
    keys = {e["key"] for e in catalogue}
    severity = {}
    for e in catalogue:
        severity.setdefault(e["key"], e.get("severity", "unrated"))

    errors = []
    for r in surfaces:
        for k in r.get("catalogue_keys", []):
            if k not in keys:
                errors.append(f"surface {r['id']}: catalogue_keys names '{k}', "
                              f"which is not a key in resources/surfaces.json")

    series = series_entries()
    built = set(series[:series.index(BUILT_THROUGH) + 1]) if BUILT_THROUGH in series else set()
    # A patch that is authored and applies but has not been sequenced yet is a
    # real intermediate state while a wave is in flight, so this is reported
    # rather than failed. It is still worth seeing: a row pointing at a patch
    # nobody will build is indistinguishable from progress.
    unsequenced = sorted(f"{r['id']} -> {r['patch_id']}" for r in surfaces
                         if r.get("patch_id") and series and r["patch_id"] not in series)

    mapped = catalogue_coverage(surfaces, catalogue)
    unmapped = sorted(k for k, hits in mapped.items() if not hits)
    covered_rows = {i for hits in mapped.values() for i in hits}
    uncatalogued = sorted(r["id"] for r in surfaces if r["id"] not in covered_rows)
    bad_status = contradictions(surfaces, built)
    claimed = {r["patch_id"] for r in surfaces if r.get("patch_id")}
    unclaimed = [p for p in series if p not in claimed]
    unclaimed_unbuilt = [p for p in unclaimed if p not in built]

    order = ["critical", "high", "medium", "low", "info", "unrated"]
    by_sev = {s: [k for k in unmapped if severity.get(k) == s] for s in order}

    print(f"\ncatalogue reconciliation  ({len(catalogue)} entries, {len(keys)} unique keys "
          f"in resources/surfaces.json; {len(surfaces)} ledger rows)")
    print(f"  {len(keys) - len(unmapped)} catalogued surface(s) map to a ledger row")
    print(f"  {len(unmapped)} catalogued surface(s) with NO ledger row: "
          + ", ".join(f"{s} {len(v)}" for s, v in by_sev.items() if v))
    shown = order if full else ["critical", "high"]
    for s in shown:
        for k in by_sev[s]:
            print(f"    - [{s}] {k}")
    if not full:
        rest = sum(len(by_sev[s]) for s in order if s not in shown)
        if rest:
            print(f"    ... {rest} more at medium/low/info/unrated; pass --full to list them")

    print(f"  {len(uncatalogued)} ledger row(s) with NO catalogue entry "
          f"(wire-level and cross-process surfaces the catalogue does not list):")
    for i in uncatalogued:
        print(f"    - {i}")

    print(f"  {len(bad_status)} row(s) whose status contradicts their own evidence:")
    print("    (review items, not proven errors. 'resolved below required' means closed on T0/T1 evidence "
          "without the depth of check the verdict asks for; 'meets required but open' can be legitimate, "
          "because a tier is a class of check while a row's verification is a specific observation at that "
          "class — both rows in that state carry the reason in their notes)")
    for c in bad_status:
        print(f"    - {c}")

    print(f"  {len(unclaimed)} of {len(series)} patch(es) in patches/series are named by no row's patch_id"
          + (f", {len(unclaimed_unbuilt)} of them after the built prefix:" if unclaimed_unbuilt else ":"))
    print("    (patch_id holds one entry while the relation is many-to-many, so a patch that amends a row "
          "already pointing at an earlier patch, or that deliberately has no observable, counts here)")
    for p in (unclaimed if full else unclaimed_unbuilt):
        print(f"    - {p}")
    if not full and len(unclaimed) > len(unclaimed_unbuilt):
        print(f"    ... {len(unclaimed) - len(unclaimed_unbuilt)} earlier entries; pass --full to list them")

    print(f"  {len(unsequenced)} row(s) pointing at a patch that is not in patches/series "
          f"(authored, not yet sequenced):")
    for u in unsequenced:
        print(f"    - {u}")

    recorded = [r for r in surfaces if r.get("achieved_tier")]
    per_tier = {t: sum(1 for r in recorded if r["achieved_tier"] == t) for t in TIERS}
    open_rows = [r for r in surfaces if r.get("status") == "open"]
    open_ach = [r for r in open_rows if r.get("achieved_tier")]
    untouched = [r for r in open_rows if not r.get("achieved_tier") and not r.get("patch_id")]
    print(f"\nachieved-tier coverage")
    print(f"  {len(recorded)} of {len(surfaces)} row(s) record an achieved tier ("
          + ", ".join(f"{t} {n}" for t, n in per_tier.items() if n) + f"); {len(surfaces) - len(recorded)} blank")
    print(f"  open rows: {len(open_rows)} total, {len(open_ach)} with an achieved tier, "
          f"{len(untouched)} untouched (no patch and no achieved tier)")
    resolved_rows = [r for r in surfaces if r.get("status") == "resolved"]
    resolved_ach = [r for r in resolved_rows if r.get("achieved_tier")]
    print(f"  resolved rows: {len(resolved_rows)} total, {len(resolved_ach)} with an achieved tier, "
          f"{len(resolved_rows) - len(resolved_ach)} blank (inherit, suppress and out-of-scope rows "
          f"have no patch to build, so there is nothing to record)")

    return errors


def main() -> int:
    full = "--full" in sys.argv[1:]
    failures = 0
    checked = 0
    for rel, schema_name in FILES:
        target = ROOT / rel
        schema = json.loads((SCHEMA_DIR / schema_name).read_text())
        if not target.exists():
            print(f"  --  {rel} (not created yet)")
            continue
        for lineno, line in enumerate(target.read_text().splitlines(), 1):
            line = line.strip()
            if not line:
                continue
            checked += 1
            try:
                row = json.loads(line)
            except json.JSONDecodeError as e:
                print(f"  FAIL {rel}:{lineno}: invalid JSON: {e}")
                failures += 1
                continue
            errors = validate(row, schema, schema, row.get("id", f"line {lineno}"), [])
            for err in errors:
                print(f"  FAIL {rel}:{lineno}: {err}")
            failures += len(errors)
        print(f"  ok   {rel}")

    errors, pending = cross_references(ROOT)
    for err in errors:
        print(f"  FAIL {err}")
    failures += len(errors)

    if pending:
        print(f"\n  {len(pending)} coherence member(s) not yet mapped (backlog, not a failure):")
        for m in pending:
            print(f"    - {m}")

    recon_errors = reconcile(ROOT, full)
    for err in recon_errors:
        print(f"  FAIL {err}")
    failures += len(recon_errors)

    print(f"\n{checked} row(s) checked, {failures} failure(s)")
    return 1 if failures else 0


if __name__ == "__main__":
    sys.exit(main())
