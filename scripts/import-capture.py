#!/usr/bin/env python3
"""Import a physical-device capture from the probe host into the admitted corpus.

The probe receiver writes what a device emitted to
`/var/lib/apostate-probe/captures/<sha256>.json` and records its own admission
decision beside it. That host is not the corpus: a capture only becomes
reference material once this repository has re-derived admission from the file
itself. This script is that step, and it is the only way a file arrives in
`resources/fingerprints/raw/`.

What it enforces, in order, refusing on the first failure:

1. **Bytes are the evidence.** The file is copied verbatim and its sha256 must
   equal the name the receiver gave it. Nothing is reformatted, so the digest
   in the admission record addresses the exact bytes in the tree.
2. **Schema.** `capture/schema/capture.schema.json`, capture version 2.
3. **The existing admission gate**, `validate_capture` from
   `scripts/decompose-capture.py`, unmodified. Secure context, no automation
   signals, every probe `ok`, every deterministic probe read twice, and an
   internally coherent Chromium browser identity.
4. **Headed.** The schema and the shared gate both tolerate a null `headed`,
   because a receiver cannot always tell. Admission here does not: a capture
   that cannot say a real window was on screen is not reference material.
5. **Collector digest.** `context.collector_sha256` must equal the sha256 of
   this repository's own `capture/collector/collector.js`. A capture measured
   by a collector we do not have measured something we cannot describe.
6. **Hardware renderer.** A software or virtual rasteriser is a real
   measurement of a real machine and is not a machine any profile may claim.
   Refused at the door rather than filtered later, so it can never reach an
   anchor.

Browser build is classified, never quietly accepted. `docs/METHODOLOGY.md`
binds a capture to the build that produced it, so the build relationship to
`build/CHROMIUM_VERSION` is written into the admission reason:

  `release`    — exactly the pinned build; a valid V3 target.
  `same-major` — same Chromium major, different patch. Admitted. Version-bearing
                 fields will differ from a pinned-build reference and a V3 run
                 must report that separately, exactly as §"Historical
                 build-binding evidence" describes.
  `off-major`  — a different Chromium major. Admitted only with
                 `--allow-off-major-build`, and never a valid V3 target for the
                 pinned build. The capability evidence still stands; the browser
                 identity does not transfer.

Note that the major used for the internal coherence check in step 3 is the
capture's own observed major, not the pinned one. That keeps every other check
in the shared gate exactly as written — user agent, brand list and
`uaFullVersion` must still agree with each other — while moving the single
question "is this the build we ship?" out of a boolean refusal and into a
recorded classification.

Usage:
    scripts/import-capture.py --manifest resources/fingerprints/import-manifest.json
    scripts/import-capture.py --as NAME [--from-host USER@HOST] SOURCE
    scripts/import-capture.py --as NAME --local path/to/capture.json
"""

import argparse
import contextlib
import datetime
import hashlib
import importlib.util
import json
import os
import pathlib
import re
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
RAW_DIR = REPO / "resources" / "fingerprints" / "raw"
SCHEMA_PATH = REPO / "capture" / "schema" / "capture.schema.json"
COLLECTOR_PATH = REPO / "capture" / "collector" / "collector.js"
PIN_PATH = REPO / "build" / "CHROMIUM_VERSION"
ADMISSION_SCRIPT = REPO / "scripts" / "decompose-capture.py"

DEFAULT_HOST = os.environ.get("APOSTATE_PROBE_HOST", "apostate@40.87.20.34")
DEFAULT_REMOTE_DIR = os.environ.get(
    "APOSTATE_PROBE_CAPTURES", "/var/lib/apostate-probe/captures")

# <device-description>-<basic ISO 8601 UTC>Z, matching the two captures already
# admitted. The timestamp is checked against context.taken_at so a descriptive
# name cannot drift from the session it describes.
NAME_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*-(\d{8}T\d{6})Z$")

# Renderer substrings that mean "no GPU was involved". Same set
# scripts/decompose-capture.py refuses to build a gpu block from; defined here
# because admission is the earlier gate, and imported from there by
# scripts/build-anchors.py so the list has exactly one owner.
SOFTWARE_RENDERERS = (
    "Basic Render Driver", "Basic Display", "SwiftShader", "llvmpipe",
    "softpipe", "VMware", "VirtualBox", "Parallels",
    "Microsoft Remote Display", "Mesa OffScreen",
)

# The one case in which a software-rasteriser capture is admissible.
#
# The refusal below exists to stop a capture that NAMES a discrete GPU while
# MEASURING a rasteriser from becoming evidence for that GPU: the two llvmpipe
# Tesla captures in corpus/anchors/probe-host-survey.json are exactly that, and
# every WebGL digest they produced belongs to llvmpipe, not to a P100 or a T4.
#
# It is the wrong answer for a capture whose identity strings are the software
# renderer's OWN. There is no misattribution to prevent there, and a host with
# no usable GPU device really does render through SwiftShader, so that machine
# is claimable -- by exactly the host that is one. Without such a capture a
# GPU-less host has no servable anchor at all and inherits every surface, which
# is worse for the user than a coherent software identity.
#
# The allowance is recorded in the admission reason rather than inferred later,
# so scripts/build-anchors.py can tell the two cases apart by reading the
# decision instead of re-litigating it.
SOFTWARE_ADMISSION_NOTE = (
    "admitted as a self-consistent software capture: the renderer string names "
    "the software rasteriser itself, so identity and capability agree and there "
    "is no hardware claim to misattribute"
)

BINDINGS = ("release", "same-major", "off-major")


class ImportRefused(Exception):
    """A capture is not eligible to enter resources/fingerprints/raw."""


def die(msg):
    print("import-capture: %s" % msg, file=sys.stderr)
    raise SystemExit(2)


def load_admission_gate():
    """The admission gate from scripts/decompose-capture.py, as a module."""
    spec = importlib.util.spec_from_file_location(
        "apostate_capture_admission", ADMISSION_SCRIPT)
    if spec is None or spec.loader is None:
        die("cannot load the admission gate from %s" % ADMISSION_SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    for name in ("validate_capture", "persist_admission_decision",
                 "CaptureAdmissionError", "EXPECTED_BROWSER_MAJOR"):
        if not hasattr(module, name):
            die("%s does not export %s; admission gate changed shape"
                % (ADMISSION_SCRIPT.name, name))
    return module


@contextlib.contextmanager
def observed_major(gate, major):
    """Run the shared gate against the capture's own Chromium major.

    Every other check in validate_capture stays exactly as written; only the
    pinned-release comparison is lifted out, to be recorded by classify_build
    instead of refusing.
    """
    previous = gate.EXPECTED_BROWSER_MAJOR
    gate.EXPECTED_BROWSER_MAJOR = major
    try:
        yield
    finally:
        gate.EXPECTED_BROWSER_MAJOR = previous


def json_load_strict(raw, where):
    def reject_constant(_value):
        raise ValueError("non-finite JSON number")
    try:
        return json.loads(raw, parse_constant=reject_constant)
    except (ValueError, UnicodeError) as exc:
        raise ImportRefused("invalid capture JSON in %s: %s" % (where, exc))


def run_ssh(host, argv, what):
    try:
        done = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", host] + argv,
            check=False, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        raise ImportRefused("ssh is unavailable: %s" % exc)
    if done.returncode != 0:
        raise ImportRefused("%s failed on %s: %s" % (
            what, host, done.stderr.decode("utf-8", "replace").strip()))
    return done.stdout


def shell_quote(value):
    return "'" + value.replace("'", "'\\''") + "'"


def fetch_remote(host, remote_dir, name):
    if "/" in name:
        raise ImportRefused("remote source must be a bare filename, got %r" % name)
    path = "%s/%s" % (remote_dir.rstrip("/"), name)
    raw = run_ssh(host, ["cat", "--", shell_quote(path)], "reading %s" % path)
    if not raw:
        raise ImportRefused("remote capture %s is empty" % path)
    return raw, "%s:%s" % (host, path)


def fetch_remote_admission(host, remote_dir, raw_sha256):
    """The receiver's own decision, used as corroboration only."""
    path = "%s/admissions/%s.json" % (remote_dir.rstrip("/"), raw_sha256)
    try:
        raw = run_ssh(host, ["cat", "--", shell_quote(path)],
                      "reading %s" % path)
    except ImportRefused:
        return None
    return json_load_strict(raw, path)


def basic_utc(taken_at):
    try:
        parsed = datetime.datetime.fromisoformat(taken_at.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        raise ImportRefused("context.taken_at is not a parseable date-time")
    if parsed.tzinfo is None:
        raise ImportRefused("context.taken_at must include a timezone")
    return parsed.astimezone(datetime.timezone.utc).strftime("%Y%m%dT%H%M%S")


def schema_validate(capture):
    try:
        import jsonschema
    except ImportError as exc:
        # A gate that cannot run has not passed; see scripts/validate-profile.py.
        die("jsonschema is required to validate a capture (%s)" % exc)
    try:
        schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die("cannot read %s: %s" % (SCHEMA_PATH, exc))
    validator = jsonschema.Draft202012Validator(schema)
    errors = sorted(validator.iter_errors(capture), key=lambda e: list(e.path))
    if errors:
        first = errors[0]
        where = "/".join(str(part) for part in first.path) or "<root>"
        raise ImportRefused("capture fails %s at %s: %s (%d error(s))" % (
            SCHEMA_PATH.name, where, first.message, len(errors)))


def ua_major(capture):
    ua = (capture.get("context") or {}).get("ua")
    if not isinstance(ua, str):
        raise ImportRefused("context.ua must be a string")
    match = re.search(r"(?:Chrome|Chromium)/(\d+)(?:\.|\s|$)", ua)
    if not match:
        raise ImportRefused("context.ua is not a Chromium browser identity")
    return int(match.group(1))


def full_version(capture):
    high = (((capture.get("probes") or {}).get("navigator.userAgentData") or {})
            .get("value") or {}).get("high") or {}
    version = high.get("uaFullVersion")
    if not isinstance(version, str) or not re.fullmatch(r"\d+(?:\.\d+){3}", version):
        raise ImportRefused(
            "navigator.userAgentData.high.uaFullVersion must be a four-part "
            "Chromium version, got %r" % (version,))
    return version


def classify_build(version, pin):
    if version == pin:
        return "release"
    if version.split(".")[0] == pin.split(".")[0]:
        return "same-major"
    return "off-major"


def renderer_of(capture):
    webgl1 = (((capture.get("probes") or {}).get("webgl1") or {}).get("value") or {})
    return webgl1.get("unmaskedRenderer")


def software_marker(renderer):
    if not isinstance(renderer, str):
        return None
    lowered = renderer.lower()
    return next((s for s in SOFTWARE_RENDERERS if s.lower() in lowered), None)


def admit(raw, name, *, gate, pin, collector_sha256, allow_off_major,
          require_hardware_renderer=True):
    """Every admission check. Returns (capture, raw_sha256, binding, reason)."""
    raw_sha256 = hashlib.sha256(raw).hexdigest()
    capture = json_load_strict(raw, name)

    schema_validate(capture)

    context = capture.get("context") or {}
    if context.get("collector_sha256") != collector_sha256:
        raise ImportRefused(
            "context.collector_sha256 %r is not this tree's collector (%s)"
            % (context.get("collector_sha256"), collector_sha256))
    if context.get("headed") is not True:
        raise ImportRefused(
            "context.headed must be true for admission, got %r"
            % (context.get("headed"),))

    major = ua_major(capture)
    with observed_major(gate, major):
        try:
            gate.validate_capture(capture)
        except gate.CaptureAdmissionError as exc:
            raise ImportRefused(str(exc))

    version = full_version(capture)
    binding = classify_build(version, pin)
    if binding == "off-major" and not allow_off_major:
        raise ImportRefused(
            "capture is Chromium %s against release pin %s; a different major "
            "is admissible only with --allow-off-major-build" % (version, pin))

    renderer = renderer_of(capture)
    marker = software_marker(renderer)
    if marker and require_hardware_renderer:
        raise ImportRefused(
            "webgl1 renderer %r is the software or virtual rasteriser %r; this "
            "is a real measurement of a machine no profile may claim. Pass "
            "--allow-software-renderer only when the renderer string names the "
            "rasteriser itself, which is the SOFTWARE_ADMISSION_NOTE case"
            % (renderer, marker))

    taken = basic_utc(context.get("taken_at"))
    match = NAME_RE.fullmatch(name)
    if not match:
        raise ImportRefused(
            "name %r must be <device-description>-<YYYYMMDDTHHMMSS>Z" % name)
    if match.group(1) != taken:
        raise ImportRefused(
            "name %r carries timestamp %s but context.taken_at is %s (%s)"
            % (name, match.group(1), context.get("taken_at"), taken))

    reason = "capture passed admission checks"
    if marker:
        reason += "; " + SOFTWARE_ADMISSION_NOTE + (" (%s)" % marker)
    if binding != "release":
        reason += ("; browser build %s differs from release pin %s (%s) and is "
                   "not a valid V3 target for the pinned build"
                   % (version, pin, binding))
    return capture, raw_sha256, binding, reason


def write_capture(raw, path):
    """Verbatim, or refuse. The digest in the record addresses these bytes."""
    if path.exists():
        existing = path.read_bytes()
        if existing == raw:
            return "unchanged"
        raise ImportRefused(
            "%s already holds different bytes (sha256 %s, incoming %s)"
            % (path, hashlib.sha256(existing).hexdigest(),
               hashlib.sha256(raw).hexdigest()))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(raw)
    return "written"


def import_one(entry, args, gate, pin, collector_sha256):
    name = entry["name"]
    source = entry["source"]
    if entry.get("local"):
        local = pathlib.Path(source)
        try:
            raw = local.read_bytes()
        except OSError as exc:
            raise ImportRefused("cannot read %s: %s" % (local, exc))
        origin = str(local)
        remote_record = None
    else:
        raw, origin = fetch_remote(args.from_host, args.remote_dir, source)
        remote_record = None

    capture, raw_sha256, binding, reason = admit(
        raw, name, gate=gate, pin=pin, collector_sha256=collector_sha256,
        allow_off_major=bool(entry.get("allow_off_major_build")) or args.allow_off_major_build,
        require_hardware_renderer=not (
            bool(entry.get("allow_software_renderer"))
            or args.allow_software_renderer))

    stem = pathlib.PurePosixPath(source).stem
    if re.fullmatch(r"[0-9a-f]{64}", stem) and stem != raw_sha256:
        raise ImportRefused(
            "source is named %s but its bytes hash to %s" % (stem, raw_sha256))

    if not entry.get("local") and not args.no_remote_admission:
        remote_record = fetch_remote_admission(
            args.from_host, args.remote_dir, raw_sha256)
        if remote_record is not None:
            if remote_record.get("raw_sha256") != raw_sha256:
                raise ImportRefused(
                    "receiver admission record addresses %r, not %s"
                    % (remote_record.get("raw_sha256"), raw_sha256))
            if remote_record.get("decision") != "accepted":
                raise ImportRefused(
                    "receiver rejected this capture: %s"
                    % remote_record.get("reason"))

    target = RAW_DIR / (name + ".json")
    state = write_capture(raw, target)
    # The writer re-derives the acceptance from these bytes instead of taking
    # this script's word for it, so it has to run under the same gate that
    # admitted them: an off-major capture is checked against its own major,
    # exactly as admit() checked it above.
    with observed_major(gate, ua_major(capture)):
        record = gate.persist_admission_decision(
            RAW_DIR, raw_sha256, "accepted", reason, capture["context"],
            capture_path=str(target.relative_to(REPO)), raw=raw)

    return {
        "name": name,
        "origin": origin,
        "raw_sha256": raw_sha256,
        "binding": binding,
        "browser_version": full_version(capture),
        "renderer": renderer_of(capture),
        "label": capture["context"].get("label"),
        "capture": str(target.relative_to(REPO)),
        "record": str(pathlib.Path(record).relative_to(REPO)),
        "state": state,
        "receiver_record": bool(remote_record),
    }


def load_manifest(path):
    try:
        manifest = json.loads(pathlib.Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        die("cannot read manifest %s: %s" % (path, exc))
    imports = manifest.get("imports")
    if not isinstance(imports, list) or not imports:
        die("manifest %s has no imports" % path)
    for entry in imports:
        if not isinstance(entry, dict) or not entry.get("name") or not entry.get("source"):
            die("manifest entry needs name and source: %r" % (entry,))
    return manifest


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("source", nargs="?",
                    help="remote capture filename, or a local path with --local")
    ap.add_argument("--as", dest="name", help="descriptive name, without .json")
    ap.add_argument("--manifest", help="import every entry in a manifest")
    ap.add_argument("--local", action="store_true",
                    help="source is a path in this filesystem")
    ap.add_argument("--from-host", default=DEFAULT_HOST,
                    help="probe host (default %s)" % DEFAULT_HOST)
    ap.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR,
                    help="capture directory on the probe host")
    ap.add_argument("--allow-off-major-build", action="store_true",
                    help="admit a capture from a different Chromium major")
    ap.add_argument("--allow-software-renderer", action="store_true",
                    help="admit a capture whose renderer string names the "
                         "software rasteriser itself; see SOFTWARE_ADMISSION_NOTE")
    ap.add_argument("--no-remote-admission", action="store_true",
                    help="skip the receiver's corroborating decision")
    args = ap.parse_args()

    if args.manifest:
        if args.source or args.name:
            die("--manifest takes no positional source or --as")
        manifest = load_manifest(args.manifest)
        remote = manifest.get("remote") or {}
        if remote.get("host") and args.from_host == DEFAULT_HOST:
            args.from_host = remote["host"]
        if remote.get("dir") and args.remote_dir == DEFAULT_REMOTE_DIR:
            args.remote_dir = remote["dir"]
        entries = manifest["imports"]
    else:
        if not args.source or not args.name:
            die("usage: import-capture.py --as NAME SOURCE  (or --manifest FILE)")
        entries = [{"name": args.name, "source": args.source, "local": args.local}]

    try:
        pin = PIN_PATH.read_text(encoding="utf-8").strip()
        collector_sha256 = hashlib.sha256(COLLECTOR_PATH.read_bytes()).hexdigest()
    except OSError as exc:
        die("cannot read build pins or collector: %s" % exc)
    if not re.fullmatch(r"\d+(?:\.\d+){3}", pin):
        die("build/CHROMIUM_VERSION is not a four-part version: %r" % pin)

    gate = load_admission_gate()
    failures = 0
    for entry in entries:
        entry = dict(entry)
        if args.local:
            entry["local"] = True
        try:
            result = import_one(entry, args, gate, pin, collector_sha256)
        except ImportRefused as exc:
            print("REFUSED %s: %s" % (entry.get("name"), exc), file=sys.stderr)
            failures += 1
            continue
        except (OSError, ValueError) as exc:
            print("REFUSED %s: %s" % (entry.get("name"), exc), file=sys.stderr)
            failures += 1
            continue
        print("%-8s %s" % (result["state"], result["capture"]))
        print("         sha256=%s build=%s (%s)"
              % (result["raw_sha256"], result["browser_version"], result["binding"]))
        print("         label=%r renderer=%s" % (result["label"], result["renderer"]))
        print("         record=%s receiver-corroborated=%s"
              % (result["record"], result["receiver_record"]))
    if failures:
        print("%d capture(s) refused" % failures, file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
