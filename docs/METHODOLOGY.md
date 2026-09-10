# Methodology

How correctness is defined, evidenced, and verified in this project. Every
other document defers to this one.

## 1. The falsifiable claim

"Stealth" is not a property that can be measured, so it is not a unit of work
here. It is replaced by a claim that can be falsified:

> A browser is correct when every observable it emits equals the value that a
> **named reference device** emits.

The reference device is a row in the fingerprint corpus. Correctness is a diff
against that row, not a judgement about whether something "looks like a bot".
This is the whole reason the project is tractable: a 500-row surface map with
opinions attached does not converge, and a 500-row surface map with a ground
truth attached converges mechanically.

## 2. Axioms

**A1 — Provenance.** Every value must be produced by the code path that would
have produced the real value. Patch the emitter, never the accessor.

No JavaScript injection. No CDP override. No wrapper objects, `Proxy`, or
redefined property descriptors. A value that is correct only when read the
obvious way is not correct. Getting this right makes `Function.prototype.toString`
probing, descriptor comparison, prototype-chain walking, worker-scope re-reads
and cross-realm checks irrelevant *by construction* rather than by
counter-measure — there is nothing for them to find, because nothing was
wrapped.

**A2 — Coherence, not concealment.** Correct means "equal to reference device
D", never "less detectable". A patch that makes a value harder to read, noisier,
or absent has not made it correct. Every patch cites the corpus row it
reproduces.

**A3 — Determinism.** Real hardware is deterministic. Two identical canvas
renders on real silicon are bit-identical; two reads of `deviceMemory` agree;
an audio graph rendered twice yields the same samples.

Therefore Apostate injects **no per-call randomness**. Variation lives at the
profile boundary — a different profile is a different device — never inside a
session. Randomised noise is itself a tell: it is unstable within a session,
it breaks returning-visitor consistency across sessions, and it is a behaviour
no physical device exhibits. Where a value cannot be derived, it is **replayed**
from the corpus capture, not synthesised.

## 3. Evidence tiers

Every assertion recorded in the ledger carries a tier. Tiers exist because the
inputs to this project have wildly different reliability and mixing them
silently is how a stealth project accumulates confident errors.

| Tier | Meaning | Examples |
|------|---------|----------|
| **T0** | Measured | A corpus row; a value observed on real hardware here |
| **T1** | Primary | Chromium source; W3C/WHATWG/IETF spec text; peer-reviewed paper |
| **T2** | Secondary | Competitor repo, vendor blog, forum claim, another tool's patch set |
| **T3** | Inference | Reasoning from T0/T1 without direct observation |

**The closing rule: a ledger row may be marked `resolved` only on T0 or T1
evidence.** T2 may open a row; it may never close one. T3 may never close one.

This rule is what quarantines contaminated inputs automatically, with no
manual triage. Anything imported from a source whose method we cannot audit
enters at T2 and stays open until re-derived from Chromium source or measured
against a real device.

## 4. Ground truth: what we actually hold

Stated honestly, because building on an assumed corpus is the expensive
mistake.

Ground truth here means **captures we took ourselves**, with the pipeline in
`capture/`, on devices whose owner consented. That is the only class of
evidence admitted at T0.

| Class | Own captures | Notes |
|-------|--------------|-------|
| macOS desktop | 0 | Hardware on hand; blocked on the capture pipeline |
| Windows desktop | 0 | Hardware pending |
| Android mobile | 0 | |
| iOS | 0 | |

The three files in `resources/fingerprints/` are **schema templates, not
captures** — see `resources/fingerprints/PROVENANCE.md`. They must never be
cited as T0.

So the corpus is empty, and building it is the project's first task rather than
an assumed input. Two sources, in order:

1. **Devices we control.** Captured directly, full provenance, highest
   confidence.
2. **Consented community contributions.** A public capture page where a person
   chooses to submit their own device profile. This scales with adoption, it is
   the honest way to build breadth, and it is the same tool as (1) so the data
   is directly comparable.

### On third-party corpora

Fingerprint datasets belonging to commercial vendors are **not redistributed by
this project and are not admitted as T0**, regardless of how they were
obtained. This is partly a licensing question and partly a quality one: such
sets typically store digests rather than renders, so they can verify a value but
never reconstruct one, and reconstruction is exactly what replay requires.

Where such a set is consulted privately it may inform *priors* — which GPU
renderer strings occur in the wild, how a field is distributed — and those
priors are recorded as T2. They never close a ledger row and they never ship.

## 5. The registries

Four artifacts. All work attaches to one of them.

1. **Surface Ledger** (`ledger/surfaces.jsonl`) — the detector-side map. One
   row per observable, carrying the decision fields that force a verdict.
2. **Emitter Index** (`ledger/emitters.jsonl`) — the Chromium-side map. One row
   per `(file, symbol, process)` that produces a value. Many-to-many with the
   ledger; the join between the two is the real output of the mapping phase.
3. **Profile Schema** (`config/profile.schema.json`) — the contract between
   them. **Derived, never invented**: every `spoof` verdict in the ledger
   demands exactly one profile field, and the schema is generated from that
   set. It stays empty until the ledger is closed.
4. **Coherence Graph** (`ledger/coherence.jsonl`) — edges between ledger rows
   that must agree with each other. User-agent ↔ client hints ↔ platform ↔ GPU
   renderer ↔ font set ↔ screen geometry ↔ timezone. Nearly every real failure
   is a broken edge rather than a wrong value, so edges are first-class rows
   with their own owner, not comments on other rows.

### A profile is only replayable by the build it was captured from

Apostate does not spoof its own browser version: the binary really is the
Chromium it reports, and claiming another would require behaving like that
version — every feature-detection difference would contradict the claim.

It follows that a capture is bound to a build. Our first V3 run compared a
reference taken on Chrome 152.0.7977.76 against a binary built from
152.0.7977.82, and the version-bearing fields differed in ways no patch should
ever fix. The conformance runner reports that separately rather than counting it
as failure.

So a capture must record the exact browser build, and a reference is only a
valid V3 target for a binary of the same version. Rebasing onto a new Chromium
means re-capturing the references, not just re-applying the patches.

### The loader is faithful: 30/30 on a self-consistency test

Before reading anything into a cross-platform score, the loader itself has to be
shown correct. The control for that holds every platform variable constant:
capture the browser with no profile, derive a profile from that capture, run the
same binary again with it, and diff the two.

**30 of 30 probes conform.** Same host, same build, no platform difference in
play — so the profile round-trips through capture, derivation and replay without
loss.

That makes the cross-platform number interpretable. A gap there is a property of
the host, not a defect in the loader, and the two can be reported separately
instead of being confounded.

The test found one defect, and it was in the harness: the worker header echo was
not being normalised for environment-dependent values, so two captures of one
machine differed because they had used different ports. The main-thread echo had
been normalised months of iterations earlier and the worker variant was added
later without it.

### Same-host control and its limits

A same-host control isolates the profile loader from differences in installed
resources and rendering implementations.

A Linux host was made to present a *different Linux device* — eight cores rather
than thirty-two, 16GB rather than 32, 2560x1440 rather than 800x600, an NVIDIA
RTX 3060 rather than SwiftShader, Europe/Warsaw with Polish language
preferences, dark mode, a fine pointer with hover — and the result diffed
against the same host's own unprofiled capture.

**Every field that differed was one the profile set.** Fonts, canvas, client
rects, codecs, audio render, WebGPU, keyboard layout, media devices, speech
voices and WebRTC capabilities all conform exactly. This establishes a control
for the measured inputs.

This test does not establish equivalence to the named NVIDIA device or to every
device running the same OS. The reference still came from the same host. A
different GPU, font collection, driver, or audio implementation requires its own
functional measurements even when the OS agrees.

### Historical cross-platform baseline and remaining work

A Linux host was made to present a macOS M4 Max, and the result diffed against
that machine. Against 319 differing fields with no profile, the profile closed
77. These are historical baseline counts, not the current conformance score or
a proof that the remaining behavior cannot be implemented.

| kind | fields | why |
|---|---|---|
| closed by the profile | 77 | values the browser chooses |
| **fonts** | 85 | the host does not have the font files |
| **WebGL parameter tables** | 78 | see below — the measurement is confounded by the test host having no GPU |
| canvas renders | 8 | follows font resources, shaping, metrics, and raster implementation |
| codecs, WebGPU adapter, voices, keyboard, media devices | 17 | OS-provided resources the host lacks |
| audio render | 4 | CPUID-selected FFT kernel and host libm |
| still patchable | ~50 | headers, layout, remaining media queries |

Cross-OS execution is a product requirement. A native reference machine is an
oracle and a control; requiring that OS for execution does not close a
cross-platform failure.

Missing resources and differing implementations require different fixes. A
font file may be provisioned. Font selection, outline extraction, rasterization,
FFT arithmetic, and graphics operations may require portable implementations
with the reference's behavior. An OS gate in stock Chromium establishes how
that build behaves; it does not establish that another implementation is
impossible.

These implementations must satisfy the same axioms and verification gates as
every other patch. Advertising a graphics limit requires executing operations
at that limit. Advertising a codec or speech voice requires a working provider.
Matching one captured digest does not establish parity for other inputs: retain
held-out inputs, exceptional values, repeated calls, and native-path controls.
Unimplemented or unmeasured behavior remains a reported failure. Neither a
same-OS run nor a change to the scoring rules can close it.

That the font half is provisionable is now measured, not assumed. Chromium on
Linux asks fontconfig, and fontconfig answers from whatever it is pointed at:
running the browser with `FONTCONFIG_FILE` set to a config naming one directory
made it enumerate exactly the families in that directory. Pointed at a directory
holding two families it reported those and nothing else; pointed at a config
whose font path excluded the system directories it reported none at all.

So font enumeration is a deployment decision with a known mechanism, and
`scripts/make-fontconfig.py` generates the configuration and reports which of a
reference device's families a directory still lacks.

**Removal is worth much less than adding, and an earlier draft of this section
overstated it.** It claimed that a Linux host enumerating DejaVu Sans, Liberation
Sans and Noto Sans on a macOS profile was itself the tell. That does not hold:
those three are freely downloadable, LibreOffice installs Liberation and DejaVu
on any platform, and Noto arrives with all sorts of software. Real machines carry
long tails of fonts their owners installed. A rule keyed on the *presence* of a
foreign family would fire on ordinary users.

The defensible signal is the inverse — the **absence of families the claimed
platform cannot be without**. Menlo, Monaco, Zapfino, PingFang SC and Helvetica
Neue ship with macOS and cannot be uninstalled. A machine claiming macOS that
lacks them is not a Mac, and no amount of user behaviour explains it away.

The measurement bears that out. Of 76 families compared between the reference
Mac and an unprovisioned Linux host, 47 already agree on metrics. Of the 29 that
differ, 26 are families absent from the host — recovered by installing them —
and exactly 3 are the Linux families present here and not there. So removal buys
three fields and is tidiness; installation buys twenty-six and is the work.

**Metric-compatible substitution already works, and only for metrics.** The
unprovisioned host reports Arial, Courier, Courier New, Helvetica, Times and
Times New Roman as present with metrics identical to the Mac's, because
fontconfig aliases them to Liberation and DejaVu, which were designed as
metric-compatible substitutes. Generic `serif`, `sans-serif` and `monospace`
resolve to identical widths — 692, 720 and 636 — on both machines.

That is why the split between probe families matters. A probe that measures text
*width* is satisfied by a metric-compatible substitute. A probe that reads
rendered *pixels* is not: the glyph outlines differ, which is the 32% of text-band
pixels that differ between the two machines. Installing the real files is what
closes the second, and nothing closes it short of that.

**The WebGL figure is not yet trustworthy, and the reason matters.** Every
measurement behind it was taken on a build host with no GPU, through
ANGLE-on-SwiftShader, against a Windows reference that was itself a GPU-less VM
running Microsoft Basic Render Driver. Software rasteriser against software
rasteriser.

The differences that result are mixed in direction. Four limits are higher on
our side and could be clamped down safely — clamping down is always safe,
because a page can only falsify a limit by exceeding it. But three are *lower*:
MAX_TEXTURE_SIZE, MAX_RENDERBUFFER_SIZE and MAX_VIEWPORT_DIMS all read 8192,
which is SwiftShader's cap, against 16384 and 32767 on the reference. Raising
those is exactly the move that gets falsified by a second code path — allocate
the texture and the claim collapses.

But a Linux host with a real GPU reports 16384 or more natively, because it is
the same class of silicon a Windows machine would be reporting through D3D11.
So an unknown share of this row is the test environment rather than the
platform, and the honest position is that the cross-OS WebGL gap has not been
measured yet. Measuring it needs a Linux host with a discrete GPU and a
same-GPU Windows capture to compare against; until then this row should be read
as an upper bound, not a ceiling.

None of this was assumed. The comparison that produced it — an unprofiled build
against the same reference — is the control, and it is worth re-running whenever
the claim about what is reachable changes.

## 6. Verification tiers

A change is not done until it passes the tier its ledger row names.

| Tier | Gate | Cost |
|------|------|------|
| **V0** | Schema and lint: ledger rows well-formed, patch applies, series ordered | free |
| **V1** | Single translation unit compiles against the pinned build dir | seconds |
| **V2** | Full build succeeds, binary launches, smoke suite passes | hours |
| **V3** | **Corpus conformance**: launch with profile P, collect, diff against P's corpus row | minutes |
| **V4** | Live detectors and heretic | minutes |

**V3 is the scoreboard.** The corpus is already expressed in the same schema a
collector produces, so per-surface pass/fail is a mechanical field diff rather
than an opinion. The collector is built before the first patch, not after.

Builds are checkpoints, never a debugging loop: work is gated at V0/V1 and
batched, so that entering V2 is an expectation of success rather than an
experiment.

## 7. Working rules for parallel work

The map is large enough to need fan-out, and fan-out is how a project like this
becomes incoherent. Four rules hold it together.

1. **Fixed schema, never prose.** Every unit of delegated work returns JSON
   validating against a schema in `ledger/schema/`.
2. **No direct writes.** Workers emit proposals to `ledger/inbox/`. A single
   arbiter merges into the ledger. This prevents write races and keeps one mind
   responsible for consistency between rows.
3. **Shard by category**, using the categories already present in the data, so
   that a shard is semantically coherent and most coherence edges stay inside
   one shard.
4. **Escalate beats drop.** Marking a surface `out-of-scope` requires a cited
   reason. Uncertainty is recorded as `escalate`. Dropping a real surface costs
   a detection; a false keep costs one review.
