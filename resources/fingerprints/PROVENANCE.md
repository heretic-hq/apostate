# Provenance of files in this directory

Under axiom A2 every patch cites the corpus row it reproduces, so the tier of
each file here is load-bearing. **None of the three files in this directory is a
capture.** They are schema templates. They may be used to understand the field
layout. They must never be cited as T0 evidence, and no profile may ship from
them.

## Evidence

All three files carry placeholder values in the four fields that encode measured
device output (`canvas`, `webgl`, `audio`, `rectangles`). Each of those fields is
a 64-byte digest followed by a block of sampled bytes; the sample block is what
a real device actually produced.

| Check | These files | Real captures (n=2,995 Android) |
|---|---|---|
| `audio` sample block | constant `0x88` × 100, in all three | 36–37 distinct byte values |
| `rectangles` sample block | byte-identical across all three | varies per device |
| `canvas`/`webgl` sample block | identical across both iPhone files | varies per device |

A constant audio block is not a value any audio pipeline emits. Identical
`rectangles` across an iPhone, an iPhone, and an M4 Max is not a coincidence —
it is one template copied three times.

Metadata corroborates:

- `02996-apple-m4-max.json` — user agent is `HeadlessChrome/152.0.0.0`. Whatever
  produced this was headless Chrome, which is the exact condition we exist to
  eliminate. `width`/`height` are `756×469`, a window size, not a screen.
- `02996-apple-iphone-15-chrome.json` — `tags: ["Linux","Safari","Desktop"]` on
  an iPhone Chrome profile. Three wrong labels.
- `02996-apple-iphone-15-safari.json` — claims iOS `18_7` with `Version/26.5`.
  Those two do not ship together.

## Where the real ground truth is

`~/fingerprints/fingerprints/` — 2,995 Android captures, Chrome 132–146. That is
the T0 corpus, and `corpus/build_oracle.py` is what makes it queryable.

Desktop and iOS ground truth does not exist yet. See `docs/METHODOLOGY.md` §4.
