# Provenance

Under axiom A2 every patch cites the capture it reproduces, so the tier of each
file here is load-bearing. Tiers are defined in `docs/METHODOLOGY.md` §3.

`raw/` contains both admitted references and legacy historical captures. Only the
`capture_version: 2` files listed in **Current captures** are admitted as T0. A
legacy file that remains in this directory is non-admissible historical material,
not ground truth for a profile or patch. Admission still requires a real headed
capture, owner consent, a secure context where the probe requires one, and no
automation suspicion.

Admission is not a property of the filename. Each admitted file has a decision
record at `raw/admissions/<sha256-of-the-file>.json` naming the decision, the
reason, and the `context` it was decided on. A capture in `raw/` with no such
record — or whose record says `rejected` — is not a reference, and
`scripts/build-anchors.py` skips it by name rather than inferring anything from
the directory it sits in; the one narrow exception is recorded per capture in
`corpus/capture-tiers.json` and described below. The two references admitted
before this convention existed carry their records in
`corpus/blocks/admissions/` instead, written by
`scripts/decompose-capture.py`; both locations are authoritative.

An `accepted` record is no longer something a caller can assert. It used to be:
`persist_admission_decision` took the decision and the reason as arguments and
validated nothing, so `raw/admissions/70fd8f09….json` recorded
`stock-linux-20260907T150627Z.json` as accepted with the reason "capture passed
admission checks" — for a capture that fails five of them: `automation_suspected`
is true, `automation_signals` names the Headless token in its UA,
`capture_version` is 1, `secure_context` is absent, and the `screen.details`
probe did not complete. That record has been deleted and the writer now derives
the verdict: recording an acceptance requires the capture's own bytes, requires
them to hash to the digest the record addresses, and requires the admission
gate to pass on them. A caller with no capture to show cannot record an
acceptance at all.

Deleting it left one real dependency to carry: the tree's only measurement of
stock Chromium's software rasteriser is that capture, and
`corpus/anchors/linux-swiftshader-google-6922d61bab83.json` is built from it and
is cited by patches 0027, 0038, 0052, 0091, 0102 and 0104. So the allowance is
written down instead of laundered. `corpus/capture-tiers.json` records an
`anchor_exception` on that capture naming the single backend it covers, and
`scripts/build-anchors.py` checks that name against the backend it MEASURED —
an exception for a software rasteriser cannot be spent on a hardware anchor,
and it cannot make the capture a reference for anything else.

## `self/` is not a reference store

`self/` holds captures of **our own browser**: `apostate-linux`,
`apostate-profiled`, `apostate-m4max` and `macos-claim`. They are kept because
before/after evidence of our own behaviour is exactly what a patch has to cite,
and they are kept out of `raw/` because conforming against them is circular —
they measure what we emitted, so agreement proves only that we agree with
ourselves. Nothing that globs the reference store can reach them.

## What each capture is

`corpus/capture-tiers.json` records, per capture and keyed by the sha256 of its
bytes, whether it is a full physical device (`physical-full`), a VM with a real
passthrough GPU whose GPU cluster alone is a measurement
(`physical-gpu-only`), or not a reference at all (`not-a-reference`: our own
output, or a session with automation suspicion). A tier is a claim about the
world that no probe settles, so each entry carries the measurement that
supports it and what would have to be observed to refute it. A capture with no
entry fails closed: `capture/derive/conform.py` refuses it as a reference
rather than assuming it is trustworthy.

## Reading a capture

Each file carries its own provenance in `context`:

- `collector_sha256` — which collector version measured it. Captures from
  different collector versions are compared PER PROBE, against the source
  digests in `corpus/collector-probe-matrix.json`: probes whose implementation
  is byte-identical across the two generations are compared and the rest are
  skipped and reported. A collector with no entry in that matrix is refused
  outright, which today means `91fe5c48` — a locally-modified collector that
  was never committed, so there is no source to digest.
- `automation_suspected` — true if anything automation-shaped was present.
  **A capture with this set is not T0**, whatever else it contains.
- `repeat` — a second reading of every deterministic probe from the same
  session. Fields that differ between the two are not stable on that hardware,
  and a V3 difference in one of them is not by itself a defect.

Every probe records `{ok, value, error}`. A probe that failed says so and holds
a null value. Nothing here is ever backfilled with a plausible substitute.

## Current captures

| Device | Platform | File | Notes |
|---|---|---|---|
| `apple-m4-max` | macOS 26.6.2, Chrome 152.0.7977.83 | `raw/m4-max-chrome-20260908T163229Z.json` | Admitted v2 reference; 44/44 probes measured. On the release pin. |
| `windows-chrome` | Windows 11, Chrome 152.0.7977.83 | `raw/windows-chrome-20260910T140813Z.json` | Admitted v2 reference; 44/44 probes measured. Intel UHD 630. On the release pin. |
| `RTX 4070 Ti SUPER` | Linux, Chrome 152.0.7977.82 | `raw/linux-nvidia-ada-4070tisuper-20260914T082759Z.json` | Admitted v2 reference; 44/44 measured. ANGLE/Vulkan. Same major as the pin, different patch. |
| `RTX 4080 SUPER` | Linux, Chrome 152.0.7977.82 | `raw/linux-nvidia-ada-4080super-20260914T101137Z.json` | Admitted v2 reference; 44/44 measured. ANGLE/Vulkan. Same major as the pin, different patch. |
| `RTX 3090` | Linux, Chrome 152.0.7977.82 | `raw/linux-nvidia-ampere-3090-20260914T130309Z.json` | Admitted v2 reference; 44/44 measured. ANGLE/Vulkan, no WebGPU adapter on this host. Same major as the pin, different patch. |
| `RTX PRO 4000 Blackwell` | Linux, Chrome 152.0.7977.82 | `raw/linux-nvidia-blackwell-rtxpro4000-20260914T142041Z.json` | Admitted v2 reference; 44/44 measured. ANGLE/Vulkan, no WebGPU adapter on this host. Same major as the pin, different patch. |
| `RTX 3070 Ti` | Windows, Chrome 153.0.8010.37 | `raw/windows-nvidia-ampere-3070ti-20260914T154230Z.json` | Admitted v2 reference; 44/44 measured. ANGLE/D3D11. **Off-major build: not a valid V3 target for the 152 pin.** |
| `RTX A4500` | Windows, Chrome 153.0.8010.37 | `raw/windows-nvidia-ampere-a4500-20260915T191219Z.json` | Admitted v2 reference; 44/44 measured. ANGLE/D3D11. **Off-major build: not a valid V3 target for the 152 pin.** |

The six NVIDIA references were imported from the project's probe host with
`scripts/import-capture.py --manifest resources/fingerprints/import-manifest.json`,
which re-derives admission from the bytes rather than trusting the receiver's
decision, copies the file verbatim so its sha256 still addresses it, and refuses
a software or virtual rasteriser outright. The descriptive filename's timestamp
is checked against `context.taken_at`, so a name cannot drift from the session
it describes.

`docs/METHODOLOGY.md` binds a capture to the build that produced it, so the
admission reason records the build relationship to `build/CHROMIUM_VERSION`:
`release` is exactly the pin, `same-major` is the same Chromium major on a
different patch, and `off-major` is a different major and is admitted only with
`--allow-off-major-build`. All three are valid physical-device evidence for the
GPU capability cluster; only `release` is a valid V3 conformance target for the
pinned build. `corpus/anchors/` records the same caveat per anchor.

All other raw captures, including legacy v1 files such as
`raw/win-intel-uhd630-chrome.json`, are historical and non-admissible. They may
remain for audit history, but must not be cited as T0 evidence or used as current
reference inputs.

Captures that exist on the probe host and were **not** admitted are accounted
for by measurement, not by omission: `corpus/anchors/probe-host-survey.json`
carries their digests and `corpus/anchors/README.md` tabulates why each was
discarded.

## Why there is no vendor data here

This project does not redistribute fingerprint corpora belonging to anyone else,
and does not admit them as ground truth. Beyond the licensing question, such
sets store a digest of each measured render plus a short lossy tail — enough to
tell two devices apart, never enough to reproduce what either drew. Replay needs
reproduction, so that format could not have carried the project regardless.

Three files previously here (`02996-apple-m4-max`, and two iPhone 15 profiles)
were generated by an earlier collector that constructed values shaped like a
fingerprint without measuring them: `rectangles` was a sine wave over two
constant hashes, the audio render sat inside a `catch` that substituted zeros,
and the WebGL block reused the canvas tail — so three different devices produced
one identical fingerprint. They have been removed. They described a schema this
project no longer targets, and contaminated data left in the tree eventually
gets cited no matter what a README says. The lessons they taught are recorded as
the design rules in `capture/README.md`.

## Comparing references

Both admitted files were collected in a secure, headed Chrome session with
`automation_suspected: false`. Probe values are evidence only for the device,
platform and collector context recorded in that file; values from legacy v1
captures are not interchangeable with these references. For a remote receiver,
forward the port and browse to `localhost` rather than to the machine's address.
