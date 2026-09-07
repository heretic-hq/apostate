# Subsystem 13 — Profile Composition

How a corpus of captures becomes many coherent profiles without inventing a
single value.

## The dilemma this exists to escape

Two obvious designs both fail, in opposite directions.

**Synthesise from a seed.** Inject PRNG noise into canvas readbacks, WebGL
buffers and layout scale factors. This gives unlimited profiles and corrupts
engine invariants doing it: altering low-order canvas bits fails bitstream
verification, and scaling a viewport by an arbitrary float desynchronises
Chromium's 60-to-1 subpixel quantisation so `getBoundingClientRect` and
`getClientRects` disagree with each other. The browser then contradicts itself,
which is a harder signal than any wrong value.

**Replay a monolith.** Ship captures bit-for-bit. Every invariant holds, and a
thousand sessions of one capture emit one fingerprint — a static cluster that is
trivially attributable.

Trying to fix the first by hand-wiring the relationships leads to an exponential
graph: memory tiers, core counts, ANGLE limit tables, audio buffer sizes and
layout rounding units all have to agree simultaneously, and each new surface
multiplies the constraints.

## What we do instead

Cut captures along the seams where real hardware is genuinely configurable, and
compose profiles from blocks that each came from a real machine. Every value
shipped was measured on silicon. The only thing composed is the *combination*,
and only across seams where that combination also exists in the world.

Six blocks:

| block | owns | why the seam is real |
|---|---|---|
| `platform` | OS identity, fonts, voices, codecs, wasm, API surface, audio render | fixed by the OS build; not configurable |
| `gpu` | renderer and vendor strings **plus** capability tables **plus** rendered output | atomic on purpose — see below |
| `display` | screen tuple, DPR, gamut, HDR, built-in capture devices | one GPU ships in several chassis; any machine drives an external panel |
| `hardware` | core count, installed memory, audio buffer | chosen at purchase for an otherwise identical machine |
| `locale` | timezone, languages, keyboard layout | the user's setting, not the device's |
| `theme` | dark mode, contrast, system colours | the user's setting, independent of the panel |

`css.media` is split by feature rather than assigned whole, because it answers
two unrelated questions in one probe: what the panel can do (`color-gamut`,
`dynamic-range`, `pointer`) and what the user chose (`prefers-color-scheme`,
`prefers-contrast`). A p3 HDR display is equally plausible in light or dark mode,
so those are separate axes.

## Why the GPU block is atomic

The tempting move is an equivalence class: group GPUs whose ANGLE backend
returns identical capability tables, then swap renderer strings freely within
the class. The premise is true for macOS — `DisplayMtl::ensureCapsInitialized`
sets `max2DTextureSize`, `maxVaryingVectors` and the rest under
`#if TARGET_OS_OSX`, as compile-time constants, and every Apple-family threshold
used in the caps and extension paths is ≤ 6 while M1 already reports Apple7. So
M1 through M4 Max resolve every branch identically. Intel Macs have no Apple
family at all and lose the ASTC and ETC extensions, which is where the boundary
actually falls.

We do not use it as a class anyway, for one reason: **identical capability
tables do not imply identical rendered output**. Two GPUs can agree on every
queryable limit and still rasterise a canvas differently. Swapping a renderer
string across that gap would produce a machine whose claimed GPU and actual
pixels disagree — the exact class of self-contradiction the whole design exists
to avoid.

So the string never travels without the table and the pixels that belong to it.
The class question is kept open and made answerable instead: every gpu block
records

- `identity_sha256` — the capability tables with the renderer and vendor strings
  removed, so two captures of the same silicon hash equal despite different names
- `render_sha256` — the canvas and WebGL pixel hashes

When a second Apple Silicon capture arrives, equal `identity_sha256` **and**
equal `render_sha256` proves interchangeability by measurement. Until then
nothing is asserted, and nothing is lost.

## Compatibility

Blocks may only combine if they were captured on the same operating system.
Conservative on purpose: a macOS display tuple with a Windows GPU is not a
machine, and the failure is not a wrong value but an impossible device.

It follows that diversity arrives with captures rather than with cleverness. One
macOS capture yields one macOS profile. The second yields the cross product of
both — and because the axes are orthogonal, the count grows multiplicatively
while the capture count grows by one.

## Evidence tiers

A block is `measured` when every field came from a capture.

`measured-floor` marks a block whose memory figure is `deviceMemory`'s bucket
rather than installed RAM. `deviceMemory` rounds to a power of two and
saturates, so every machine above the top bucket reports the same number. This
was harmless while the only consumer re-bucketed it, and stopped being harmless
when the incognito storage quota started deriving from the same field. Such a
block is usable, and its memory value is a lower bound, not a measurement.

`catalogue` is reserved for a hardware block built from a manufacturer's
shipping configuration for a chassis we have captured — a real machine we did
not personally hold. Marked, counted separately, never silently mixed.

## Tools

```
scripts/decompose-capture.py CAPTURE.json     # capture -> blocks
scripts/compose-profile.py --list             # what is in the corpus
scripts/compose-profile.py --enumerate        # coherent combinations
scripts/compose-profile.py --gpu X --display Y ... --out profile.json
```

The decomposer refuses any capture that reports automation signals, was taken
outside a secure context, or has a failed probe — a partial capture makes
partial blocks, and a bad block is worse than a missing one because it composes
silently.

Round-trip is verified: decomposing the M4 Max reference and recomposing it
reproduces the hand-built profile identically on every field.
