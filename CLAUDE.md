# Apostate — working agreement

Apostate is an anti-detect Chromium fork: a real browser binary whose
fingerprint is modified in C++ at the source level. Free, open source, no paid
tier.

Read `docs/METHODOLOGY.md` before doing anything else. It defines what
"correct" means here, and this file will not repeat it.

## The three axioms

1. **Provenance** — patch the emitter, never the accessor. No JS injection, no
   CDP override, no wrappers or redefined descriptors. If a value is wrong,
   find the C++ that produced it.
2. **Coherence** — correct means "equal to reference device D", not "less
   detectable". Cite the corpus row.
3. **Determinism** — no per-call randomness, ever. Variation lives at the
   profile boundary. Where a value cannot be derived, replay it from the
   capture.

If a change cannot satisfy all three, it does not land. There is no "good
enough for now" tier; a partially coherent browser is more detectable than an
unmodified one, because incoherence is itself the signal.

## Hard rules

- **Never cite T2 evidence to close a ledger row.** Competitor repos, blogs and
  vendor writeups open questions; Chromium source, specs, and measurements
  answer them. Tiers are defined in `docs/METHODOLOGY.md` §3.
- **Never treat `resources/fingerprints/*.json` as ground truth.** They are
  templates. See the `PROVENANCE.md` beside them.
- **Never commit or ship a third-party fingerprint corpus**, and never cite one
  as T0. Vendor datasets may inform priors at T2 and stay out of the tree. T0
  means a capture we took ourselves, with consent, using `capture/`.
- **Never introduce a new observable.** A patch that fixes one surface while
  adding a command-line switch the page can see, a novel mojo interface, an
  unusual process name, or a timing change is a net loss. Cross-process
  plumbing goes through the profile loader; nothing else invents its own path.
- **Never hand-edit anything under `out/` or `src/`.** Those are generated. All
  source changes are patches in `patches/`, listed in `patches/series`.
- **Never commit to `.internal/`.** It is gitignored. This repository's history
  is public and permanent; internal planning, positioning, and strategy live
  there and nowhere else. Do not reference their contents in public docs,
  commit messages, or code comments.

## Repository layout

```
build/        Pinned versions and GN args. The build contract — see docs/BUILD.md
patches/      The fork. One patch per concern, ordered by patches/series
config/       profile.schema.json — DERIVED from the ledger, not hand-written
capture/      The capture pipeline. Also the V3 conformance harness
corpus/       Oracle builder. Turns our captures into a queryable DB
ledger/       Surface ledger, emitter index, coherence graph, and their schemas
resources/    surfaces.json (input map) and fingerprint templates
scripts/      Every operational step. Nothing is done by hand
docs/         METHODOLOGY.md is authoritative; ARCHITECTURE.md, BUILD.md follow
```

## Build discipline

The build pipeline is reproducible and fully scripted. This is not a
preference — an environment configured outside the repo is an environment that
drifts, and drift in a fingerprinting project produces silent, unattributable
failures.

- Every input is pinned in `build/`: Chromium revision, depot_tools revision,
  toolchain, GN args, container base image digest.
- Every step is an idempotent script in `scripts/`. If a step needs a human to
  run a command by hand, that is a bug in the script.
- Two builds from the same pins produce byte-identical output.
  `scripts/verify-reproducible.sh` is the check, and it runs in CI.
- Never `gclient sync` without the pins. Never let depot_tools self-update
  (`DEPOT_TOOLS_UPDATE=0`).

Builds are checkpoints, not a debugging loop. Gate work at V0/V1
(`scripts/checkfile.sh`) and batch it; a full build should be expected to pass,
not tried to see what happens.

## Verification

Nothing is "done" until it passes the tier its ledger row names — V0 through
V4, defined in `docs/METHODOLOGY.md` §6. V3 (corpus conformance) is the
scoreboard: launch with profile P, collect, diff against P's corpus row.

Report results as they are. A patch that compiles is not a patch that works,
and a V3 diff with three red fields is reported as three red fields.

## Working mode

Run the agreed plan to completion without checking in between steps, and
parallelize wherever the work allows. Stop for the user only when a finding
forces a **material decision** — something that changes scope, architecture, or
whether the product is viable. Progress updates, permission to continue, and
confirmation of an obvious next step are not material decisions.

When genuinely blocked, ask with a recommendation rather than a survey.

## Process-matching in scripts

Never `pkill -f <pattern>` or `pgrep -f <pattern>` where the pattern also
appears in the command line doing the matching — it matches itself. This has
cost time four times in this project: it killed an ssh session mid-checkout, it
stalled a build watcher that never cleared, and once it produced a false process
count that nearly triggered a fix to code that already worked.

Use a self-excluding pattern (`receiv[e].py`), match on a PID captured at start,
or make the process exit on its own. `capture/server/receive.py --once` exists
for exactly that reason.

## The shared checkout

`.workspace/src` is shared: mapping work cites line numbers from it while patch
work applies patches to it. Those conflict, and it has already cost real
rework — a shard was mapping a file while a patch was applied underneath it, so
its citations pointed at lines that no longer existed.

Rule: **the checkout stays pristine while mapping is in flight.** Apply patches
only to compile-check, then reset. Ledger citations always name the pristine
tree at `CHROMIUM_VERSION`; the patch that changes a line is linked through the
row's `patch_id`, not by re-citing the patched line.

## Parallel work

Delegated units return JSON validating against a schema in `ledger/schema/`,
written to `ledger/inbox/`. Workers never write the ledger directly; a single
arbiter merges. Shard by category. When uncertain, mark `escalate` — never
silently drop a surface.

## Commits

Small, focused, and frequent. Message says what changed and why, in plain
language. No attribution or co-author trailers.
