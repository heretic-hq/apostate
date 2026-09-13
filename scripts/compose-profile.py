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
import hashlib
import itertools
import re
import json
import pathlib
import sys

BLOCKS = pathlib.Path("corpus/blocks")
KINDS = ("platform", "gpu", "display", "hardware", "locale", "theme")

# A block is admissible only when the decomposer recorded this explicit
# evidence vocabulary. Labels and filenames are selection hints, never proof.
EVIDENCE_CLASSES = {
    "physical-ground-truth", "compatibility-capture", "catalogue-value",
    "native-derived", "proxy-derived", "host-inherited",
}
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def _source_capture_path(block):
    """Return a locally available raw capture path, when one is discoverable."""
    source = block.get("source_capture")
    if not isinstance(source, str) or not source.strip():
        return None
    candidates = []
    path = pathlib.Path(source)
    if path.is_absolute():
        candidates.append(path)
    else:
        block_path = block.get("_path")
        if block_path:
            candidates.append(pathlib.Path(block_path).resolve().parent / path)
        candidates.append(pathlib.Path(__file__).resolve().parent.parent /
                          "resources/fingerprints/raw" / path.name)
    return next((p for p in candidates if p.is_file()), None)


def _validate_source_capture_hash(block):
    value = block.get("source_capture_sha256")
    if not isinstance(value, str) or not SHA256_RE.fullmatch(value):
        raise ValueError("source_capture_sha256 must be a 64-character SHA-256")
    path = _source_capture_path(block)
    if path is not None:
        actual = hashlib.sha256(path.read_bytes()).hexdigest()
        if actual.lower() != value.lower():
            raise ValueError("source_capture_sha256 does not match %s" % path)
    return value.lower()


def _validate_source_context(block):
    context = block.get("source_context")
    if not isinstance(context, dict) or not context:
        raise ValueError("source_context must be a non-empty object")
    required = ("taken_at", "ua", "collector_sha256", "secure_context",
                "automation_suspected", "automation_signals")
    missing = [key for key in required if key not in context]
    if missing:
        raise ValueError("source_context missing required field(s): %s" %
                         ", ".join(missing))
    if not isinstance(context["taken_at"], str) or not context["taken_at"].strip():
        raise ValueError("source_context.taken_at must be a non-empty string")
    if not isinstance(context["ua"], str) or not context["ua"].strip():
        raise ValueError("source_context.ua must be a non-empty string")
    collector = context["collector_sha256"]
    if not isinstance(collector, str) or not SHA256_RE.fullmatch(collector):
        raise ValueError("source_context.collector_sha256 is invalid")
    if context["secure_context"] is not True:
        raise ValueError("source_context.secure_context must be true")
    if context["automation_suspected"] is not False:
        raise ValueError("source_context.automation_suspected must be false")
    if context["automation_signals"] != []:
        raise ValueError("source_context.automation_signals must be an empty array")
    if "label" in context and context["label"] is not None and (
            not isinstance(context["label"], str) or not context["label"].strip()):
        raise ValueError("source_context.label must be a non-empty string or null")
    if (block.get("label") is not None and context.get("label") is not None and
            block["label"] != context["label"]):
        raise ValueError("label disagrees with source_context.label")
    if (block.get("source_taken_at") is not None and
            context["taken_at"] != block["source_taken_at"]):
        raise ValueError("source_taken_at disagrees with source_context.taken_at")
    return context


def _validate_provenance(block, require_platform=False):
    evidence = block.get("evidence")
    if not isinstance(evidence, str) or evidence not in EVIDENCE_CLASSES:
        raise ValueError("unsupported or missing evidence class %r" % evidence)
    source = block.get("source_capture")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("source_capture is required")
    source_hash = _validate_source_capture_hash(block)
    context = _validate_source_context(block)
    provenance = block.get("provenance")
    if not isinstance(provenance, (dict, str)) or not provenance:
        raise ValueError("provenance must be a non-empty object or string")
    platform = block.get("captured_platform")
    if require_platform and (not isinstance(platform, str) or not platform.strip()):
        raise ValueError("captured_platform is required")
    return {
        "evidence": evidence,
        "source_capture": source,
        "source_capture_sha256": source_hash,
        "source_context": context,
        "captured_platform": platform,
        "provenance": provenance,
    }


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
    """The OS a block was captured on, from its measured platform probe."""
    c = block.get("content", {})
    ua = c.get("navigator.userAgentData") or {}
    return (ua.get("high") or {}).get("platform") or block.get("captured_platform")


def evidence_of(block):
    """Return an explicit evidence class after validating block provenance."""
    return _validate_provenance(block)["evidence"]


def provenance_token(chosen):
    """Encode complete block provenance in schema-allowed source_capture text."""
    records = {}
    for kind in KINDS:
        block = chosen[kind]
        provenance = _validate_provenance(block)
        records[kind] = {
            "content_sha256": block.get("content_sha256"),
            **provenance,
        }
    return "composed:" + json.dumps(records, sort_keys=True,
                                     separators=(",", ":"))


def compatible(chosen):
    """Return (ok, reason). Blocks must agree on captured platform."""
    for kind in KINDS:
        try:
            _validate_provenance(chosen[kind], require_platform=True)
        except (KeyError, ValueError) as exc:
            return False, "%s block has invalid provenance: %s" % (kind, exc)

    plat = chosen["platform"].get("captured_platform")
    if not plat:
        return False, "platform block does not declare an OS"
    measured_plat = platform_of(chosen["platform"])
    if measured_plat and measured_plat != plat:
        return False, ("platform block metadata says %s but probe says %s"
                       % (plat, measured_plat))
    # Derived from KINDS rather than listed, because listing it is how the
    # theme block escaped the check when it was added as the sixth kind.
    for kind in (k for k in KINDS if k != "platform"):
        bp = chosen[kind]["captured_platform"]
        if bp != plat:
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
        # source_blocks is deliberately not a profile property: the native
        # schema rejects arbitrary top-level metadata. Keep complete structured
        # resolver provenance in the allowed source_capture string; launch
        # emitters strip this resolver-only field before native payload emission.
        "source_capture": provenance_token(chosen),
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
        "memory": ({"total_bytes": hw["memory_total_bytes"]}
                   if hw.get("memory_total_bytes") is not None else {}),
        "audio": ({"hardware_buffer_frames": hw["audio_buffer_frames"]}
                  if hw.get("audio_buffer_frames") is not None else {}),
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
                try:
                    tier = evidence_of(b)
                except ValueError:
                    tier = "invalid"
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
    tiers = {evidence_of(chosen[k]) for k in KINDS}
    if "catalogue-value" in tiers:
        print("NOTE: profile includes catalogue-value blocks (normalized "
              "compatibility research, not a direct device capture)", file=sys.stderr)
    elif "compatibility-capture" in tiers:
        print("NOTE: profile includes compatibility-capture blocks (exercised "
              "against targets, not direct physical-device ground truth)", file=sys.stderr)

    text = json.dumps(profile, indent=2) + "\n"
    if args.out:
        args.out.write_text(text)
        print("wrote %s" % args.out, file=sys.stderr)
    else:
        print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
