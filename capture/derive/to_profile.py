#!/usr/bin/env python3
"""Derive an Apostate profile from a T0 capture.

The profile is what the browser presents; the capture is what a real device
emitted. This turns one into the other, so a profile is always traceable to a
measurement rather than being written by hand.

    python3 capture/derive/to_profile.py CAPTURE.json > profile.json

Fields the capture does not carry are omitted rather than defaulted. An absent
field leaves the surface inherited, which is correct; an invented one is wrong
in a way no consumer can detect.
"""

import argparse
import json
import pathlib
import re
import sys


def probe(capture, name):
    p = capture.get("probes", {}).get(name)
    return p["value"] if p and p.get("ok") else None


def css_rgb_to_argb(value):
    """'rgba(179, 215, 255, 0.8)' -> 0xCCB3D7FF. None if unparseable."""
    if not value:
        return None
    m = re.match(r"rgba?\(\s*(\d+)[,\s]+(\d+)[,\s]+(\d+)\s*(?:[,/]\s*([\d.]+))?\s*\)", value)
    if not m:
        return None
    r, g, b = (int(m.group(i)) for i in (1, 2, 3))
    a = round(float(m.group(4)) * 255) if m.group(4) else 255
    return (a << 24) | (r << 16) | (g << 8) | b


def build(capture):
    ctx = capture.get("context", {})
    nav = probe(capture, "navigator.scalars") or {}
    uad = probe(capture, "navigator.userAgentData") or {}
    high = (uad.get("high") or {}) if uad else {}
    screen = probe(capture, "screen.geometry") or {}
    gl = probe(capture, "webgl1") or {}
    intl = probe(capture, "intl.locale") or {}
    media = probe(capture, "css.media") or {}
    system = (probe(capture, "css.system") or {}).get("colors") or {}
    mem = probe(capture, "memory.heap") or {}

    profile = {"id": ctx.get("label") or "unnamed",
               "source_capture": ctx.get("taken_at")}

    def put(section, key, value):
        if value is None or value == "":
            return
        profile.setdefault(section, {})[key] = value

    put("cpu", "logical_cores", nav.get("hardwareConcurrency"))

    # deviceMemory is Chromium's bucketed output, not installed RAM. Recording
    # it as the exact bucket is honest: the real figure is not recoverable from
    # the capture, and any value inside the bucket reproduces it.
    if nav.get("deviceMemory"):
        put("memory", "total_bytes", int(nav["deviceMemory"]) * 1024**3)

    put("platform", "name", high.get("platform"))
    put("platform", "version", high.get("platformVersion"))
    put("platform", "architecture", high.get("architecture"))
    put("platform", "bitness", high.get("bitness"))
    put("platform", "model", high.get("model"))
    if high.get("mobile") is not None:
        put("platform", "mobile", high["mobile"])
    put("platform", "navigator_platform", nav.get("platform"))

    put("browser", "user_agent", nav.get("userAgent"))

    put("screen", "width", screen.get("width"))
    put("screen", "height", screen.get("height"))
    put("screen", "avail_width", screen.get("availWidth"))
    put("screen", "avail_height", screen.get("availHeight"))
    put("screen", "device_pixel_ratio", screen.get("devicePixelRatio"))
    put("screen", "color_depth", screen.get("colorDepth"))

    put("gpu", "unmasked_renderer", gl.get("unmaskedRenderer"))
    put("gpu", "unmasked_vendor", gl.get("unmaskedVendor"))

    put("locale", "timezone", (intl.get("resolvedOptions") or {}).get("timeZone"))
    langs = nav.get("languages")
    if langs:
        put("locale", "accept_languages", ",".join(langs))

    # Pointer and hover are capability facts about the device, and headless
    # reports none for both — so a profile derived from a headless capture would
    # carry that tell forward. Taken from the capture as measured; a capture
    # that says none was itself taken without an input device.
    pointer = media.get("any-pointer") or []
    put("input", "pointer_type",
        "fine" if "fine" in pointer else "coarse" if "coarse" in pointer else None)
    hover = media.get("any-hover") or []
    if hover:
        put("input", "hover", "hover" in hover)

    scheme = media.get("prefers-color-scheme") or []
    if scheme:
        put("theme", "prefers_dark", "dark" in scheme)
    put("theme", "highlight_argb", css_rgb_to_argb(system.get("Highlight")))
    put("theme", "highlight_text_argb", css_rgb_to_argb(system.get("HighlightText")))

    return profile


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, help="write here instead of stdout")
    args = ap.parse_args()

    capture = json.loads(args.capture.read_text())
    ctx = capture.get("context", {})
    if ctx.get("automation_suspected"):
        print(f"REFUSED: {args.capture.name} reports automation signals "
              f"{ctx.get('automation_signals')}. A capture taken under automation "
              f"is not ground truth and must not become a profile.", file=sys.stderr)
        return 2

    profile = build(capture)
    text = json.dumps(profile, indent=1)
    if args.out:
        args.out.write_text(text + "\n")
        print(f"wrote {args.out}", file=sys.stderr)
    else:
        print(text)

    missing = [s for s in ("cpu", "memory", "platform", "browser", "screen",
                           "gpu", "locale", "theme") if s not in profile]
    if missing:
        print(f"note: no data for {', '.join(missing)} — those surfaces stay "
              f"inherited", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
