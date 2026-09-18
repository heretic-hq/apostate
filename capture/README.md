# Capture pipeline

Produces the ground truth everything else is measured against, and doubles as
the V3 conformance harness. One codebase, two modes:

- **capture** — run on a real device to record what it actually emits (T0)
- **conform** — run inside Apostate and diff the result against a corpus row

They must be the same code. A conformance harness that measures differently
from the capture tool compares two incompatible number systems, which is how
the previous collector failed.

## Design rules

These exist because the collector that produced the Apple fingerprints in
`resources/fingerprints/` violated every one of them.

1. **Measure or admit failure. Never fabricate.** Each probe returns
   `{ok, value, error}`. A probe that throws records the error and a `null`
   value. There are no fallback constants and no `catch {}` that substitutes a
   plausible number — a silently-zeroed audio buffer is what made three
   different devices produce one identical fingerprint.
2. **Errors are data.** A capture with failed probes is valid and useful. A
   capture that hides them is poison, because it looks complete.
3. **Raw before derived.** Store actual bytes: canvas PNG, audio float samples,
   full client-rect coordinates, every queried WebGL parameter. Digests are
   computed afterwards, off-device, from the stored raw. A SHA-1 can verify a
   value but can never reconstruct one, and replay needs reconstruction.
4. **Read twice.** Every render probe runs twice in one session and stores both
   results. Axiom A3 says real hardware is deterministic; this is the
   measurement that proves it per-field, and it tells us a field's natural
   variance before we can call a V3 difference a failure.
5. **Context is mandatory.** Device, OS, browser build and channel, device
   pixel ratio, GPU driver, and whether anything automation-shaped was present.
   A capture without provenance is not evidence.
6. **No automation.** Captures are taken in a normal headed browser opened by a
   person. Running the collector under CDP would record the artifacts we exist
   to remove.

## Layout

```
schema/capture.schema.json   Native lossless capture format
collector/                   The measurement page. No dependencies, no build step
server/receive.py            Local receiver; writes raw captures to disk
derive/conform.py            Diffs one capture against another — the V3 gate
derive/coherence.py          Decides the invariants a diff cannot — the coherence gate
coherence/probe.html         Operands for the single-binary invariants
collect-unattended.py        Takes a capture on a host with no display
```

## Taking a capture

```sh
python3 capture/server/receive.py --out resources/fingerprints/raw
```

Open the printed URL on the target device in a normal browser window and submit.
The receiver writes `<sha256 of the submitted bytes>.json`, records its
admission decision under `admissions/`, and prints a probe summary including
anything that failed.

Then load it into the oracle:

```sh
python3 corpus/build_oracle.py --src resources/fingerprints/raw
```

## Launch flags are part of the measurement

A capture describes the browser *as launched*, and some flags change what is
being measured rather than how it is observed.

`--disable-gpu` is the one that has already caused a wrong result here. It makes
`CollectGraphicsInfoGL` return early with the literal strings `"Disabled"` for
vendor, renderer and version, without ever calling `glGetString` — while WebGL,
which reaches the driver through the command buffer, still reports a real ANGLE
string. So a capture taken that way describes a configuration no real user runs,
and it silently exempts the entire GPUInfo path from whatever is being tested.

A host with no display needs more care than it looks. `--headless` is not the
answer: the receiver rejects any capture whose user agent carries a `Headless`
marker, because a headless UA is the loudest automation signal there is. Run a
real headed browser on a virtual display instead. `collect-unattended.py` does
that, and handles the two host facts that otherwise make every such capture
inadmissible:

```sh
python3 capture/collect-unattended.py --label <name> --out <dir>
```

The browser has to be the release major, or admission refuses the capture on
its version alone. A distribution's Chrome package is whatever is current, so a
server is as likely to be behind as ahead. Google's apt repository carries only
the newest stable, but older stable `.deb`s stay in the pool. Unpack one beside
the installed browser rather than over it, and point the driver at it with
`--chrome`:

```sh
curl -O https://dl.google.com/linux/chrome/deb/pool/main/g/google-chrome-stable/google-chrome-stable_<version>-1_amd64.deb
dpkg-deb -x google-chrome-stable_<version>-1_amd64.deb /opt/chrome<major>
```

`build/CHROMIUM_VERSION` is the build to match. The exact patch release is not
always in the pool; the nearest one of the same major is, and
`scripts/import-capture.py` admits that as `same-major` and records the
difference rather than pretending it away.

Under Xvfb the only GL driver is Mesa llvmpipe, so Chrome's software-rendering
blocklist disables `webgl` and `webgpu`, `getContext('webgl')` returns null, and
the `webgl1` and `webgl2` probes measure nothing at all — which the receiver
rejects, correctly. `--enable-unsafe-swiftshader` permits WebGL's software
fallback, and the probes then measure a real software backend. Prefer it to
`--use-gl=angle --use-angle=swiftshader`: that pair replaces the GL driver, so
on a host which does have a GPU it quietly produces a software capture from
hardware — the `--disable-gpu` mistake in different clothes. The fallback
opt-in changes nothing where a GPU works.

The second fact is `screen.details`. It refuses to prompt mid-capture, so the
window-management permission has to exist before the run and nobody is present
to click Allow. The driver writes the grant into the fresh profile, under the
old name Chrome still stores it by, `window_placement`.

A capture taken this way describes SwiftShader and not the host's GPU, so the
driver prints the renderer it measured and the two cannot be confused.
`scripts/import-capture.py` needs `--allow-software-renderer` to admit one, and
no anchor may claim it as hardware.

`--no-sandbox` is unavoidable when running as root and is itself a deviation
from a normal launch; prefer a non-root user where possible.

Captures taken before this was understood are still valid for the surfaces they
were used for — WebGL values go through the command buffer and were genuinely
measured — but they are not general-purpose references, and their `GPUInfo`
values mean nothing.

## Measuring variance

`conform.py` is also how a field's natural variance gets established: point it
at two captures of the *same* device and everything it reports is variance
rather than defect. There is no separate tool, because "does this device match
itself" and "does Apostate match this device" are the same comparison.

This is not optional rigour. A single capture cannot distinguish spoofing from
variance, having nothing to measure variance against — we withdrew a finding
that way, after two captures of one MacBook five hours apart reported different
`availHeight` values because the dock had moved.

Measured so far on an M4 Max, stock Chrome 152: 29/29 probes conform within a
session, and 29/29 across separate launches five hours apart, canvas, WebGL and
audio renders byte-identical throughout.

On a display-less Linux host running SwiftShader under Xvfb, stock Chrome 152:
39/39 probes conform across two separate unattended launches, canvas, WebGL and
audio renders byte-identical. A software rasteriser is as deterministic as the
hardware here, which is what makes such a host usable as a conformance runner
even though it is useless as a hardware reference.

## Collector generations, and what makes two of them comparable

Every capture records the sha256 of the `collector.js` that measured it.
`import-capture.py` refuses to admit a capture the collector in this tree did
not produce, and `conform.py` used to refuse to compare two captures whose
hashes differed at all. The first rule is right. The second was far stronger
than the evidence, and the cost was never tracked: the collector has changed
five times and no capture was ever migrated, so of the 26 captures admitted
across `resources/fingerprints/raw/` (22) and `resources/fingerprints/self/`
(4, our own browser's output) only eight could be compared to anything.

| collector  | captures | what is in it                                                |
|------------|----------|--------------------------------------------------------------|
| `6385f36c` | 9        | the first M4 Max and Linux sweep, plus four runs of our own browser |
| `6b9f3004` | 5        | M4 Max battery/incognito matrix, `win-intel-uhd630`           |
| `c856a603` | 3        | three early `apple-m4-max` reads                              |
| `91fe5c48` | 1        | `apple-m4-max-20260907T083721Z`                               |
| `a19ad58d` | 8        | the current generation: `m4-max-chrome-20260908T163229Z`, `windows-chrome-20260910T140813Z`, four `linux-nvidia` hosts and two `windows-nvidia` hosts |

**Comparability is now decided per probe, and measured.**
`scripts/build-collector-matrix.py` extracts every revision of the collector
reachable from git, slices each into its probe registrations and module-level
helpers, resolves which helpers each probe's body reaches transitively, and
digests the probe's source together with that closure. Two generations measure
a probe the same way exactly when the digest matches. The result is checked in
as `corpus/collector-probe-matrix.json` and regenerating it reproduces the file
byte for byte, so the claim is falsifiable by re-extracting the collectors.
`conform.py` compares the probes whose digests match and skips and REPORTS the
rest; a collector the matrix does not contain is still refused outright.

What it found, for the two pairs that matter:

| pair | comparable | implementation differs | removed | added |
|------|-----------:|-----------------------:|--------:|------:|
| `6b9f3004` → `a19ad58d` | 31 | 5 (`clientrects`, `codecs.media`, `css.media`, `fonts.detected`, `permissions.states`) | 0 | 8 |
| `6385f36c` → `a19ad58d` | 30 | 6 (the same five plus `api.surface`) | 0 | 8 |

The empirical check on it is the one device we captured twice across a
generation boundary: the Sri Lanka Windows box on `6b9f3004` and `a19ad58d`.
Holding it to itself across the two collectors now compares 26 probes and
reports 13, and every one of the five implementation-differing probes it skips
would otherwise have produced findings — 12 leaf differences in `clientrects`,
10 in `codecs.media`, 6 in `permissions.states`, 3 in `css.media` — that are
properties of the instrument, not of the machine.

**The limit of this evidence, stated because it is not the limit the original
plan asked for.** A source digest proves the probe itself was not rewritten. It
does not prove that a probe ADDED later cannot perturb an earlier one through
timing, GPU memory or permission state; only running both collectors against
one binary on one host would settle that. `conform.py` prints this caveat
beside every skipped probe rather than implying more than it measured.

`91fe5c48` is not in git history: a locally-modified collector, run once with
some checks deliberately disabled, never committed. It has no source to digest,
so it stays stranded and `corpus/collector-probe-matrix.json` records why in
its `unmatrixable` section. The alternative — trusting the probes whose values
match a sibling capture of the same device — was rejected: agreement with the
value under test is not evidence about the instrument, and "some checks
disabled" means we cannot name which probes were weakened. Nothing is lost by
it; the M4 Max it measured is also captured on the current collector.

**Adding a probe to the collector still costs something**, but now it costs
exactly the probes it touches rather than the whole reference set, and the
matrix says which ones.

## Deciding coherence edges

`derive/coherence.py` decides the `ledger/coherence.jsonl` invariants that a
field diff cannot, and is named in those rows as
`capture/derive/coherence.py:<edge id>`. Some relations hold over window
position and size, which `conform.py` excludes from comparison by design because
window geometry is the user's choice rather than the device's identity; others
need an observable no capture records. Both kinds have to be decided inside one
running browser against no reference at all.

```sh
capture/derive/coherence.py live --browser out/Release/chrome --profile PROFILE.json
capture/derive/coherence.py capture resources/fingerprints/raw/<name>.json
capture/derive/coherence.py pair A.json B.json
```

`live` serves `coherence/probe.html`, launches the browser at it headed — under
`xvfb-run` on Linux, the configuration `scripts/run-v3.sh` uses, because every
clause about a window rect is meaningless without a window — and decides the
single-binary edges. `capture` decides `coh.render-determinism` from one
capture's two reads. `pair` decides the two-profile edge.

The outcome vocabulary matters more than it looks. `INCONCLUSIVE` means the
right inputs were supplied and the run did not exercise the invariant: two runs
that claim the same audio hardware agree on every render hash, and reporting
that as a pass would be a check that passes on correct and broken input alike.
`SKIP` means this mode cannot decide this edge at all. Neither is a pass, and
only `INCONCLUSIVE` affects the exit status (2).

## Format

The capture format is our own, defined in `schema/capture.schema.json`. It is
deliberately not modelled on any vendor's schema.

The reason is rule 3. Vendor fingerprint formats store a digest of each measured
render plus a short lossy tail. That is enough to tell whether two devices
differ, and never enough to reproduce what either one drew. Since replay is the
entire premise of the project, a format that cannot round-trip a render is not a
format we can build on. We store the PNG and the float samples.

We do not redistribute anyone else's fingerprint corpus, and no such corpus is
admitted as ground truth. See `docs/METHODOLOGY.md` §4.
