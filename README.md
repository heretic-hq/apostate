# Apostate

An anti-detect Chromium fork. A real browser binary whose fingerprint is
modified in C++ at the source level, not injected from JavaScript.

Free and open source. No paid tier, no licence key, no gated builds.

## Status

Release candidate handoff is in progress. The reproducible build baseline is
pinned to Chromium 152.0.7977.83 and the current patch series contains 70
patches. The initial offered catalogue is 14 normalized compatibility-backed
profile families derived from 16 authorized compatibility captures. These are
selectable product profiles while equivalent physical captures are unavailable;
they are not claims that the host owns the named physical device.

The checked-in capture inventory has mixed review status. A file is T0 only
after its own provenance, capture conditions and contents have been reviewed;
captures with uncertain or ambiguous provenance are excluded or quarantined.
The public catalogue ships normalized Apostate-owned values, not raw external
captures. Compatibility profiles remain labelled as compatibility evidence and
may be replaced or refined by verified physical captures later.

This is not a finished release. Full-build/native launch, package, V2, physical
V3/V4 and compatibility V3-C/V4-C gates remain blocked where their platform,
corpus or other prerequisites are absent. No native or package release success
is claimed.

What is in the tree is the source-level implementation, profile schema and
catalogue/composition work needed for those gates. A selectable compatibility
profile may have provisional or limited surfaces until native compatibility
validation passes; missing coverage remains a reported gap rather than an
invented value.

## Profile selection

Apostate does not choose a random profile when no profile arguments are set.
With no profile, fingerprint, or platform, the launcher uses `host-inherited`
mode. It sends no fingerprint payload to Chromium, so the browser keeps native
host values.

Set a platform without a fingerprint to select that platform's deterministic
default family. Set a fingerprint seed to select a family deterministically.
The same seed, platform, catalogue version, and Chromium build select the same
family on every run. The seed is not a request for per-call noise.

Python:

```python
from apostate import launch_persistent_context

# Host-inherited mode. No catalogue family is selected.
browser = launch_persistent_context(user_data_dir="./profile")

# Deterministic selection from the macOS families.
browser = launch_persistent_context(
    user_data_dir="./profile",
    fingerprint="customer-42",
    fingerprint_platform="macos",
)
```

Node.js:

```js
import { resolveProfile } from "@heretic-hq/apostate";

const host = resolveProfile();
console.log(host.source); // "host-inherited"

const selected = resolveProfile({
  fingerprint: "customer-42",
  fingerprintPlatform: "macos",
});
console.log(selected.source); // "fingerprint"
console.log(selected.profileId); // stable for the same inputs
```

Use an explicit profile ID when the family must not change with the seed. The
catalogue reports compatibility-backed families as compatibility evidence. It
does not turn them into physical-device claims.

## What makes it different

Most anti-detect browsers **synthesise** a fingerprint: a seed feeds a generator
that invents plausible values and sprinkles noise over canvas, WebGL and audio
readback. That noise is itself detectable. Real hardware is deterministic — two
identical canvas renders on real silicon are bit-identical — so a surface that
wobbles between reads is a surface that answers "not real hardware".

Apostate **replays** reviewed evidence and composes it only where the
configuration is coherent. A profile may be a reviewed physical capture, a
compatibility-backed catalogue entry or a coherent composition of reviewed
blocks. There is no per-call randomness anywhere in the binary. Variation
happens at the profile boundary, never inside a session.

That approach only works with evidence behind it. Only per-file verified
captures can close T0. Captures with uncertain or ambiguous provenance are
excluded or quarantined, and catalogue values remain labelled by evidence
class. Coverage we do not have is tracked as a gap rather than filled with
invented values.

We do not redistribute raw third-party fingerprint datasets. Breadth comes
from reviewed captures and compatibility-backed catalogue values that satisfy
the schema and coherence rules.

## Design rules

Three axioms decide every patch, and a change that breaks one does not land:

1. **Provenance** — patch the emitter, never the accessor. No JavaScript
   injection, no CDP override, no wrapped objects. Nothing to find, because
   nothing was wrapped.
2. **Coherence** — correct means "equal to the declared target", whether that
   target is a physical reference device or a compatibility target. Every patch
   cites the physical corpus row or compatibility acceptance record it
   reproduces; compatibility evidence is never physical T0 evidence.
3. **Determinism** — no per-call randomness. Ever.

The reasoning behind them is in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Build

Fully reproducible: same pins in, byte-identical binary out, no manual steps.
Every input — Chromium revision, depot_tools revision, GN args, container image
digest, patch series — is pinned in `build/`. See
[docs/BUILD.md](docs/BUILD.md).

## Licence

See [LICENSE](LICENSE).
