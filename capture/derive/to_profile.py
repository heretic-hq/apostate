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
import math
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


def derive_gl_limits(gl1, gl2):
    """Preserve measured limits; reject conflicting context measurements."""
    parameters = {}
    for src in (gl1, gl2):
        for name, value in (src.get("parameters") or {}).items():
            if not name.startswith("MAX_"):
                continue
            if name in parameters and parameters[name] != value:
                raise ValueError(f"WebGL1/WebGL2 disagree on {name}; "
                                 "one gl_limits map cannot represent both")
            parameters[name] = value

    limits = {}
    for name, value in parameters.items():
        if name == "MAX_VIEWPORT_DIMS":
            if (not isinstance(value, list) or len(value) != 2
                    or any(type(v) is not int or not 0 < v <= 2**31 - 1
                           for v in value)):
                raise ValueError("MAX_VIEWPORT_DIMS must contain two positive int32 values")
            width, height = value
            limits["MAX_VIEWPORT_DIMS_WIDTH"] = width
            limits["MAX_VIEWPORT_DIMS_HEIGHT"] = height
            if width == height:
                # Older binaries understand the scalar square bound.
                limits[name] = width
        elif type(value) is int and value > 0:
            limits[name] = value
    return limits


def derive_screen_displays(capture):
    """Carry measured monitor topology; omitted workarea origins stay inherited."""
    details = probe(capture, "screen.details")
    if details is None:
        return None
    if not isinstance(details, dict):
        raise ValueError("screen.details must be an object")
    if "screens" not in details:
        return None
    screens = details["screens"]
    if not isinstance(screens, list) or not 1 <= len(screens) <= 16:
        raise ValueError("screen.details must contain 1..16 screens")
    if type(details.get("screenCount")) is not int or details["screenCount"] != len(screens):
        raise ValueError("screen.details screenCount disagrees with screens")
    mapping = {
        "left": "left", "top": "top", "width": "width", "height": "height",
        "avail_width": "availWidth", "avail_height": "availHeight",
        "device_pixel_ratio": "devicePixelRatio", "color_depth": "colorDepth",
        "is_primary": "isPrimary", "is_internal": "isInternal", "label": "label",
    }
    displays = []
    for screen in screens:
        if not isinstance(screen, dict) or any(key not in screen for key in mapping.values()):
            raise ValueError("screen.details has an incomplete display")
        display = {dest: screen[src] for dest, src in mapping.items()}
        if any(type(display[name]) is not int for name in
               ("left", "top", "width", "height", "avail_width", "avail_height")):
            raise ValueError("screen.details display geometry must use integers")
        for dest, src in (("avail_left", "availLeft"), ("avail_top", "availTop")):
            if screen.get(src) is not None:
                display[dest] = screen[src]
        displays.append(display)

    geometry = probe(capture, "screen.geometry") or {}
    current_primary = details.get("currentIsPrimary")
    candidates = []
    for display in displays:
        if type(current_primary) is bool and display["is_primary"] != current_primary:
            continue
        comparisons = (("width", "width"), ("height", "height"),
                       ("avail_width", "availWidth"), ("avail_height", "availHeight"),
                       ("device_pixel_ratio", "devicePixelRatio"), ("color_depth", "colorDepth"))
        if any(key in geometry and display[field] != geometry[key]
               for field, key in comparisons):
            continue
        # A measured workarea must fit inside the monitor. This can identify
        # the current display without assuming the window's top-left monitor.
        if all(type(geometry.get(k)) is int for k in ("availLeft", "availTop")):
            if not (display["left"] <= geometry["availLeft"] <=
                    display["left"] + display["width"] - display["avail_width"] and
                    display["top"] <= geometry["availTop"] <=
                    display["top"] + display["height"] - display["avail_height"]):
                continue
        candidates.append(display)
    if len(candidates) == 1:
        current = candidates[0]
        for field, key in (("avail_left", "availLeft"), ("avail_top", "availTop")):
            if geometry.get(key) is not None:
                if field in current and current[field] != geometry[key]:
                    raise ValueError("current display workarea origins disagree between probes")
                current[field] = geometry[key]

    for display in displays:
        for name in ("left", "top", "width", "height", "avail_width", "avail_height",
                     "avail_left", "avail_top"):
            if name not in display:
                continue
            value = display[name]
            if type(value) is not int or not -1000000 <= value <= 1000000:
                raise ValueError(f"screen.displays {name} must be a bounded integer")
        for size, available in (("width", "avail_width"), ("height", "avail_height")):
            if not 0 < display[available] <= display[size] <= 1000000:
                raise ValueError("screen.displays workarea must fit inside positive bounds")
        for origin, available, size, extent in (("left", "avail_left", "width", "avail_width"),
                                                ("top", "avail_top", "height", "avail_height")):
            if available in display and not (display[origin] <= display[available] <=
                    display[origin] + display[size] - display[extent]):
                raise ValueError("screen.displays workarea origin is outside its monitor")
        ratio = display["device_pixel_ratio"]
        if type(ratio) not in (int, float) or not math.isfinite(ratio) or not 1 <= ratio <= 8:
            raise ValueError("screen.displays device_pixel_ratio must be finite and in [1,8]")
        if type(display["color_depth"]) is not int or display["color_depth"] not in (24, 30):
            raise ValueError("screen.displays color_depth must be 24 or 30")
        if any(type(display[field]) is not bool for field in ("is_primary", "is_internal")):
            raise ValueError("screen.displays primary/internal flags must be booleans")
        if not isinstance(display["label"], str) or len(display["label"].encode()) > 1024:
            raise ValueError("screen.displays label must be a string of at most 1024 UTF-8 bytes")
    if sum(display["is_primary"] for display in displays) != 1:
        raise ValueError("screen.displays must have exactly one primary display")
    if type(geometry.get("isExtended")) is bool and geometry["isExtended"] != (len(displays) > 1):
        raise ValueError("screen.geometry isExtended disagrees with display count")
    return displays


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

    # Which video codecs the captured GPU decodes in fixed function, read off
    # decodingInfo().powerEfficient. This is a GPU-generation property — Intel
    # Gen9.5 does h264, vp9 and hevc and not av1 — so it has to travel with the
    # GPU the profile claims.
    CODEC_MARKERS = (("avc1", "h264"), ("avc3", "h264"), ("vp08", "vp8"),
                     ("vp8", "vp8"), ("vp09", "vp9"), ("vp9", "vp9"),
                     ("hev1", "hevc"), ("hvc1", "hevc"), ("av01", "av1"))
    hw = set()
    for entry in (probe(capture, "codecs.media") or {}).get("decodingInfo") or []:
        query = entry.get("query") or entry.get("config") or {}
        ctype = (query.get("contentType") or "").lower()
        if not ctype.startswith("video/"):
            continue
        if not (entry.get("result") or {}).get("powerEfficient"):
            continue
        for marker, name in CODEC_MARKERS:
            if marker in ctype:
                hw.add(name)
                break
    if hw:
        profile.setdefault("media", {})["hw_decode_codecs"] = sorted(hw)

    # TTS voices. Replayed rather than derived: the names encode the OS and
    # its installed language packs, and nothing about the platform string
    # predicts which twenty-odd voices a given Windows install carries.
    voices = probe(capture, "speech.voices") or []
    if isinstance(voices, list) and voices:
        out = []
        for v in voices:
            name, lang = v.get("name"), v.get("lang")
            if not name or not lang:
                continue
            entry = {"name": name, "lang": lang}
            if v.get("default"):
                entry["default"] = True
            out.append(entry)
        if out:
            profile.setdefault("speech", {})["voices"] = out

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
    limits = derive_gl_limits(gl1, gl2)
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

    # Multi-monitor. isExtended is free to read while getScreenDetails() needs
    # window-management, so this must come from the capture rather than from
    # whatever display the replay host happens to have.
    put("screen", "is_extended", screen.get("isExtended"))

    put("screen", "width", screen.get("width"))
    put("screen", "height", screen.get("height"))
    put("screen", "avail_left", screen.get("availLeft"))
    put("screen", "avail_top", screen.get("availTop"))
    put("screen", "avail_width", screen.get("availWidth"))
    put("screen", "avail_height", screen.get("availHeight"))
    put("screen", "device_pixel_ratio", screen.get("devicePixelRatio"))
    put("screen", "color_depth", screen.get("colorDepth"))
    displays = derive_screen_displays(capture)
    if displays:
        put("screen", "displays", displays)
        for index, display in enumerate(displays):
            missing = [name for name in ("avail_left", "avail_top") if name not in display]
            if missing:
                warn(f"screen.displays[{index}] has no measured {', '.join(missing)}; "
                     "those workarea origins remain inherited")

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
    # CSS system fonts. Which keywords diverge is a platform fact — Chromium's
    # Windows provider special-cases only menu, small-caption and status-bar —
    # but the values behind them come from the user's Windows theme, so all six
    # are replayed rather than derived from the platform name.
    sys_fonts = {}
    for kw, spec in ((probe(capture, "css.system") or {}).get("fonts") or {}).items():
        family, size = spec.get("fontFamily"), spec.get("fontSize")
        if not family or not isinstance(size, str) or not size.endswith("px"):
            continue
        # getComputedStyle quotes families containing spaces; the emitter wants
        # the bare name.
        family = family.strip().strip('"').strip("'")
        try:
            sys_fonts[kw] = {"family": family, "size_px": float(size[:-2])}
        except ValueError:
            continue
    if sys_fonts:
        profile.setdefault("theme", {})["system_fonts"] = dict(sorted(sys_fonts.items()))

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

    try:
        profile = build(capture)
    except ValueError as error:
        print(f"REFUSED: {error}", file=sys.stderr)
        return 2
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
