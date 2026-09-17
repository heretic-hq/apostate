#!/usr/bin/env python3
"""Project every capture on the probe host down to its GPU digests.

An anchor is only defensible if the captures that were *not* admitted are
accounted for as measurement rather than as a claim in a README. The probe host
holds captures this repository refuses, and the reason they are refused is
itself a measurement: a group of files whose renderer strings name different
silicon while their capability tables and rendered pixels hash equal were not
taken on different machines.

That evidence has to live in the tree, because the host is not the tree and a
future reader cannot re-derive a discard decision from a sentence. This script
writes it: one row per capture on the host, carrying only digests, provenance
and identity strings. No probe payload is transferred — a capture is ~1.4 MB of
raw pixels and audio, and none of it is needed to compare two devices.

The projection runs on the host and returns roughly 400 bytes per capture.
The digest definitions mirror scripts/build-anchors.py, and
`build-anchors.py --survey` re-checks every row it can measure locally against
this file, so the two implementations cannot silently drift apart.

Usage:
    scripts/capture-digest-survey.py
    scripts/capture-digest-survey.py --from-host USER@HOST --out PATH
"""

import argparse
import json
import os
import pathlib
import subprocess
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent
DEFAULT_OUT = REPO / "corpus" / "anchors" / "probe-host-survey.json"
DEFAULT_HOST = os.environ.get("APOSTATE_PROBE_HOST", "apostate@40.87.20.34")
DEFAULT_REMOTE_DIR = os.environ.get(
    "APOSTATE_PROBE_CAPTURES", "/var/lib/apostate-probe/captures")

# Runs on the probe host. Reads each capture, emits digests, never the payload.
PROJECTION = r'''
import hashlib, json, pathlib, sys

CAPS = ("antialiasSamples", "contextAttributes", "extensions", "parameters", "precision")
IDENTITY = ("renderer", "shadingLanguageVersion", "unmaskedRenderer",
            "unmaskedVendor", "vendor", "version")
# Mirrors PARAMETER_IDENTITY in scripts/build-anchors.py: the collector queries
# every GL constant, so `parameters` also carries the identity strings under
# their getParameter names, and they are not capability.
PARAMETER_IDENTITY = ("RENDERER", "SHADING_LANGUAGE_VERSION", "VENDOR", "VERSION")


def caps(value):
    out = {k: (value or {}).get(k) for k in CAPS}
    if isinstance(out.get("parameters"), dict):
        out["parameters"] = dict((k, v) for k, v in out["parameters"].items()
                                 if k not in PARAMETER_IDENTITY)
    return out


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(canon(obj).encode("utf-8")).hexdigest()


def probe(capture, pid, section="probes"):
    entry = (capture.get(section) or {}).get(pid)
    if not entry or not entry.get("ok"):
        return None
    return entry.get("value")


def webgpu_cluster(value):
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


rows = []
root = pathlib.Path(sys.argv[1])
for path in sorted(root.glob("*.json")):
    raw = path.read_bytes()
    row = {"file": path.name, "sha256": hashlib.sha256(raw).hexdigest()}
    try:
        capture = json.loads(raw)
    except ValueError as exc:
        row["unreadable"] = str(exc)
        rows.append(row)
        continue
    context = capture.get("context") or {}
    probes = capture.get("probes") or {}
    webgl1 = probe(capture, "webgl1") or {}
    webgl2 = probe(capture, "webgl2") or {}
    canvas = probe(capture, "canvas.2d") or {}
    high = ((probe(capture, "navigator.userAgentData") or {}).get("high") or {})
    row.update({
        "automation_signals": context.get("automation_signals"),
        "automation_suspected": context.get("automation_suspected"),
        "browser_platform": high.get("platform"),
        "browser_version": high.get("uaFullVersion"),
        "canvas_pixels_sha256": canvas.get("pixels_sha256"),
        "capture_version": capture.get("capture_version"),
        "collector_sha256": context.get("collector_sha256"),
        "failed_probes": sorted(k for k, v in probes.items() if not v.get("ok")),
        "headed": context.get("headed"),
        "identity": {
            "webgl1": {k: webgl1.get(k) for k in IDENTITY},
            "webgl2": {k: webgl2.get(k) for k in IDENTITY},
        },
        "label": context.get("label"),
        "probe_count": len(probes),
        "repeat_count": len(capture.get("repeat") or {}),
        "secure_context": context.get("secure_context"),
        "taken_at": context.get("taken_at"),
        "ua": context.get("ua"),
        "webgl1_caps_sha256": sha(caps(webgl1)),
        "webgl1_pixels_sha256": webgl1.get("pixels_sha256"),
        "webgl2_caps_sha256": sha(caps(webgl2)),
        "webgl2_pixels_sha256": webgl2.get("pixels_sha256"),
        "webgpu_cluster_sha256": sha(webgpu_cluster(probe(capture, "webgpu"))),
    })
    rows.append(row)
print(json.dumps(rows, sort_keys=True, separators=(",", ":")))
'''


def die(msg):
    print("capture-digest-survey: %s" % msg, file=sys.stderr)
    raise SystemExit(2)


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--from-host", default=DEFAULT_HOST)
    ap.add_argument("--remote-dir", default=DEFAULT_REMOTE_DIR)
    ap.add_argument("--out", type=pathlib.Path, default=DEFAULT_OUT)
    args = ap.parse_args()

    try:
        done = subprocess.run(
            ["ssh", "-o", "BatchMode=yes", args.from_host,
             "python3", "-", args.remote_dir],
            input=PROJECTION.encode("utf-8"), check=False,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    except OSError as exc:
        die("ssh is unavailable: %s" % exc)
    if done.returncode != 0:
        die("projection failed on %s: %s"
            % (args.from_host, done.stderr.decode("utf-8", "replace").strip()))
    try:
        captures = json.loads(done.stdout)
    except ValueError as exc:
        die("projection returned unparseable output: %s" % exc)
    if not isinstance(captures, list) or not captures:
        die("projection returned no captures from %s" % args.remote_dir)

    survey = {
        "captures": sorted(captures, key=lambda row: row["file"]),
        "dir": args.remote_dir,
        "host": args.from_host,
        "projection_sha256": __import__("hashlib").sha256(
            PROJECTION.encode("utf-8")).hexdigest(),
    }
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(
        json.dumps(survey, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8")
    print("surveyed %d capture(s) on %s -> %s"
          % (len(captures), args.from_host, args.out.relative_to(REPO)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
