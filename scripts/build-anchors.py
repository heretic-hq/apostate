#!/usr/bin/env python3
"""Derive GPU anchors from admitted captures, and prove which ones are equal.

An **anchor** is the atomic, non-recombinable GPU capability cluster: WebGL1
and WebGL2 `extensions`, `parameters`, `precision`, `contextAttributes` and
`antialiasSamples`, the readback render digest, and the WebGPU features, limits
and adapter vendor/architecture. Identity strings — `unmaskedVendor`,
`unmaskedRenderer`, `vendor`, `renderer`, `version`,
`shadingLanguageVersion` — are stripped out of the cluster and carried as a
separate per-member list, because they are the only part of a GPU's report that
is a *name* rather than a *behaviour*.

That split is the whole point. A profile that swaps a renderer string is only
defensible if the capability surface behind the string is the surface that was
measured. So the question "may these two cards present each other's name?"
becomes a digest comparison instead of an opinion, and the answer is written
into the anchor file whether it is yes or no.

Grouping is by measured equality of the **GL cluster** —
`webgl1_caps_sha256`, `webgl2_caps_sha256`, `webgl1_pixels_sha256`,
`webgl2_pixels_sha256`. WebGPU is recorded inside the anchor but is not part of
the grouping key, because it is measurably *not* uniform across cards that agree
on every WebGL table and every rendered pixel: two of the four Linux/Vulkan
NVIDIA hosts return no WebGPU adapter at all. Folding it into the key would
split one measured cluster into two on the strength of whether a second API was
available on the host, which is a property of the host's driver stack, not of
the silicon's capability surface. Every WebGPU variant present is emitted with
the members that produced it, and the `equivalence` block records that the
comparison did not match.

Canvas 2D is excluded for the reason `scripts/decompose-capture.py` already
records: it is a font and raster measurement, not a GPU measurement. It is
reported per member so the exclusion can be checked, not assumed.

Inputs are captures under `resources/fingerprints/raw/` that carry an
`accepted` admission record, plus any capture for which
`corpus/capture-tiers.json` records an anchor exception naming the backend that
capture measures. Nothing else is read; a capture without either is skipped by
name, never inferred from a filename.

The exception exists because one capture needed it and the alternative in use
was worse. The tree's only measurement of stock Chromium's software rasteriser
is a headless session, and it was carrying a hand-written `accepted` admission
record that claimed it "passed admission checks" — a record this script
believed without re-deriving. An acceptance that cannot be re-derived is not
evidence, so that record is gone and the allowance is written down instead,
per capture, naming the one backend it covers and checked against the backend
measured here.

Output is deterministic: no timestamps, no host state, sorted compact JSON.
Running it twice produces byte-identical files.

Usage:
    scripts/build-anchors.py
    scripts/build-anchors.py --out corpus/anchors --no-readme
"""

import argparse
import hashlib
import importlib.util
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = REPO / "resources" / "fingerprints" / "raw"
DEFAULT_OUT = REPO / "corpus" / "anchors"
PIN_PATH = REPO / "build" / "CHROMIUM_VERSION"
SURVEY_PATH = DEFAULT_OUT / "probe-host-survey.json"
ADMISSION_DIRS = (REPO / "corpus" / "blocks" / "admissions", RAW_DIR / "admissions")
IMPORT_SCRIPT = REPO / "scripts" / "import-capture.py"
PROVENANCE_MODULE = REPO / "corpus" / "provenance.py"
PROVENANCE_TIERS = REPO / "corpus" / "capture-tiers.json"
DISPERSION_PATH = (REPO / "resources" / "profiles" / "dispersion"
                   / "gpu_identity.json")

ANCHOR_SCHEMA = "apostate/corpus/anchor/1"

# The capability cluster. Sorted so the digest is stable under key order.
CAPS_FIELDS = ("antialiasSamples", "contextAttributes", "extensions",
               "parameters", "precision")
# Everything in a WebGL probe that names the part rather than describing it.
IDENTITY_FIELDS = ("renderer", "shadingLanguageVersion", "unmaskedRenderer",
                   "unmaskedVendor", "vendor", "version")
# The collector queries every enumerable GL constant, so `parameters` also holds
# the four identity strings under their getParameter names. They are the same
# values the top-level fields carry, so they leave the capability cluster with
# the rest of the identity and are recorded beside it.
PARAMETER_IDENTITY = {"RENDERER": "renderer",
                      "SHADING_LANGUAGE_VERSION": "shadingLanguageVersion",
                      "VENDOR": "vendor",
                      "VERSION": "version"}
GROUPING_DIGESTS = ("webgl1_caps_sha256", "webgl2_caps_sha256",
                    "webgl1_pixels_sha256", "webgl2_pixels_sha256")

BACKENDS = (("SwiftShader", "ANGLE/SwiftShader", "swiftshader"),
            ("Vulkan", "ANGLE/Vulkan", "vulkan"),
            ("Direct3D11", "ANGLE/D3D11", "d3d11"),
            ("D3D11", "ANGLE/D3D11", "d3d11"),
            ("Metal Renderer", "ANGLE/Metal", "metal"),
            ("OpenGL", "ANGLE/OpenGL", "gl"))

DEVICE_PATTERNS = (
    re.compile(r"Vulkan [\d.]+ \((?P<device>.+?) \(0x[0-9A-Fa-f]{8}\)\)"),
    re.compile(r"^ANGLE \([^,]+, (?P<device>.+?) \(0x[0-9A-Fa-f]{8}\) Direct3D11"),
    re.compile(r"ANGLE Metal Renderer: (?P<device>[^,]+),"),
)

PLATFORM_SLUGS = {"Linux": "linux", "Windows": "windows", "macOS": "macos",
                  "Android": "android", "Chrome OS": "chromeos", "iOS": "ios"}


def die(msg):
    print("build-anchors: %s" % msg, file=sys.stderr)
    raise SystemExit(2)


def load_import_helpers():
    """classify_build, full_version and the software-renderer list live in
    scripts/import-capture.py, which owns the admission door."""
    spec = importlib.util.spec_from_file_location(
        "apostate_capture_import", IMPORT_SCRIPT)
    if spec is None or spec.loader is None:
        die("cannot load %s" % IMPORT_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("classify_build", "full_version", "software_marker",
                 "SOFTWARE_RENDERERS", "SOFTWARE_ADMISSION_NOTE"):
        if not hasattr(module, name):
            die("%s does not export %s" % (IMPORT_SCRIPT.name, name))
    return module


def load_provenance():
    """The tier register's reader. corpus/provenance.py owns that policy."""
    spec = importlib.util.spec_from_file_location(
        "apostate_provenance", PROVENANCE_MODULE)
    if spec is None or spec.loader is None:
        die("cannot load %s" % PROVENANCE_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("load_tiers", "anchor_exception_refusal", "ProvenanceError"):
        if not hasattr(module, name):
            die("%s does not export %s" % (PROVENANCE_MODULE.name, name))
    return module


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def rel(path):
    """Repo-relative when the path is inside the tree, absolute when it is not.

    Inputs are overridable on the command line, so a path outside the checkout
    is a legitimate call and must not raise.
    """
    try:
        return str(pathlib.Path(path).relative_to(REPO))
    except ValueError:
        return str(path)


def dump(path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(canon(obj) + "\n", encoding="utf-8")


def probe(capture, pid, section="probes"):
    entry = (capture.get(section) or {}).get(pid)
    if not entry or not entry.get("ok"):
        return None
    return entry.get("value")


def caps_of(value):
    caps = {k: (value or {}).get(k) for k in CAPS_FIELDS}
    if isinstance(caps.get("parameters"), dict):
        caps["parameters"] = {k: v for k, v in caps["parameters"].items()
                              if k not in PARAMETER_IDENTITY}
    return caps


def identity_of(value):
    out = {k: (value or {}).get(k) for k in IDENTITY_FIELDS}
    parameters = (value or {}).get("parameters")
    if isinstance(parameters, dict):
        out["parameters"] = {k: parameters[k] for k in sorted(PARAMETER_IDENTITY)
                             if k in parameters}
    return out


def identity_parameters_agree(value):
    """getParameter(VENDOR) and the probe's `vendor` field must be one string.

    They are read in the same session from the same context, so a disagreement
    would mean something between them rewrote one of them. Recorded rather than
    assumed.
    """
    parameters = (value or {}).get("parameters") or {}
    return all(parameters.get(name) == (value or {}).get(field)
               for name, field in PARAMETER_IDENTITY.items()
               if name in parameters)


def webgpu_cluster(value):
    """features + limits + adapter vendor/architecture. Adapter description and
    device are identity, not capability, and are dropped with the WebGL names."""
    value = value or {}
    out = {"preferredCanvasFormat": value.get("preferredCanvasFormat"), "adapters": {}}
    for name, adapter in sorted((value.get("adapters") or {}).items()):
        if not isinstance(adapter, dict):
            out["adapters"][name] = None
            continue
        info = adapter.get("info") if isinstance(adapter.get("info"), dict) else {}
        out["adapters"][name] = {
            "architecture": info.get("architecture"),
            "features": adapter.get("features"),
            "limits": adapter.get("limits"),
            "vendor": info.get("vendor"),
        }
    return out


def backend_of(renderer):
    for needle, label, slug in BACKENDS:
        if needle.lower() in (renderer or "").lower():
            return label, slug
    return "unknown", "unknown"


def vendor_of(unmasked_vendor):
    match = re.search(r"\(([^)]+)\)", unmasked_vendor or "")
    name = match.group(1) if match else (unmasked_vendor or "unknown")
    name = name.replace(" Corporation", "").strip()
    return name, re.sub(r"[^a-z0-9]+", "", name.lower()) or "unknown"


def device_of(renderer):
    """The card name, read out of the renderer string it came from."""
    for pattern in DEVICE_PATTERNS:
        match = pattern.search(renderer or "")
        if match:
            device = match.group("device").strip()
            # ANGLE's Vulkan string repeats the vendor: "NVIDIA NVIDIA GeForce ...".
            return re.sub(r"^(\S+) \1 ", r"\1 ", device)
    return renderer or "unknown"


def pci_id_of(renderer):
    match = re.search(r"\(0x([0-9A-Fa-f]{8})\)", renderer or "")
    return match.group(1).lower() if match else None


def admission_record(raw_sha256):
    for directory in ADMISSION_DIRS:
        path = directory / (raw_sha256 + ".json")
        if not path.exists():
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            die("unreadable admission record %s: %s" % (path, exc))
        record["__record_path"] = rel(path)
        return record
    return None


def measure(path, helpers, provenance, tiers, pin):
    raw = path.read_bytes()
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    record = admission_record(raw_sha256)
    try:
        capture = json.loads(raw)
    except ValueError as exc:
        if record is None:
            return None, "unreadable and unadmitted: %s" % exc
        die("%s is admitted but unreadable: %s" % (path.name, exc))

    webgl1 = probe(capture, "webgl1") or {}
    webgl2 = probe(capture, "webgl2") or {}
    canvas = probe(capture, "canvas.2d") or {}
    renderer = webgl1.get("unmaskedRenderer")
    marker = helpers.software_marker(renderer)
    software = bool(marker)
    backend, backend_slug = backend_of(renderer)

    accepted = record is not None and record.get("decision") == "accepted"
    exception = None
    if not accepted:
        # No acceptance to read. The one remaining way in is a named exception
        # in the tier register, and it is checked against the backend MEASURED
        # here rather than against anything the capture or its filename says,
        # so an allowance written for a software rasteriser cannot be spent on
        # a hardware anchor. A capture the register does not mention at all is
        # skipped: absence is not permission.
        why = ("no admission record for sha256 %s" % raw_sha256 if record is None
               else "admission decision is %r" % record.get("decision"))
        try:
            entry = tiers.lookup(path)
        except provenance.ProvenanceError as exc:
            return None, "%s; %s" % (why, exc)
        refusal = provenance.anchor_exception_refusal(entry, backend)
        if refusal:
            return None, "%s; %s" % (why, refusal)
        exception = entry["anchor_exception"]
    elif software and helpers.SOFTWARE_ADMISSION_NOTE not in (record.get("reason") or ""):
        # A software capture admitted through scripts/import-capture.py with
        # --allow-software-renderer carries SOFTWARE_ADMISSION_NOTE in its
        # reason, which means its identity strings are the rasteriser's own and
        # there is no hardware claim to misattribute; anything else still
        # refuses, because that is the llvmpipe-named-as-a-Tesla case the
        # refusal exists for.
        die("%s is admitted but its renderer %r is the software rasteriser %r and "
            "its admission record does not carry the self-consistent-software "
            "allowance; an anchor may not be built from it"
            % (path.name, renderer, marker))

    high = (probe(capture, "navigator.userAgentData") or {}).get("high") or {}
    version = helpers.full_version(capture)
    vendor, vendor_slug = vendor_of(webgl1.get("unmaskedVendor"))
    cluster = webgpu_cluster(probe(capture, "webgpu"))

    repeat = {
        "webgl1_caps": sha(caps_of(probe(capture, "webgl1", "repeat"))) ==
                       sha(caps_of(webgl1)),
        "webgl2_caps": sha(caps_of(probe(capture, "webgl2", "repeat"))) ==
                       sha(caps_of(webgl2)),
        "webgl1_pixels": (probe(capture, "webgl1", "repeat") or {}).get("pixels_sha256")
                         == webgl1.get("pixels_sha256"),
        "webgl2_pixels": (probe(capture, "webgl2", "repeat") or {}).get("pixels_sha256")
                         == webgl2.get("pixels_sha256"),
        "webgpu_cluster": sha(webgpu_cluster(probe(capture, "webgpu", "repeat")))
                          == sha(cluster),
    }

    return {
        "capture": rel(path),
        "capture_name": path.name,
        "capture_sha256": raw_sha256,
        "admission_record": (record["__record_path"] if record is not None
                             else rel(PROVENANCE_TIERS)),
        "admission_reason": (record.get("reason") if accepted else
                             "no admission record; admitted for the %s backend "
                             "only by the anchor exception recorded in %s: %s"
                             % (exception["backend"], rel(PROVENANCE_TIERS),
                                exception["reason"])),
        "label": (capture.get("context") or {}).get("label"),
        "taken_at": (capture.get("context") or {}).get("taken_at"),
        "browser_version": version,
        "build_binding": helpers.classify_build(version, pin),
        "platform": high.get("platform"),
        "backend": backend,
        "backend_slug": backend_slug,
        "software": software,
        "vendor": vendor,
        "vendor_slug": vendor_slug,
        "device": device_of(renderer),
        "pci_id": pci_id_of(renderer),
        "identity": {"webgl1": identity_of(webgl1), "webgl2": identity_of(webgl2)},
        "identity_parameters_agree": {"webgl1": identity_parameters_agree(webgl1),
                                      "webgl2": identity_parameters_agree(webgl2)},
        "caps": {"webgl1": caps_of(webgl1), "webgl2": caps_of(webgl2)},
        "webgpu": cluster,
        "digests": {
            "webgl1_caps_sha256": sha(caps_of(webgl1)),
            "webgl2_caps_sha256": sha(caps_of(webgl2)),
            "webgl1_pixels_sha256": webgl1.get("pixels_sha256"),
            "webgl2_pixels_sha256": webgl2.get("pixels_sha256"),
            "webgpu_cluster_sha256": sha(cluster),
            "canvas_pixels_sha256": canvas.get("pixels_sha256"),
        },
        "repeat_stable": repeat,
    }, None


def compare(members, digest, note=None):
    values = {m["capture_name"]: m["digests"][digest] for m in members}
    distinct = sorted(set(values.values()), key=lambda v: (v is None, v))
    entry = {"digest": digest, "distinct_values": len(distinct),
             "matched": len(distinct) == 1}
    if entry["matched"]:
        entry["value"] = distinct[0]
    else:
        entry["values_by_member"] = values
    if note:
        entry["note"] = note
    return entry


ROTATION_MEASURED = (
    "Every member of this anchor produced the same WebGL1 capability digest, "
    "the same WebGL2 capability digest and the same readback render digest, so "
    "presenting one member's identity strings on another member's capture is "
    "backed by measurement rather than assumed."
)
ROTATION_SINGLE = (
    "This anchor has one member, so there is no measured second card whose "
    "identity strings it may present. Any other renderer string on this "
    "capability cluster is an assertion, not a measurement."
)
WEBGPU_SPLIT_NOTE = (
    "WebGPU is not uniform across this anchor's members and is therefore not "
    "rotatable within it: each member's WebGPU cluster belongs to that member's "
    "host driver stack. The WebGL capability and render digests, which are the "
    "grouping key, did match."
)
CANVAS_NOTE = (
    "Canvas 2D is a font and raster measurement, not a GPU measurement, and is "
    "excluded from the anchor. Recorded so the exclusion can be checked."
)


SOFTWARE_ANCHOR_POLICY = {
    "why_this_anchor_exists": (
        "A host with no usable GPU device renders through a software rasteriser. "
        "Without a software-backend anchor the compositor finds no servable "
        "cluster and the launch inherits the host entirely, losing timezone, "
        "screen, locale and font composition as well as the GPU. This anchor is "
        "the coherent alternative: its identity strings are the rasteriser's own, "
        "so identity and capability agree."
    ),
    "why_the_refusal_exists_and_why_it_does_not_apply": (
        "import-capture.py refuses a software-rasteriser capture by default, and "
        "so does this script, because a capture that NAMES a discrete GPU while "
        "MEASURING a rasteriser would become evidence for that GPU. The two "
        "llvmpipe Tesla captures in probe-host-survey.json are exactly that. A "
        "capture whose identity strings are the rasteriser's own has nothing to "
        "misattribute, so it is admissible with --allow-software-renderer, which "
        "records the allowance in the admission reason. This script reads that "
        "decision instead of re-deriving it."
    ),
    "identity_agrees_with_capability": (
        "The unmasked renderer string names the rasteriser and the capability "
        "cluster is the one that string measured. Nothing about this anchor "
        "claims hardware, and its evidence_class is compatibility-capture rather "
        "than physical-ground-truth for that reason."
    ),
    "not_rotatable": (
        "A software rasteriser's renderer string is the only identity its "
        "capability cluster ever produced. No other identity may be registered "
        "on it in resources/profiles/dispersion/gpu_identity.json."
    ),
    "closes_a_fork_specific_tell": (
        "Patches 0027 and 0038 raise this fork's own SwiftShader limits above "
        "stock's so that a profile's claim can pass the 0026 clamp, and no stock "
        "Chromium build reports those raised values. Serving this anchor claims "
        "the stock figures, and the clamp only ever reduces, so the stock figures "
        "reach the page. Claiming this cluster therefore makes the fork's "
        "software renderer indistinguishable from stock's instead of uniquely "
        "identifiable."
    ),
}


def build_anchor(members, pin):
    members = sorted(members, key=lambda m: m["capture_name"])
    first = members[0]
    key = {d: first["digests"][d] for d in GROUPING_DIGESTS}
    anchor_key = sha(key)
    anchor_id = "%s-%s-%s-%s" % (
        PLATFORM_SLUGS.get(first["platform"], (first["platform"] or "unknown").lower()),
        first["backend_slug"], first["vendor_slug"], anchor_key[:12])

    software = any(m["software"] for m in members)
    if software and not all(m["software"] for m in members):
        die("anchor %s mixes software and hardware captures; a rasteriser and a "
            "GPU are not one capability cluster" % anchor_id)

    backends = sorted({m["backend"] for m in members})
    platforms = sorted({m["platform"] for m in members})
    if len(backends) > 1 or len(platforms) > 1:
        die("anchor %s spans backends %s / platforms %s; identical capability "
            "digests across graphics backends is a finding, not a grouping"
            % (anchor_id, backends, platforms))

    webgpu_variants = {}
    for member in members:
        webgpu_variants.setdefault(member["digests"]["webgpu_cluster_sha256"], {
            "members": [], "sha256": member["digests"]["webgpu_cluster_sha256"],
            "cluster": member["webgpu"]})["members"].append(member["capture_name"])
    variants = sorted(webgpu_variants.values(), key=lambda v: v["sha256"])
    for variant in variants:
        variant["members"] = sorted(variant["members"])

    comparisons = [compare(members, d) for d in GROUPING_DIGESTS]
    comparisons.append(compare(members, "webgpu_cluster_sha256",
                               None if len(variants) == 1 else WEBGPU_SPLIT_NOTE))
    comparisons.append(compare(members, "canvas_pixels_sha256", CANVAS_NOTE))
    grouped_matched = all(c["matched"] for c in comparisons[:len(GROUPING_DIGESTS)])

    versions = sorted({m["browser_version"] for m in members})
    bindings = sorted({m["build_binding"] for m in members})

    cluster = {
        "webgl1": first["caps"]["webgl1"],
        "webgl2": first["caps"]["webgl2"],
        "render": {"webgl1_pixels_sha256": first["digests"]["webgl1_pixels_sha256"],
                   "webgl2_pixels_sha256": first["digests"]["webgl2_pixels_sha256"]},
        "webgpu": {"uniform": len(variants) == 1, "variants": variants},
    }

    anchor = {
        "schema": ANCHOR_SCHEMA,
        "anchor_id": anchor_id,
        "anchor_key_sha256": anchor_key,
        "anchor_sha256": sha(cluster),
        "platform": first["platform"],
        "backend": first["backend"],
        "vendor": first["vendor"],
        "evidence_tier": "T0",
        # A software anchor is a real measurement of a real machine, but it is a
        # machine only a GPU-less host is, so it is not ground truth for any
        # hardware claim. compatibility-capture says exactly that.
        "evidence_class": ("compatibility-capture" if software
                           else "physical-ground-truth"),
        "generated_by": "scripts/build-anchors.py",
        "release_pin": pin,
        "browser_versions": versions,
        "build_bindings": bindings,
        "capability_cluster": cluster,
        "digests": {
            "webgl1_caps_sha256": first["digests"]["webgl1_caps_sha256"],
            "webgl2_caps_sha256": first["digests"]["webgl2_caps_sha256"],
            "webgl1_pixels_sha256": first["digests"]["webgl1_pixels_sha256"],
            "webgl2_pixels_sha256": first["digests"]["webgl2_pixels_sha256"],
            "webgpu_cluster_sha256": [v["sha256"] for v in variants],
        },
        "members": [{
            "capture": m["capture"],
            "capture_sha256": m["capture_sha256"],
            "admission_record": m["admission_record"],
            "label": m["label"],
            "taken_at": m["taken_at"],
            "device": m["device"],
            "pci_id": m["pci_id"],
            "browser_version": m["browser_version"],
            "build_binding": m["build_binding"],
            "identity": m["identity"],
            "identity_parameters_agree": m["identity_parameters_agree"],
            "webgpu_cluster_sha256": m["digests"]["webgpu_cluster_sha256"],
            "canvas_pixels_sha256": m["digests"]["canvas_pixels_sha256"],
            "repeat_stable": m["repeat_stable"],
        } for m in members],
        "identity_rotation": {
            "within_anchor": {
                "status": "measured-safe" if len(members) > 1 else "single-member",
                "reason": ROTATION_MEASURED if len(members) > 1 else ROTATION_SINGLE,
                "member_count": len(members),
                "rotatable_strings": [{
                    "capture": m["capture_name"],
                    "device": m["device"],
                    "webgl1": m["identity"]["webgl1"],
                    "webgl2": m["identity"]["webgl2"],
                } for m in members],
            },
        },
        "equivalence": {
            "grouping_digests": list(GROUPING_DIGESTS),
            "member_count": len(members),
            "comparisons": comparisons,
            "matched": grouped_matched,
            "all_digests_matched": all(c["matched"] for c in comparisons),
        },
    }
    if any(m["build_binding"] == "off-major" for m in members):
        anchor["build_caveat"] = (
            "Measured on Chromium %s against release pin %s. Capability tables "
            "are build-bound, so equality of this cluster to a pinned-build "
            "capture is not established by this evidence."
            % ("/".join(versions), pin))
    elif any(m["build_binding"] != "release" for m in members):
        anchor["build_caveat"] = (
            "Measured on Chromium %s against release pin %s: same major, "
            "different patch. Version-bearing fields will differ from a "
            "pinned-build reference and a V3 run must report that separately."
            % ("/".join(versions), pin))
    if software:
        anchor["software_anchor_policy"] = dict(SOFTWARE_ANCHOR_POLICY)
    return anchor


CROSS_BACKEND_REASON = (
    "Presenting this anchor's identity strings on the %s capability cluster is "
    "not supported by measurement: the two clusters' WebGL digests differ, so "
    "the swap would advertise a capability surface that was never measured "
    "behind that name."
)
SAME_BACKEND_REASON = (
    "This anchor and %s share a graphics backend but not a capability cluster, "
    "so their identity strings are not interchangeable."
)
CONFOUNDED_MAJOR = (
    "The two anchors were measured on different Chromium majors (%s against "
    "%s), so the measured inequality is real but its cause is not isolated to "
    "the graphics backend."
)
CONFOUNDED_PATCH = (
    "The two anchors were measured on different Chromium patch builds (%s "
    "against %s), so the measured inequality is real but a build difference is "
    "not excluded as part of its cause."
)


def build_confound(this_versions, other_versions):
    if set(this_versions) == set(other_versions):
        return None
    mine = "/".join(this_versions)
    theirs = "/".join(other_versions)
    majors = {v.split(".")[0] for v in this_versions} | \
             {v.split(".")[0] for v in other_versions}
    template = CONFOUNDED_MAJOR if len(majors) > 1 else CONFOUNDED_PATCH
    return template % (mine, theirs)


def link_anchors(anchors):
    """Cross-anchor claims. Every one of them is a digest comparison."""
    by_id = {a["anchor_id"]: a for a in anchors}
    for anchor in anchors:
        cross = []
        shared_render = []
        for other_id in sorted(by_id):
            if other_id == anchor["anchor_id"]:
                continue
            other = by_id[other_id]
            same_backend = other["backend"] == anchor["backend"]
            reason = (SAME_BACKEND_REASON % other_id) if same_backend \
                else (CROSS_BACKEND_REASON % other["backend"])
            entry = {
                "anchor_id": other_id,
                "platform": other["platform"],
                "backend": other["backend"],
                "status": "asserted_not_measured",
                "reason": reason,
                "evidence": {
                    "webgl1_caps_sha256": {
                        "this": anchor["digests"]["webgl1_caps_sha256"],
                        "other": other["digests"]["webgl1_caps_sha256"],
                        "matched": anchor["digests"]["webgl1_caps_sha256"] ==
                                   other["digests"]["webgl1_caps_sha256"]},
                    "webgl2_caps_sha256": {
                        "this": anchor["digests"]["webgl2_caps_sha256"],
                        "other": other["digests"]["webgl2_caps_sha256"],
                        "matched": anchor["digests"]["webgl2_caps_sha256"] ==
                                   other["digests"]["webgl2_caps_sha256"]},
                    "webgl1_pixels_sha256": {
                        "this": anchor["digests"]["webgl1_pixels_sha256"],
                        "other": other["digests"]["webgl1_pixels_sha256"],
                        "matched": anchor["digests"]["webgl1_pixels_sha256"] ==
                                   other["digests"]["webgl1_pixels_sha256"]},
                },
            }
            confound = build_confound(anchor["browser_versions"],
                                      other["browser_versions"])
            if confound:
                entry["confounded_by_build"] = confound
            cross.append(entry)
            if anchor["digests"]["webgl1_pixels_sha256"] == \
                    other["digests"]["webgl1_pixels_sha256"]:
                shared_render.append(other_id)
        anchor["identity_rotation"]["cross_backend"] = cross
        anchor["render_digest_discrimination"] = {
            "webgl1_pixels_sha256": anchor["digests"]["webgl1_pixels_sha256"],
            "also_produced_by": shared_render,
            "discriminating": not shared_render,
            "note": (
                "The collector's WebGL scene is an analytic mediump gradient, so "
                "implementations that agree on it to 8 bits produce one digest. "
                "This render digest is shared with %s, whose capability tables "
                "differ, so the render digest alone does not separate these "
                "anchors: the WebGL capability digests do."
                % ", ".join(shared_render)) if shared_render else
                "No other anchor produced this render digest.",
        }


def check_survey(measurements, survey_path):
    """Re-measure the probe host's projection against local captures.

    scripts/capture-digest-survey.py computes the same digests on the host with
    a separate implementation. Any capture present in both must agree, or one of
    the two is wrong and the discard evidence cannot be trusted.
    """
    if not survey_path.exists():
        return None, []
    try:
        survey = json.loads(survey_path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die("unreadable survey %s: %s" % (survey_path, exc))
    rows = {row["sha256"]: row for row in survey.get("captures", [])}
    checked = 0
    for measurement in measurements:
        row = rows.get(measurement["capture_sha256"])
        if row is None:
            continue
        for digest in ("webgl1_caps_sha256", "webgl2_caps_sha256",
                       "webgl1_pixels_sha256", "webgl2_pixels_sha256",
                       "webgpu_cluster_sha256", "canvas_pixels_sha256"):
            if row.get(digest) != measurement["digests"][digest]:
                die("survey and local measurement disagree on %s for %s: %r vs %r"
                    % (digest, measurement["capture_name"], row.get(digest),
                       measurement["digests"][digest]))
        checked += 1
    admitted = {m["capture_sha256"] for m in measurements}
    discarded = sorted((row for row in survey.get("captures", [])
                        if row["sha256"] not in admitted),
                       key=lambda row: (row.get("label") or "", row["file"]))
    return {"survey": survey, "cross_checked": checked}, discarded


README_HEAD = """# GPU anchors

An anchor is the atomic GPU capability cluster: WebGL1 and WebGL2
`extensions`, `parameters`, `precision`, `contextAttributes` and
`antialiasSamples`, the readback render digest, and WebGPU features, limits and
adapter vendor/architecture — with every identity string
(`unmaskedVendor`, `unmaskedRenderer`, `vendor`, `renderer`, `version`,
`shadingLanguageVersion`) lifted out into a separate, rotatable list. The
collector queries every enumerable GL constant, so `parameters` carries those
same four masked strings again under `VENDOR`, `RENDERER`, `VERSION` and
`SHADING_LANGUAGE_VERSION`; those keys leave the cluster with the rest of the
identity rather than being hashed into it.

Anchors are grouped by measured digest equality, never by label. Generated by
`scripts/build-anchors.py` from captures under `resources/fingerprints/raw/`
that carry an `accepted` admission record; re-running it reproduces these files
byte for byte.

## Anchors

"""

README_ROTATION = """
## What rotation the evidence supports

Within an anchor with more than one member card, identity-string rotation is
**measured-safe**: every member produced the same WebGL1 capability digest, the
same WebGL2 capability digest and the same readback render digest, so one
member's renderer string sits on a capability surface that was measured behind
another member's.

Across anchors it is **`asserted_not_measured`**, and the table below is the
reason. Every pair differs on the capability digests, so presenting one
anchor's renderer string on another's capability cluster would advertise a
surface that was never measured behind that name. Where the two anchors were
also captured on different Chromium builds, the inequality is real but its
cause is not isolated to the graphics backend, and that is stated per pair.

"""

README_CATALOGUE = """
## What the anchors back

`resources/profiles/dispersion/gpu_identity.json` selects a GPU identity string
conditioned on the resolved anchor. This section is the check that it never
offers a string these anchors did not measure: every option's unmasked vendor
and renderer pair must belong to a member of the anchor it is keyed on, and
every measured member should be reachable. This file does not modify the
dispersion table; it only measures it.

"""

README_RETIRED = """
### The retired family catalogue

A fourteen-entry family catalogue under `resources/profiles` preceded the
dispersion table and has been removed. It is recorded here because the
measurement that condemned it is not re-derivable once the input is gone,
and because the same collapse shows up in the discarded captures below.

All fourteen entries carried one `gl_limits` table and one `gl_extensions`
list between them, so at most one anchor's measurements could agree with both.
The shared table was `MAX_FRAGMENT_UNIFORM_VECTORS` 1024,
`MAX_RENDERBUFFER_SIZE` 16384, `MAX_TEXTURE_SIZE` 16384 and
`MAX_VERTEX_UNIFORM_VECTORS` 1024. Measured against the anchors:

| anchor | measured 1024/16384/16384/1024? |
|---|---|
| `macos-metal-apple-850a91233555` | yes, exactly |
| `linux-vulkan-nvidia-adf287b8f0ee` | no: 4096 / 32768 / 32768 / 4096, all four wrong |
| `windows-d3d11-intel-79dfeb5b4f99` | no: `MAX_VERTEX_UNIFORM_VECTORS` is 4096 |
| `windows-d3d11-nvidia-0947761dfbe9` | no: `MAX_VERTEX_UNIFORM_VECTORS` is 4095 |

So the one shared capability table was the Apple M4 Max's numbers presented as
fourteen devices. The shared extension list was not a real context's list
either: it named `EXT_color_buffer_float` and
`EXT_disjoint_timer_query_webgl2`, absent from every measured WebGL1 list, and
`ANGLE_instanced_arrays`, `EXT_blend_minmax` and `OES_element_index_uint`,
absent from every measured WebGL2 list — a union no single context returns.

The WebGPU architecture strings were falsifiable on their own. The five Apple
entries claimed `m2`, `m2-pro`, `m3-pro`, `m4` and `m5`; the measured Apple
adapter reports `metal-3`, and Chromium emits no per-chip Apple architecture at
all. The Intel gen-9 entry claimed `gen9` where the adapter reports `gen-9`,
and its renderer string dropped the measured PCI id `(0x00009BC8)`.

"""

README_DISCARD = """
## Discarded captures

These captures exist on the probe host and are not anchors. The measurement is
in `probe-host-survey.json`, produced by `scripts/capture-digest-survey.py`,
which projects each capture on the host to its digests without transferring any
probe payload.

"""

README_PASCAL = """
## Pascal and Turing have no physical anchor

There is no anchor for NVIDIA Pascal or Turing silicon, and there is no partial
evidence for one either.

The two captures on the probe host whose labels name those generations —
`linux-nvidia-tesla-t4` (Turing) and `linux-nvidia-tesla-p100-pcie-16gb`
(Pascal) — both report the renderer
`ANGLE (Mesa, llvmpipe (LLVM 15.0.7 256 bits), OpenGL 4.5)`. llvmpipe is Mesa's
CPU rasteriser. Neither capture measured a GPU: both measured the same software
renderer on a host whose driver stack never reached the card, which is why they
hash equal to each other on every WebGL digest despite naming different silicon.

A software-rasteriser capture is a real measurement of a real machine and it is
not a machine any profile may claim, so `scripts/import-capture.py` refuses it
at admission and `scripts/build-anchors.py` refuses to build an anchor from one.
Closing Pascal or Turing needs a capture from a host with those cards' drivers
loaded.
"""


def slug(value):
    return re.sub(r"[^a-z0-9]+", "", (value or "").lower())


def dispersion_check(anchors, path):
    """Does the dispersion table only offer identity strings we measured?

    `gpu_identity.json` is conditioned on anchor id and its options carry the
    unmasked vendor and renderer pair a profile will present. An option naming
    a string no member of that anchor produced would be a cross-anchor swap
    wearing an anchor's name, which is the failure the retired family catalogue
    shipped. Read-only: this reports, it does not rewrite the table.
    """
    if not path.is_file():
        return None
    try:
        table = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die("unreadable dispersion table %s: %s" % (path, exc))

    by_id = {a["anchor_id"]: a for a in anchors}
    rows, problems = [], []
    option_sets = table.get("option_sets")
    if not isinstance(option_sets, list):
        die("%s has no option_sets array" % path)

    for option_set in option_sets:
        anchor_id = ((option_set.get("key") or {}).get("anchor"))
        anchor = by_id.get(anchor_id)
        options = option_set.get("options") or []
        measured = {}
        if anchor is not None:
            measured = {(m["identity"]["webgl1"]["unmaskedVendor"],
                         m["identity"]["webgl1"]["unmaskedRenderer"]): m["device"]
                        for m in anchor["members"]}
        offered, unmeasured = [], []
        for option in options:
            gpu = ((option.get("value") or {}).get("gpu") or {})
            pair = (gpu.get("unmasked_vendor"), gpu.get("unmasked_renderer"))
            if anchor is not None and pair in measured:
                offered.append(measured[pair])
            else:
                unmeasured.append(option.get("id") or pair[1])
        if anchor is None:
            problems.append("option set keyed on unknown anchor %r" % (anchor_id,))
        for name in unmeasured:
            problems.append("anchor %s offers unmeasured identity %r"
                            % (anchor_id, name))
        not_offered = sorted(set(measured.values()) - set(offered))
        rows.append({
            "anchor_id": anchor_id,
            "anchor_known": anchor is not None,
            "option_count": len(options),
            "offered": sorted(offered),
            "unmeasured": sorted(unmeasured),
            "measured_not_offered": not_offered,
        })

    covered = {row["anchor_id"] for row in rows}
    missing = sorted(set(by_id) - covered)
    return {"path": rel(path), "rows": rows,
            "problems": problems, "anchors_without_option_set": missing,
            "conditioned_on": table.get("conditioned_on")}


def short(digest, width=12):
    return (digest or "-")[:width]


def readme(anchors, discarded, survey_info, pin, software, catalogue):
    out = [README_HEAD]
    out.append("| anchor id | platform / backend | member cards | webgl1 caps | "
               "webgl2 caps | webgl render | webgpu | tier | capture sha256 |\n")
    out.append("|---|---|---|---|---|---|---|---|---|\n")
    for anchor in anchors:
        cards = "<br>".join(m["device"] for m in anchor["members"])
        captures = "<br>".join("`%s`" % short(m["capture_sha256"])
                               for m in anchor["members"])
        webgpu = "1 cluster" if anchor["capability_cluster"]["webgpu"]["uniform"] \
            else "%d clusters" % len(anchor["capability_cluster"]["webgpu"]["variants"])
        out.append("| `%s` | %s / %s | %s | `%s` | `%s` | `%s` | %s | %s | %s |\n" % (
            anchor["anchor_id"], anchor["platform"], anchor["backend"], cards,
            short(anchor["digests"]["webgl1_caps_sha256"]),
            short(anchor["digests"]["webgl2_caps_sha256"]),
            short(anchor["digests"]["webgl1_pixels_sha256"]),
            webgpu, anchor["evidence_tier"], captures))

    out.append("\nFull digests, and the equality that defines each group:\n\n")
    for anchor in anchors:
        out.append("### `%s`\n\n" % anchor["anchor_id"])
        out.append("- `webgl1_caps_sha256` `%s`\n" % anchor["digests"]["webgl1_caps_sha256"])
        out.append("- `webgl2_caps_sha256` `%s`\n" % anchor["digests"]["webgl2_caps_sha256"])
        out.append("- `webgl1_pixels_sha256` `%s`\n" % anchor["digests"]["webgl1_pixels_sha256"])
        out.append("- `webgl2_pixels_sha256` `%s`\n" % anchor["digests"]["webgl2_pixels_sha256"])
        for variant in anchor["capability_cluster"]["webgpu"]["variants"]:
            adapters = variant["cluster"]["adapters"]
            high = adapters.get("high-performance")
            summary = "no adapter" if high is None else "%s / %s, %d features, %d limits" % (
                high.get("vendor"), high.get("architecture"),
                len(high.get("features") or []), len(high.get("limits") or {}))
            out.append("- `webgpu_cluster_sha256` `%s` — %s (%s)\n" % (
                variant["sha256"], summary, ", ".join(variant["members"])))
        out.append("- browser build %s (%s), release pin %s\n" % (
            "/".join(anchor["browser_versions"]),
            "/".join(anchor["build_bindings"]), pin))
        if anchor.get("build_caveat"):
            out.append("- %s\n" % anchor["build_caveat"])
        if not anchor["render_digest_discrimination"]["discriminating"]:
            out.append("- %s\n" % anchor["render_digest_discrimination"]["note"])
        out.append("- grouping digests matched across all %d member(s): %s\n" % (
            anchor["equivalence"]["member_count"],
            "yes" if anchor["equivalence"]["matched"] else "no"))
        for comparison in anchor["equivalence"]["comparisons"]:
            if not comparison["matched"] and comparison.get("note"):
                out.append("- `%s` did not match across members: %s\n" % (
                    comparison["digest"], comparison["note"]))
        out.append("\n")

    out.append(README_ROTATION)
    out.append("| from | to | webgl1 caps | webgl2 caps | webgl render | "
               "status | build confound |\n")
    out.append("|---|---|---|---|---|---|---|\n")
    for anchor in anchors:
        for entry in anchor["identity_rotation"]["cross_backend"]:
            evidence = entry["evidence"]
            out.append("| `%s` | `%s` | %s | %s | %s | `%s` | %s |\n" % (
                anchor["anchor_id"], entry["anchor_id"],
                "equal" if evidence["webgl1_caps_sha256"]["matched"] else
                "`%s` vs `%s`" % (short(evidence["webgl1_caps_sha256"]["this"]),
                                  short(evidence["webgl1_caps_sha256"]["other"])),
                "equal" if evidence["webgl2_caps_sha256"]["matched"] else
                "`%s` vs `%s`" % (short(evidence["webgl2_caps_sha256"]["this"]),
                                  short(evidence["webgl2_caps_sha256"]["other"])),
                "equal" if evidence["webgl1_pixels_sha256"]["matched"] else
                "`%s` vs `%s`" % (short(evidence["webgl1_pixels_sha256"]["this"]),
                                  short(evidence["webgl1_pixels_sha256"]["other"])),
                entry["status"],
                "major" if "majors" in (entry.get("confounded_by_build") or "")
                else "patch" if entry.get("confounded_by_build") else "no"))

    if catalogue:
        out.append(README_CATALOGUE)
        out.append("Table `%s`, conditioned on %s.\n\n" % (
            catalogue["path"],
            ", ".join("`%s`" % k for k in (catalogue["conditioned_on"] or []))
            or "nothing"))
        out.append("| anchor id | options offered | every offered string "
                   "measured | measured members not offered |\n")
        out.append("|---|---|---|---|\n")
        for row in catalogue["rows"]:
            out.append("| %s | %d: %s | %s | %s |\n" % (
                "`%s`" % row["anchor_id"] if row["anchor_known"]
                else "**unknown anchor** `%s`" % row["anchor_id"],
                row["option_count"],
                ", ".join(row["offered"]) or "none",
                "yes" if not row["unmeasured"]
                else "**no: %s**" % ", ".join(row["unmeasured"]),
                ", ".join(row["measured_not_offered"]) or "none"))
        if catalogue["anchors_without_option_set"]:
            out.append("\nAnchors with no option set, so not selectable: %s\n"
                       % ", ".join("`%s`" % a for a in
                                   catalogue["anchors_without_option_set"]))
        if catalogue["problems"]:
            out.append("\n**Unbacked offers:**\n\n")
            for problem in catalogue["problems"]:
                out.append("- %s\n" % problem)
            out.append("\n")
        else:
            out.append("\nNo offer in the table names an identity string these "
                       "anchors did not measure.\n")
        out.append(README_RETIRED)
    out.append(README_DISCARD)
    if survey_info is None:
        out.append("`probe-host-survey.json` is absent; run "
                   "`scripts/capture-digest-survey.py` to regenerate it.\n")
    else:
        survey = survey_info["survey"]
        out.append("Host `%s`, directory `%s`, %d captures surveyed, %d of them "
                   "admitted and re-measured locally with matching digests.\n\n" % (
                       survey["host"], survey["dir"], len(survey["captures"]),
                       survey_info["cross_checked"]))
        clusters = {}
        for row in discarded:
            key = (row.get("webgl1_caps_sha256"), row.get("webgl2_caps_sha256"),
                   row.get("webgl1_pixels_sha256"), row.get("canvas_pixels_sha256"))
            clusters.setdefault(key, []).append(row)
        admitted_by_key = {}
        for anchor in anchors:
            for member in anchor["members"]:
                admitted_by_key[(anchor["digests"]["webgl1_caps_sha256"],
                                 anchor["digests"]["webgl2_caps_sha256"],
                                 anchor["digests"]["webgl1_pixels_sha256"],
                                 member["canvas_pixels_sha256"])] = (anchor, member)

        canvas_by_anchor = {}
        for anchor in anchors:
            for member in anchor["members"]:
                canvas_by_anchor.setdefault(member["canvas_pixels_sha256"],
                                            (anchor, member))

        out.append("### The collision\n\n")
        out.append("| captures | claimed silicon | webgl1 caps | webgl2 caps | "
                   "webgl render | canvas render | collides with |\n")
        out.append("|---|---|---|---|---|---|---|\n")
        for key in sorted(clusters, key=lambda k: (-len(clusters[k]), str(k))):
            group = clusters[key]
            hit = admitted_by_key.get(key)
            collides = "admitted `%s` (%s)" % (hit[0]["anchor_id"], hit[1]["device"]) \
                if hit else "no admitted capture"
            renderers = sorted({
                (row["identity"]["webgl1"].get("unmaskedRenderer") or "null")
                for row in group})
            vendors = sorted({re.sub(r"^ANGLE \(([^,]+),.*$", r"\1", r)
                              for r in renderers})
            claimed = "%d distinct renderer string(s): %s" % (
                len(renderers), ", ".join(vendors))
            out.append("| %d: %s | %s | `%s` | `%s` | `%s` | `%s` | %s |\n" % (
                len(group),
                ", ".join("`%s`" % (row.get("label") or row["sha256"][:12])
                          for row in group),
                claimed, short(key[0]), short(key[1]), short(key[2]),
                short(key[3]), collides))

        biggest = max((len(g) for k, g in clusters.items()
                       if k in admitted_by_key), default=0)
        out.append("\nA capability digest is a hash of what a GPU says it can do and a "
                   "render digest is a hash of what it drew. Two captures that agree on "
                   "both while naming different silicon were taken on one machine: the "
                   "renderer string was changed and nothing behind it was.")
        if biggest:
            out.append(" The largest such row holds %d captures and collides with an "
                       "admitted capture, so the machine they were all taken on is "
                       "named rather than inferred." % biggest)
        out.append("\n\n")
        out.append("### Every discarded capture\n\n")
        out.append("| capture sha256 | label | claimed renderer | build | "
                   "webgl1 caps | webgl render | canvas render | why not an anchor |\n")
        out.append("|---|---|---|---|---|---|---|---|\n")
        for row in discarded:
            renderer = row["identity"]["webgl1"].get("unmaskedRenderer")
            reasons = []
            if renderer and any(s.lower() in renderer.lower() for s in software):
                reasons.append("software rasteriser")
            if not renderer:
                reasons.append("no WebGL renderer string, so no GPU to anchor")
            if not row.get("browser_version"):
                reasons.append("no Chromium browser identity")
            key = (row.get("webgl1_caps_sha256"), row.get("webgl2_caps_sha256"),
                   row.get("webgl1_pixels_sha256"), row.get("canvas_pixels_sha256"))
            peers = len(clusters.get(key, [])) - 1
            if key in admitted_by_key:
                reasons.append("every digest identical to admitted `%s`"
                               % admitted_by_key[key][1]["device"])
            elif peers == 1:
                reasons.append("every digest identical to 1 other capture naming "
                               "other silicon")
            elif peers > 1:
                reasons.append("every digest identical to %d other captures naming "
                               "other silicon" % peers)
            canvas_hit = canvas_by_anchor.get(row.get("canvas_pixels_sha256"))
            if canvas_hit and key not in admitted_by_key:
                reasons.append("canvas render identical to admitted `%s`, so "
                               "measured on that host" % canvas_hit[1]["device"])
            if row.get("capture_version") != 2:
                reasons.append("capture version %r" % row.get("capture_version"))
            if row.get("headed") is not True:
                reasons.append("headed %r" % row.get("headed"))
            if row.get("failed_probes"):
                reasons.append("%d failed probe(s)" % len(row["failed_probes"]))
            if row.get("browser_version") and \
                    row["browser_version"].split(".")[0] != pin.split(".")[0]:
                reasons.append("Chromium %s against pin %s"
                               % (row["browser_version"], pin))
            if not reasons:
                reasons.append("not imported as a physical reference")
            out.append("| `%s` | `%s` | %s | %s | `%s` | `%s` | `%s` | %s |\n" % (
                row["sha256"][:12], row.get("label"),
                renderer or "null", row.get("browser_version") or "-",
                short(row.get("webgl1_caps_sha256")),
                short(row.get("webgl1_pixels_sha256")),
                short(row.get("canvas_pixels_sha256")), "; ".join(reasons)))
    out.append(README_PASCAL)
    return "".join(out)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    ap.add_argument("--raw", type=pathlib.Path, default=RAW_DIR)
    ap.add_argument("--survey", type=pathlib.Path, default=SURVEY_PATH)
    ap.add_argument("--no-readme", action="store_true")
    ap.add_argument("--dispersion", type=pathlib.Path, default=DISPERSION_PATH,
                    help="gpu identity dispersion table to check; read-only")
    args = ap.parse_args()

    helpers = load_import_helpers()
    provenance = load_provenance()
    try:
        tiers = provenance.load_tiers(PROVENANCE_TIERS)
    except provenance.ProvenanceError as exc:
        die("%s" % exc)
    software = tuple(helpers.SOFTWARE_RENDERERS)
    try:
        pin = PIN_PATH.read_text(encoding="utf-8").strip()
    except OSError as exc:
        die("cannot read %s: %s" % (PIN_PATH, exc))

    measurements, skipped = [], []
    for path in sorted(args.raw.glob("*.json")):
        measurement, reason = measure(path, helpers, provenance, tiers, pin)
        if measurement is None:
            skipped.append((path.name, reason))
            continue
        measurements.append(measurement)
    if not measurements:
        die("no admitted captures under %s" % args.raw)

    groups = {}
    for measurement in measurements:
        key = tuple(measurement["digests"][d] for d in GROUPING_DIGESTS)
        groups.setdefault(key, []).append(measurement)
    anchors = sorted((build_anchor(members, pin) for members in groups.values()),
                     key=lambda a: a["anchor_id"])
    link_anchors(anchors)

    survey_info, discarded = check_survey(measurements, args.survey)
    catalogue = dispersion_check(anchors, args.dispersion)

    args.out.mkdir(parents=True, exist_ok=True)
    written = set()
    for anchor in anchors:
        path = args.out / (anchor["anchor_id"] + ".json")
        dump(path, anchor)
        written.add(path.name)
    for path in sorted(args.out.glob("*.json")):
        if path.name in written:
            continue
        try:
            stale = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if isinstance(stale, dict) and stale.get("schema") == ANCHOR_SCHEMA:
            path.unlink()
            print("pruned stale anchor %s" % path.name)

    if not args.no_readme:
        (args.out / "README.md").write_text(
            readme(anchors, discarded, survey_info, pin, software, catalogue),
            encoding="utf-8")

    for name, reason in skipped:
        print("skipped %s: %s" % (name, reason))
    for anchor in anchors:
        print("%-44s %d member(s) caps=%s render=%s webgpu=%s" % (
            anchor["anchor_id"], len(anchor["members"]),
            short(anchor["digests"]["webgl1_caps_sha256"]),
            short(anchor["digests"]["webgl1_pixels_sha256"]),
            "uniform" if anchor["capability_cluster"]["webgpu"]["uniform"]
            else "%d clusters" % len(anchor["capability_cluster"]["webgpu"]["variants"])))
    print("%d anchor(s), %d admitted capture(s), %d discarded on the probe host"
          % (len(anchors), len(measurements), len(discarded)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
