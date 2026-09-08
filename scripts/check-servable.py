#!/usr/bin/env python3
"""Decide whether a host can honestly serve a profile, and say what stops it.

A profile is not servable everywhere. Some of what it claims can be patched into
any browser; some of it the host must actually be able to do, because a second
code path will check. Claiming MAX_TEXTURE_SIZE 16384 on a rasteriser capped at
8192 survives exactly until a page allocates the texture — and a competitor's
issue tracker records that being caught, along with a spoofed
MAX_VERTEX_UNIFORM_VECTORS recovered by a twelve-step binary search compiling
`uniform vec4 u[N]` and reading COMPILE_STATUS.

So the host's real capability is an input to profile selection, not an
afterthought, and it must be measured on the host rather than assumed from the
one this was developed on.

There is no new probe. A capture taken on the host **with no profile loaded** is
the host's capability record — the same collector, the same fields, already
comparable.

    scripts/check-servable.py PROFILE_CAPTURE.json HOST_CAPTURE.json

PROFILE_CAPTURE is the T0 capture of the device to be claimed. HOST_CAPTURE is
an unprofiled capture from the machine that would serve it.

Three verdicts per field:

  SERVABLE    the host meets or exceeds the claim, or the field is replayed
              from the profile and nothing can contradict it
  CLAMP       the host is below the claim on a value that clamping down keeps
              honest — ANGLE validates allocations against the reported limit,
              so a lower claim cannot be exceeded and cannot be caught
  BLOCKED     the claim exceeds what the host can do on a value a page can
              falsify. Serving it is a lie with a known refutation.
"""

import argparse
import json
import pathlib
import sys

# WebGL parameters a page can falsify by using them: allocate the texture, compile
# the shader, exceed the limit. The host must meet or exceed the claim.
FALSIFIABLE_MAX = [
    "MAX_TEXTURE_SIZE", "MAX_RENDERBUFFER_SIZE", "MAX_CUBE_MAP_TEXTURE_SIZE",
    "MAX_VIEWPORT_DIMS", "MAX_VERTEX_UNIFORM_VECTORS", "MAX_FRAGMENT_UNIFORM_VECTORS",
    "MAX_VARYING_VECTORS", "MAX_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_TEXTURE_IMAGE_UNITS",
    "MAX_COMBINED_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_ATTRIBS", "MAX_ARRAY_TEXTURE_LAYERS",
    "MAX_3D_TEXTURE_SIZE", "MAX_DRAW_BUFFERS", "MAX_COLOR_ATTACHMENTS", "MAX_SAMPLES",
    "MAX_ELEMENTS_INDICES", "MAX_ELEMENTS_VERTICES", "MAX_UNIFORM_BUFFER_BINDINGS",
    "MAX_VERTEX_UNIFORM_COMPONENTS", "MAX_FRAGMENT_UNIFORM_COMPONENTS",
]


def probe(cap, pid):
    p = cap.get("probes", {}).get(pid)
    return p["value"] if p and p.get("ok") else None


def num(v):
    if isinstance(v, (int, float)):
        return v
    if isinstance(v, list) and v and isinstance(v[0], (int, float)):
        return min(v)          # a range or dims pair: the binding constraint
    return None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("profile_capture", type=pathlib.Path)
    ap.add_argument("host_capture", type=pathlib.Path)
    ap.add_argument("--quiet", action="store_true", help="verdict only")
    args = ap.parse_args()

    want = json.loads(args.profile_capture.read_text())
    host = json.loads(args.host_capture.read_text())

    if (host.get("context") or {}).get("automation_suspected"):
        print("WARNING: host capture reports automation signals; its capability "
              "reading may not reflect a normal launch", file=sys.stderr)

    blocked, clamp, notes = [], [], []

    for ctx in ("webgl1", "webgl2"):
        w, h = probe(want, ctx), probe(host, ctx)
        if not (w and h):
            continue
        wp, hp = w.get("parameters") or {}, h.get("parameters") or {}
        for key in FALSIFIABLE_MAX:
            a, b = num(wp.get(key)), num(hp.get(key))
            if a is None or b is None:
                continue
            if a > b:
                blocked.append(("%s.%s" % (ctx, key), a, b))
            elif a < b:
                clamp.append(("%s.%s" % (ctx, key), a, b))

        # An extension the host lacks cannot be claimed: getExtension returns an
        # object whose methods are then called.
        we, he = set(w.get("extensions") or []), set(h.get("extensions") or [])
        for e in sorted(we - he):
            blocked.append(("%s.extension %s" % (ctx, e), "present", "absent"))

    # Fonts: a claimed family the host cannot render falls back, and the width
    # gives it away. This is provisioning, so it is reported apart from the
    # hardware limits — it is fixable by installing files, not by choosing a
    # different host.
    wf, hf = probe(want, "fonts.detected"), probe(host, "fonts.detected")
    if wf and hf:
        missing = sorted(set(wf.get("detected") or []) - set(hf.get("detected") or []))
        if missing:
            notes.append(("fonts not installed on host (%d)" % len(missing),
                          ", ".join(missing[:12]) + ("…" if len(missing) > 12 else "")))

    wn, hn = probe(want, "navigator.scalars"), probe(host, "navigator.scalars")
    if wn and hn:
        a, b = wn.get("hardwareConcurrency"), hn.get("hardwareConcurrency")
        if a and b and a > b:
            blocked.append(("navigator.hardwareConcurrency", a, b))

    if not args.quiet:
        print("profile: %s" % (want["context"].get("label") or args.profile_capture.name))
        print("host   : %s" % (host["context"].get("label") or args.host_capture.name))
        hg = probe(host, "webgl1") or {}
        print("         %s" % (hg.get("unmaskedRenderer") or "unknown renderer"))
        print()
        if blocked:
            print("BLOCKED — the host cannot do these and a page can check (%d):" % len(blocked))
            for k, a, b in blocked:
                print("    %-46s claim=%-10s host=%s" % (k, a, b))
            print()
        if clamp:
            print("CLAMP — host exceeds the claim; reporting the lower value stays honest (%d):"
                  % len(clamp))
            for k, a, b in clamp[:10]:
                print("    %-46s claim=%-10s host=%s" % (k, a, b))
            if len(clamp) > 10:
                print("    … %d more" % (len(clamp) - 10))
            print()
        for title, body in notes:
            print("%s:\n    %s\n" % (title, body))

    if blocked:
        print("VERDICT: NOT SERVABLE from this host (%d blocking field(s))" % len(blocked))
        return 1
    print("VERDICT: servable" + (" after clamping %d field(s)" % len(clamp) if clamp else ""))
    return 0


if __name__ == "__main__":
    sys.exit(main())
