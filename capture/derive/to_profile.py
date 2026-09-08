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

    def warn(msg):
        print(f"WARNING: {msg}", file=sys.stderr)

    def put(section, key, value):
        if value is None or value == "":
            return
        profile.setdefault(section, {})[key] = value

    put("cpu", "logical_cores", nav.get("hardwareConcurrency"))

    # deviceMemory is Chromium's bucketed output, not installed RAM: it rounds to
    # a power of two and saturates, so every machine above the top bucket reports
    # the same number. The real figure is not recoverable from any probe we have.
    #
    # This used to be harmless. The claim in the old comment here — "any value
    # inside the bucket reproduces it" — was true while the only consumer was
    # ApproximatedDeviceMemory, which re-buckets and so cannot tell the
    # difference. It stopped being true when the incognito storage quota started
    # reading the same field: that path multiplies by a ratio in [0.15, 0.2] and
    # divides by three, and a bucketed input produces a quota no machine of the
    # real size reports.
    #
    # Measured: the reference M4 Max reports deviceMemory 32, so this wrote
    # 32 GiB. Its real incognito quota is 10 GiB, which requires a pool of at
    # least 10 GiB, which requires at least ~50 GiB of installed RAM. The
    # machine has more memory than this field can express, and the profile said
    # 32.
    #
    # So it is written, because it is the best the capture can give, and flagged
    # loudly, because it is a lower bound rather than a measurement.
    if nav.get("deviceMemory"):
        gib = int(nav["deviceMemory"])
        put("memory", "total_bytes", gib * 1024**3)
        warn(f"memory.total_bytes = {gib} GiB is deviceMemory's bucket, NOT "
             f"installed RAM. deviceMemory saturates, so the real machine may "
             f"have more. Replace it with the true installed figure before "
             f"using this profile: the incognito storage quota is derived from "
             f"it and a bucketed value produces a quota no real machine of that "
             f"size reports.")

    put("platform", "name", high.get("platform"))
    put("platform", "version", high.get("platformVersion"))
    put("platform", "architecture", high.get("architecture"))
    put("platform", "bitness", high.get("bitness"))
    put("platform", "model", high.get("model"))
    if high.get("mobile") is not None:
        put("platform", "mobile", high["mobile"])
    put("platform", "navigator_platform", nav.get("platform"))

    put("browser", "user_agent", nav.get("userAgent"))

    # Everything below exists because a patch reads it. A field a patch supports
    # and this tool does not emit is a patch that silently does nothing: the
    # build is correct, the profile is valid, and the surface stays wrong.
    # Four patches were in exactly that state — colour gamut and HDR, the audio
    # buffer, capture device counts and the keyboard layout — because compose
    # emitted them and this did not.

    # Display gamut and HDR drive ScreenInfo::display_color_spaces, which is the
    # single source for both the color-gamut and dynamic-range media features.
    gamut = "srgb"
    for g in ("rec2020", "p3"):
        if g in (media.get("color-gamut") or []):
            gamut = g
            break
    put("screen", "color_gamut", gamut)
    put("screen", "hdr", "high" in (media.get("dynamic-range") or []))

    # baseLatency is buffer size over sample rate, so the frame count is exact.
    # Stored as frames because that is what the emitter takes; storing the
    # latency would lose the rate it was derived against.
    audio = probe(capture, "audio.properties") or {}
    if audio.get("baseLatency") and audio.get("sampleRate"):
        put("audio", "hardware_buffer_frames",
            round(audio["baseLatency"] * audio["sampleRate"]))

    # Capture device counts. A laptop with neither a microphone nor a camera is
    # not a laptop, and a headless host has neither.
    devs = (probe(capture, "media.devices") or {}).get("counts") or {}
    if devs:
        put("media", "audioinput_count", devs.get("audioinput", 0))
        put("media", "videoinput_count", devs.get("videoinput", 0))

    # The keyboard layout is a user setting and is not derivable from anything
    # else in the profile, so it is replayed verbatim (axiom A3).
    layout = probe(capture, "keyboard.layout")
    if layout:
        put("keyboard", "layout_map", layout)

    # WebGL limits and extensions. Both are served clamped: a limit is reported
    # as the smaller of the claim and the driver's real value, and an extension
    # is withheld rather than invented, so neither can over-claim.
    gl1 = probe(capture, "webgl1") or {}
    gl2 = probe(capture, "webgl2") or {}
    limits = {}
    for src in (gl1, gl2):
        for k, v in (src.get("parameters") or {}).items():
            if not k.startswith("MAX_"):
                continue
            n = min(v) if isinstance(v, list) and v and isinstance(v[0], int) else v
            if isinstance(n, int) and n > 0:
                limits[k] = min(limits.get(k, n), n)
    if limits:
        profile["gl_limits"] = limits
    exts = sorted(set(gl1.get("extensions") or []) | set(gl2.get("extensions") or []))
    if exts:
        profile["gl_extensions"] = exts

    # getShaderPrecisionFormat, the third WebGL component. Independent of the
    # limits and the extension list, read by nothing else, and therefore the
    # one that stays wrong quietly while the other two get fixed. Matching two
    # of three is worse than matching none: a mismatched third component is a
    # contradiction rather than an unknown.
    precisions = {}
    for src in (gl2, gl1):          # webgl1 wins on conflict; it is the wider
        for k, v in (src.get("precision") or {}).items():
            if isinstance(v, dict) and {"rangeMin", "rangeMax", "precision"} <= set(v):
                precisions[k] = {"rangeMin": v["rangeMin"],
                                 "rangeMax": v["rangeMax"],
                                 "precision": v["precision"]}
    if precisions:
        profile["gl_precisions"] = dict(sorted(precisions.items()))

    put("screen", "width", screen.get("width"))
    put("screen", "height", screen.get("height"))
    put("screen", "avail_left", screen.get("availLeft"))
    put("screen", "avail_top", screen.get("availTop"))
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
