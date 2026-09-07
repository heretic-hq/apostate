#!/usr/bin/env python3
"""Decompose a T0 capture into orthogonal, independently permutable blocks.

The corpus is not a set of monolithic profiles. A monolith can only ever be
replayed as itself, so a thousand sessions of one capture emit one fingerprint
and collapse into a single attributable cluster. But the alternative usually
reached for — synthesising values from a PRNG — corrupts engine invariants:
altering low-order canvas bits fails bitstream verification, and scaling a
viewport by an arbitrary float desynchronises Chromium's 60-to-1 subpixel
quantisation so getBoundingClientRect and getClientRects disagree.

This takes the third path. A capture is cut into blocks along the seams where
real hardware is genuinely configurable, and profiles are composed by combining
blocks that came from real machines. Every value shipped was measured on
silicon; the only thing invented is the *combination*, and only across seams
where the combination also exists in the world.

Blocks, and why each seam is real:

  platform   OS identity and everything the OS build alone determines — fonts,
             speech voices, codecs, wasm, the API surface, audio render, system
             colours. Not configurable at purchase; changes only with the OS.
  gpu        Renderer and vendor strings *together with* the capability tables
             and the rendered output they produce. Atomic on purpose: the string
             never travels without the table that belongs to it, so we never
             assert two GPUs are interchangeable.
  display    Screen tuple, DPR, colour gamut, HDR, built-in capture devices.
             Real because one GPU ships in several chassis, and any machine can
             drive an external panel.
  hardware   Core count, installed memory, audio buffer. Real because these are
             chosen at purchase for an otherwise identical machine.
  locale     Timezone, languages, keyboard layout. Real because it is the user's
             setting, not the device's.

Usage:
    scripts/decompose-capture.py CAPTURE.json [--out corpus/blocks]
"""

import argparse
import hashlib
import json
import pathlib
import sys

# Probe -> block. A probe listed nowhere is deliberately not carried into any
# block; see UNASSIGNED at the bottom for why each one is left out.
BLOCK_PROBES = {
    "platform": [
        "navigator.userAgentData", "navigator.plugins", "api.surface",
        "wasm", "native_code.toString", "math.precision",
        "webrtc.capabilities", "fonts.detected", "fonts.query_api",
        "audio.offline_render", "speech.voices", "touch",
    ],
    # codecs.media sits here rather than in platform, on evidence. Comparing a
    # Windows VM with no GPU driver against a real Windows machine with an
    # RTX 3070 Ti, the only codec field that differed was decodingInfo's
    # powerEfficient on the three video codecs — false on the VM, true on the
    # card. powerEfficient means hardware-accelerated decode, so it is a
    # property of the GPU, not of the OS. Everything else in the probe was
    # identical across the two machines.
    "gpu": ["webgl1", "webgl2", "webgpu", "canvas.2d", "canvas.toDataURL_variants",
            "clientrects", "codecs.media"],
    "display": ["screen.geometry", "screen.details", "css.media", "media.devices"],
    "hardware": ["memory.heap", "audio.properties"],
    "locale": ["intl.locale", "keyboard.layout"],
    "theme": ["css.system", "css.media"],
}

# css.media answers two unrelated questions in one probe: what the panel can do,
# and what the user has chosen. Those are independent axes — a p3 HDR display is
# equally plausible in light or dark mode — so the probe is split by feature
# rather than assigned whole to either block.
DISPLAY_MEDIA_FEATURES = {"color-gamut", "dynamic-range", "any-pointer", "pointer",
                          "any-hover", "hover", "orientation", "update",
                          "overflow-block", "display-mode", "scripting"}
THEME_MEDIA_FEATURES = {"prefers-color-scheme", "prefers-reduced-motion",
                        "prefers-contrast", "forced-colors", "inverted-colors"}

# Fields inside a probe that identify the *part* rather than its behaviour.
# Excluded from a block's identity hash so that two captures of the same silicon
# hash equal even though their strings differ.
IDENTITY_FIELDS = {"unmaskedVendor", "unmaskedRenderer", "vendor", "renderer"}


def canon(obj):
    return json.dumps(obj, sort_keys=True, separators=(",", ":"))


def sha(obj):
    return hashlib.sha256(canon(obj).encode()).hexdigest()


def strip_identity(value):
    """A copy of a probe value with the part-identifying strings removed."""
    if not isinstance(value, dict):
        return value
    return {k: v for k, v in value.items() if k not in IDENTITY_FIELDS}


def probe(cap, pid):
    p = cap.get("probes", {}).get(pid)
    return p["value"] if p and p.get("ok") else None


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("capture", type=pathlib.Path)
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("corpus/blocks"))
    ap.add_argument("--allow-software-gpu", action="store_true",
                    help="keep a gpu block whose renderer is a software or "
                         "virtual rasteriser (normally skipped)")
    ap.add_argument("--allow-foreign-brand", action="store_true",
                    help="capture is from another Chromium browser: take only "
                         "the hardware blocks, never the platform block")
    args = ap.parse_args()

    cap = json.loads(args.capture.read_text())
    ctx = cap["context"]

    # A capture that is not ground truth must never enter the corpus. These are
    # the same two gates conform.py applies, checked here so a bad capture is
    # rejected at the door rather than discovered downstream.
    if ctx.get("automation_suspected"):
        print("REFUSED: capture reports automation signals: %s"
              % ctx.get("automation_signals"), file=sys.stderr)
        return 2
    if ctx.get("secure_context") is False:
        print("REFUSED: capture was not taken in a secure context. Every "
              "secure-context-gated probe in it reads 'unsupported'.", file=sys.stderr)
        return 2
    # The binary we ship is Chrome-branded Chromium and says so in its brand
    # list, so a capture from another Chromium-derived browser cannot supply a
    # platform block: the profile would claim Chrome while its Client Hints
    # named a different vendor, which is a flat contradiction a page reads in
    # one call. Edge, Brave, Opera and Vivaldi all present as Chromium plus
    # their own brand.
    #
    # The hardware-shaped blocks are a different question. A GPU block is ANGLE
    # over the same driver whatever Chromium wrapped it, and a screen is a
    # screen. Those are probably portable — but probably is not measured, so
    # they are only emitted on request and are stamped with the browser they
    # came from, never silently.
    brands = ((probe(cap, "navigator.userAgentData") or {}).get("low") or {}).get("brands") or []
    names = [b.get("brand") for b in brands]
    foreign = [n for n in names
               if n and n not in ("Chromium", "Google Chrome")
               and "Not" not in n and "Brand" not in n]
    if foreign and not args.allow_foreign_brand:
        print("REFUSED: capture is from %s, not Google Chrome (brands: %s)."
              % (", ".join(foreign), ", ".join(n for n in names if n)), file=sys.stderr)
        print("  Our binary reports 'Google Chrome', so a platform block from this",
              file=sys.stderr)
        print("  capture would contradict the profile it is used in.", file=sys.stderr)
        print("  Re-capture in Google Chrome, or pass --allow-foreign-brand to take",
              file=sys.stderr)
        print("  only the hardware blocks (gpu, display, hardware, locale, theme).",
              file=sys.stderr)
        return 2

    failed = [k for k, v in cap["probes"].items() if not v.get("ok")]
    if failed:
        print("REFUSED: %d probe(s) failed; a partial capture makes partial "
              "blocks: %s" % (len(failed), ", ".join(failed)), file=sys.stderr)
        return 2

    label = ctx.get("label") or args.capture.stem
    written = []

    for block, pids in BLOCK_PROBES.items():
        if foreign and block == "platform":
            continue
        content = {}
        for pid in pids:
            v = probe(cap, pid)
            if v is not None:
                content[pid] = v
        if block in ("display", "theme") and "css.media" in content:
            keep = DISPLAY_MEDIA_FEATURES if block == "display" else THEME_MEDIA_FEATURES
            content["css.media"] = {k: v for k, v in content["css.media"].items()
                                    if k in keep}

        if not content:
            continue

        # The identity hash ignores the part's *name*. Two captures of the same
        # silicon therefore hash equal even if their renderer strings differ,
        # which is what makes "are these interchangeable?" a measurement rather
        # than an assertion. It stays unused until a second capture exists to
        # compare against.
        ident = {pid: strip_identity(v) for pid, v in content.items()}

        rec = {
            "block": block,
            "label": label,
            "source_capture": str(args.capture.name),
            "source_taken_at": ctx.get("taken_at"),
            "browser_version": (probe(cap, "navigator.userAgentData") or {})
                               .get("high", {}).get("uaFullVersion"),
            "captured_browser": (foreign[0] if foreign else "Google Chrome"),
            "captured_platform": ((probe(cap, "navigator.userAgentData") or {})
                                  .get("high") or {}).get("platform"),
            "content_sha256": sha(content),
            "identity_sha256": sha(ident),
            "content": content,
        }

        if block == "platform":
            # Carried as a side field rather than as a probe, because
            # navigator.scalars spans blocks: its UA and platform strings belong
            # to the OS, its languages to locale, its core and memory counts to
            # hardware. Each block takes only the fields it owns.
            nav = probe(cap, "navigator.scalars") or {}
            rec["navigator_scalars"] = {
                k: nav.get(k) for k in ("userAgent", "platform", "languages")
                if nav.get(k) is not None
            }

        if block == "hardware":
            # Sourced from probes that span blocks, so they are pulled out by
            # field rather than carried whole.
            nav = probe(cap, "navigator.scalars") or {}
            audio = probe(cap, "audio.properties") or {}
            rec["logical_cores"] = nav.get("hardwareConcurrency")
            # baseLatency is buffer size over sample rate, so the frame count is
            # recoverable exactly. Stored as frames because that is what the
            # emitter takes; storing the latency would make the profile carry a
            # derived value and lose the rate it was derived against.
            if audio.get("baseLatency") and audio.get("sampleRate"):
                rec["audio_buffer_frames"] = round(
                    audio["baseLatency"] * audio["sampleRate"])
                rec["audio_sample_rate"] = audio["sampleRate"]
            # deviceMemory is Chromium's bucket, not installed RAM: it rounds to
            # a power of two and saturates, so every machine above the top bucket
            # reports the same number. Recorded as a floor and flagged, because
            # the incognito storage quota is derived from this field and a
            # bucketed value there produces a quota no machine of the real size
            # reports. The true figure has to come from the machine's owner.
            if nav.get("deviceMemory"):
                rec["memory_total_bytes"] = int(nav["deviceMemory"]) * 1024**3
                rec["memory_is_floor"] = True
                rec["evidence"] = "measured-floor"

        if block == "gpu":
            g = content.get("webgl1") or {}
            # A software or virtual renderer is a real measurement of a real
            # machine, and it is not a machine any consumer profile should
            # claim. "Microsoft Basic Render Driver" is what Windows falls back
            # to with no GPU driver, which is the signature of a VM — it says
            # data centre as loudly as anything a page can read. Blocked here
            # rather than at composition time, so it never enters the corpus.
            r = (g.get("unmaskedRenderer") or "")
            soft = next((s for s in ("Basic Render Driver", "Basic Display",
                                     "SwiftShader", "llvmpipe", "softpipe",
                                     "VMware", "VirtualBox", "Parallels",
                                     "Microsoft Remote Display", "Mesa OffScreen")
                         if s.lower() in r.lower()), None)
            if soft and not args.allow_software_gpu:
                print("SKIPPED gpu block: renderer is %r, a software or virtual "
                      "rasteriser (%s)." % (r, soft), file=sys.stderr)
                print("  No consumer machine reports this, so it must not become "
                      "a GPU block. The", file=sys.stderr)
                print("  capture's other blocks are still usable. Override with "
                      "--allow-software-gpu.", file=sys.stderr)
                continue
            rec["renderer"] = g.get("unmaskedRenderer")
            rec["vendor"] = g.get("unmaskedVendor")
            # Rendered output, kept separate from the capability tables. Two
            # GPUs may agree on every table and still rasterise differently;
            # only equal render hashes justify swapping one for the other.
            rec["render_sha256"] = sha({
                "canvas": (content.get("canvas.2d") or {}).get("pixels_sha256"),
                "webgl1": (content.get("webgl1") or {}).get("pixels_sha256"),
                "webgl2": (content.get("webgl2") or {}).get("pixels_sha256"),
            })

        d = args.out / block
        d.mkdir(parents=True, exist_ok=True)
        path = d / ("%s-%s.json" % (label, rec["content_sha256"][:12]))
        path.write_text(json.dumps(rec, indent=2) + "\n")
        written.append((block, path, rec))

    print("decomposed %s" % args.capture.name)
    for block, path, rec in written:
        extra = ""
        if block == "gpu":
            extra = "  renderer=%s" % rec.get("renderer")
        print("  %-9s %-58s caps=%s%s"
              % (block, str(path), rec["identity_sha256"][:12], extra))
    return 0


# UNASSIGNED, deliberately:
#   battery            power state, not device identity; its own axis
#   headers.echo*      derived from locale + platform, not independent
#   navigator.scalars  spans blocks; its fields are sourced from the block that
#                      owns each one rather than carried whole
#   storage.estimate, timing.resolution, network.connection, permissions.states
#                      measured volatile, or state rather than identity
#   memory.heap        carried in hardware, but see the note in compose about
#                      jsHeapSizeLimit being a V8 constant, not a host fact
if __name__ == "__main__":
    sys.exit(main())
