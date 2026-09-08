#!/usr/bin/env python3
"""Compose a profile from verified blocks, refusing incoherent combinations.

Takes one block of each kind and emits a profile. Every value in the result was
measured on real silicon; the only thing composed is the combination, and the
combination is only allowed across seams where it also exists in the world.

Two rules do the work.

**Same platform.** Blocks may only combine if they were captured on the same
operating system. This is conservative on purpose: a macOS display tuple with a
Windows GPU is not a machine, and the failure would not be a wrong value but an
impossible device. It also means diversity arrives with captures — one macOS
capture yields one macOS profile, and the second yields the cross product.

**Evidence tier.** A block is `measured` when every field in it came from a
capture. A hardware block may instead be `catalogue`: core count and installed
memory are chosen at purchase, so a manufacturer's shipping configuration for
the same chassis is a real machine even though we never held one. Catalogue
blocks are marked, counted separately, and never silently mixed with measured
ones in reporting.

Usage:
    scripts/compose-profile.py --platform P --gpu G --display D \\
        --hardware H --locale L [--out profile.json]
    scripts/compose-profile.py --list
    scripts/compose-profile.py --enumerate     # every coherent combination
"""

import argparse
import itertools
import re
import json
import pathlib
import sys

BLOCKS = pathlib.Path("corpus/blocks")
KINDS = ("platform", "gpu", "display", "hardware", "locale", "theme")


def load_all():
    out = {}
    for kind in KINDS:
        d = BLOCKS / kind
        out[kind] = sorted(
            ((json.loads(f.read_text()) | {"_path": str(f)})
             for f in d.glob("*.json")),
            key=lambda b: (b.get("label", ""), b.get("content_sha256", "")),
        ) if d.is_dir() else []
    return out


def platform_of(block):
    """The OS a block was captured on, from whatever probe it happens to carry."""
    c = block.get("content", {})
    ua = c.get("navigator.userAgentData")
    if ua:
        return (ua.get("high") or {}).get("platform")
    return block.get("captured_platform")


def compatible(chosen):
    """Return (ok, reason). Blocks must agree on the platform they came from."""
    plat = platform_of(chosen["platform"])
    if not plat:
        return False, "platform block does not declare an OS"
    # Derived from KINDS rather than listed, because listing it is how the
    # theme block escaped the check when it was added as the sixth kind: the
    # rule was written for five and silently kept passing four.
    for kind in (k for k in KINDS if k != "platform"):
        b = chosen[kind]
        bp = b.get("captured_platform") or platform_of(b)
        if bp and bp != plat:
            return False, ("%s block was captured on %s, platform block is %s"
                           % (kind, bp, plat))
    return True, ""


def get(block, pid, *path, default=None):
    v = block.get("content", {}).get(pid)
    for k in path:
        if not isinstance(v, dict):
            return default
        v = v.get(k)
    return default if v is None else v


FALSIFIABLE_MAX = [
    "MAX_TEXTURE_SIZE", "MAX_RENDERBUFFER_SIZE", "MAX_CUBE_MAP_TEXTURE_SIZE",
    "MAX_VIEWPORT_DIMS", "MAX_VERTEX_UNIFORM_VECTORS", "MAX_FRAGMENT_UNIFORM_VECTORS",
    "MAX_VARYING_VECTORS", "MAX_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_TEXTURE_IMAGE_UNITS",
    "MAX_COMBINED_TEXTURE_IMAGE_UNITS", "MAX_VERTEX_ATTRIBS", "MAX_3D_TEXTURE_SIZE",
    "MAX_ARRAY_TEXTURE_LAYERS", "MAX_DRAW_BUFFERS", "MAX_COLOR_ATTACHMENTS",
    "MAX_SAMPLES", "MAX_ELEMENTS_INDICES", "MAX_ELEMENTS_VERTICES",
]


def host_blocks(gpu_block, host_capture):
    """Claims in a gpu block that the serving host cannot honour."""
    def n(v):
        if isinstance(v, (int, float)):
            return v
        if isinstance(v, list) and v and isinstance(v[0], (int, float)):
            return min(v)
        return None

    out = []
    for ctx in ("webgl1", "webgl2"):
        w = (gpu_block.get("content") or {}).get(ctx)
        hp = host_capture.get("probes", {}).get(ctx)
        if not (w and hp and hp.get("ok")):
            continue
        h = hp["value"]
        wp, hpar = w.get("parameters") or {}, h.get("parameters") or {}
        for key in FALSIFIABLE_MAX:
            a, b = n(wp.get(key)), n(hpar.get(key))
            if a is not None and b is not None and a > b:
                out.append(("%s.%s" % (ctx, key), a, b))
        for e in sorted(set(w.get("extensions") or []) - set(h.get("extensions") or [])):
            out.append(("%s.extension %s" % (ctx, e), "present", "absent"))
    return out


def compose(chosen):
    """Build a profile dict from the chosen blocks."""
    plat, gpu, disp, hw, loc, theme = (chosen[k] for k in KINDS)

    ua_high = get(plat, "navigator.userAgentData", "high", default={}) or {}
    geo = disp.get("content", {}).get("screen.geometry", {}) or {}
    media = disp.get("content", {}).get("css.media", {}) or {}
    devs = get(disp, "media.devices", "counts", default={}) or {}
    intl = loc.get("content", {}).get("intl.locale", {}) or {}
    nav = (plat.get("navigator_scalars") or {})
    tmedia = theme.get("content", {}).get("css.media", {}) or {}
    tcolors = theme.get("content", {}).get("css.system", {}).get("colors", {}) or {}

    gamut = "srgb"
    for g in ("rec2020", "p3"):
        if g in (media.get("color-gamut") or []):
            gamut = g
            break

    profile = {
        "id": "composed",
        "source_blocks": {k: chosen[k]["content_sha256"][:12] for k in KINDS},
        "platform": {
            "name": ua_high.get("platform"),
            "version": ua_high.get("platformVersion"),
            "architecture": ua_high.get("architecture"),
            "bitness": ua_high.get("bitness"),
            "mobile": ua_high.get("mobile", False),
            "navigator_platform": nav.get("platform"),
        },
        "gpu": {
            "unmasked_renderer": gpu.get("renderer"),
            "unmasked_vendor": gpu.get("vendor"),
        },
        "browser": {"user_agent": nav.get("userAgent")},
        "screen": {
            "width": geo.get("width"), "height": geo.get("height"),
            "avail_left": geo.get("availLeft"), "avail_top": geo.get("availTop"),
            "avail_width": geo.get("availWidth"), "avail_height": geo.get("availHeight"),
            "device_pixel_ratio": geo.get("devicePixelRatio"),
            "color_depth": geo.get("colorDepth"),
            "color_gamut": gamut,
            "hdr": "high" in (media.get("dynamic-range") or []),
        },
        "media": {
            "audioinput_count": devs.get("audioinput", 0),
            "videoinput_count": devs.get("videoinput", 0),
        },
        "locale": {
            "timezone": (intl.get("resolvedOptions") or {}).get("timeZone"),
            "accept_languages": ",".join(nav.get("languages") or []) or None,
        },
        "keyboard": {"layout_map": loc.get("content", {}).get("keyboard.layout", {})},
        "cpu": {"logical_cores": hw.get("logical_cores")},
        "memory": {"total_bytes": hw.get("memory_total_bytes")},
        "audio": {"hardware_buffer_frames": hw.get("audio_buffer_frames")},
    }

    # pointer/hover come from the display block's media queries
    ptr = (media.get("pointer") or ["fine"])
    profile["input"] = {
        "pointer_type": ptr[0] if ptr else "fine",
        "hover": "hover" in (media.get("hover") or []),
    }

    profile["theme"] = {
        "prefers_dark": "dark" in (tmedia.get("prefers-color-scheme") or []),
    }
    for key, css in (("highlight_argb", "Highlight"),
                     ("highlight_text_argb", "HighlightText")):
        argb = css_to_argb(tcolors.get(css))
        if argb is not None:
            profile["theme"][key] = argb

    return {k: v for k, v in profile.items() if v not in (None, {}, [])}


def css_to_argb(s):
    """rgb()/rgba() to the packed ARGB the profile loader takes."""
    if not isinstance(s, str):
        return None
    m = re.match(r"rgba?\(([^)]+)\)", s.strip())
    if not m:
        return None
    parts = [x.strip() for x in m.group(1).split(",")]
    if len(parts) < 3:
        return None
    try:
        r, g, b = (int(float(x)) for x in parts[:3])
        a = int(round(float(parts[3]) * 255)) if len(parts) > 3 else 255
    except ValueError:
        return None
    return (a << 24) | (r << 16) | (g << 8) | b


def describe(b, kind):
    if kind == "gpu":
        return "%s  [%s]" % (b.get("renderer"), b["content_sha256"][:12])
    if kind == "display":
        g = b.get("content", {}).get("screen.geometry", {}) or {}
        return "%sx%s @%s  [%s]" % (g.get("width"), g.get("height"),
                                    g.get("devicePixelRatio"), b["content_sha256"][:12])
    if kind == "hardware":
        return "%s cores, %s  [%s]" % (
            b.get("logical_cores", "?"),
            ("%d GiB" % (b["memory_total_bytes"] // 1024**3))
            if b.get("memory_total_bytes") else "? memory",
            b["content_sha256"][:12])
    if kind == "locale":
        intl = b.get("content", {}).get("intl.locale", {}) or {}
        return "%s  [%s]" % ((intl.get("resolvedOptions") or {}).get("timeZone"),
                             b["content_sha256"][:12])
    return "%s  [%s]" % (platform_of(b), b["content_sha256"][:12])


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    for k in KINDS:
        ap.add_argument("--" + k)
    ap.add_argument("--host", type=pathlib.Path,
                    help="an unprofiled capture from the machine that will serve "
                         "this profile. Blocks whose claims the host cannot "
                         "honour are refused: a WebGL limit or extension the "
                         "host lacks is falsifiable by using it, so serving it "
                         "is a lie with a known refutation. Selection has to be "
                         "dynamic per host — the corpus cannot know in advance "
                         "which machine will run it.")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--enumerate", action="store_true")
    ap.add_argument("--out", type=pathlib.Path)
    args = ap.parse_args()

    all_blocks = load_all()

    if args.list:
        for kind in KINDS:
            print("%s (%d):" % (kind, len(all_blocks[kind])))
            for b in all_blocks[kind]:
                tier = b.get("evidence", "measured")
                print("    %-64s %s" % (describe(b, kind), tier))
        return 0

    if args.enumerate:
        total = ok = 0
        for combo in itertools.product(*(all_blocks[k] for k in KINDS)):
            chosen = dict(zip(KINDS, combo))
            total += 1
            good, why = compatible(chosen)
            if good:
                ok += 1
        print("%d combination(s) of available blocks, %d coherent" % (total, ok))
        for kind in KINDS:
            print("  %-9s %d" % (kind, len(all_blocks[kind])))
        return 0

    chosen = {}
    for kind in KINDS:
        want = getattr(args, kind)
        pool = all_blocks[kind]
        if not pool:
            print("no %s blocks; run scripts/decompose-capture.py first" % kind,
                  file=sys.stderr)
            return 2
        if want:
            m = [b for b in pool if b["content_sha256"].startswith(want)
                 or b.get("label") == want]
            if len(m) != 1:
                print("--%s %r matched %d blocks" % (kind, want, len(m)), file=sys.stderr)
                return 2
            chosen[kind] = m[0]
        elif len(pool) == 1:
            chosen[kind] = pool[0]
        else:
            print("--%s is ambiguous (%d blocks); name one" % (kind, len(pool)),
                  file=sys.stderr)
            return 2

    good, why = compatible(chosen)
    if not good:
        print("REFUSED: %s" % why, file=sys.stderr)
        return 2

    if args.host:
        blocked = host_blocks(chosen["gpu"], json.loads(args.host.read_text()))
        if blocked:
            print("REFUSED: this host cannot serve the chosen gpu block.",
                  file=sys.stderr)
            for k, a, b in blocked[:8]:
                print("    %-44s claim=%-10s host=%s" % (k, a, b), file=sys.stderr)
            if len(blocked) > 8:
                print("    … %d more" % (len(blocked) - 8), file=sys.stderr)
            print("  Run scripts/check-servable.py for the full report.",
                  file=sys.stderr)
            return 2

    profile = compose(chosen)
    tiers = {chosen[k].get("evidence", "measured") for k in KINDS}
    if "catalogue" in tiers:
        print("NOTE: profile includes catalogue-tier blocks (a real shipping "
              "configuration, not a machine we measured)", file=sys.stderr)

    text = json.dumps(profile, indent=2) + "\n"
    if args.out:
        args.out.write_text(text)
        print("wrote %s" % args.out, file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
