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

| Class | Real captures | Tier | Notes |
|-------|---------------|------|-------|
| Android mobile | **2,995** | T0 | Chrome 132–146, 87 GPU families. Complete value coverage. |
| macOS desktop | 0 | — | Capturable: real hardware on hand |
| Windows desktop | 0 | — | Requires real hardware; VM captures carry their own tells |
| iOS | 0 | — | |

The three files in `resources/fingerprints/` are **schema templates, not
captures** — see `resources/fingerprints/PROVENANCE.md`. They must never be
cited as T0.

Where a class has no ground truth, that is recorded as a gap and the
corresponding ledger rows stay open. A profile is never shipped for a device
class we cannot verify against.

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
