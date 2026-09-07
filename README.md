# Apostate

An anti-detect Chromium fork. A real browser binary whose fingerprint is
modified in C++ at the source level, not injected from JavaScript.

Free and open source. No paid tier, no licence key, no gated builds.

## Status

Early, and honest about it. The build is reproducible, the surface map is
complete across twelve subsystems, and thirteen patches are in the tree — but
conformance against a real device currently sits at 8 of 30 probes. Fonts,
audio, the WebGL parameter tables and much else still report the host.

Nothing here is usable yet. Watch releases rather than cloning expectantly.

What does work end to end: a Linux host can present a coherent macOS identity
across `navigator.platform`, `userAgentData`, every Client Hint and the User-Agent
string, with CPU count, device memory, GPU strings, screen geometry, timezone and
colour scheme following the same profile. 30 mapped surfaces are implemented and
verified by capture.

## What makes it different

Most anti-detect browsers **synthesise** a fingerprint: a seed feeds a generator
that invents plausible values and sprinkles noise over canvas, WebGL and audio
readback. That noise is itself detectable. Real hardware is deterministic — two
identical canvas renders on real silicon are bit-identical — so a surface that
wobbles between reads is a surface that answers "not real hardware".

Apostate **replays** instead. A profile is a real device capture, and the
browser reproduces what that device actually emitted. There is no per-call
randomness anywhere in the binary. Variation happens by choosing a different
device, which is what variation means on the open internet.

That approach only works with real captures behind it, so the capture pipeline
comes before the patches. Every profile Apostate ships is measured from a real
device with its owner's consent, using the tool in `capture/` — which is also
the harness that verifies the browser against it. Coverage we do not have is
tracked as a gap rather than filled with invented values.

We do not redistribute fingerprint datasets belonging to anyone else. Breadth
comes from devices we control and from people who choose to contribute their
own profile.

## Design rules

Three axioms decide every patch, and a change that breaks one does not land:

1. **Provenance** — patch the emitter, never the accessor. No JavaScript
   injection, no CDP override, no wrapped objects. Nothing to find, because
   nothing was wrapped.
2. **Coherence** — correct means "equal to what device D emits", not "harder to
   read". Every patch cites the capture it reproduces.
3. **Determinism** — no per-call randomness. Ever.

The reasoning behind them is in [docs/METHODOLOGY.md](docs/METHODOLOGY.md).

## Build

Fully reproducible: same pins in, byte-identical binary out, no manual steps.
Every input — Chromium revision, depot_tools revision, GN args, container image
digest, patch series — is pinned in `build/`. See
[docs/BUILD.md](docs/BUILD.md).

## Licence

See [LICENSE](LICENSE).
