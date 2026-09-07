# Architecture

How Apostate is put together. `docs/METHODOLOGY.md` says what correct means;
this says where the parts live and why they sit where they do.

## The shape of the problem

A browser's fingerprint is not one value or one file. It is several hundred
observables produced by different code in different processes, some read from
the operating system, some computed from the CPU, some genuinely rendered by
hardware. Making them describe one device means finding, for each, the point
where the value originates — and there is no general rule for where that is.

Three examples from the map, all verified in source:

- `navigator.hardwareConcurrency` looks like a Blink property. Its Blink
  accessor is a pure forwarder; the value comes from `base::SysInfo`, which also
  sizes thread pools — so patching the accessor gives the right number while the
  process keeps behaving like the host.
- `navigator.platform` looks like a runtime query. Its `uname(2)` path is dead
  code, overridden by a compile-time constant.
- WebGL's renderer string has two independent readers, and the obvious emitter
  feeds the one JavaScript cannot see.

The architecture follows from that: everything is organised around finding and
patching origins, and around proving afterwards that the result is coherent.

## Components

```
capture/     Measures a real device. Also the V3 conformance harness
ledger/      The map: surfaces, emitters, and the coherence graph between them
patches/     The fork itself, one patch per concern, ordered by patches/series
config/      profile.schema.json — derived from the loader, not hand-written
corpus/      Turns captures into a queryable oracle
build/       Every pinned input. See docs/BUILD.md
scripts/     Every operational step
```

## The profile loader is the spine

`base/apostate/profile.h` holds the device a process is presenting. Two
decisions determine everything downstream.

**It lives in `base/`** because `base/` is its first consumer — `SysInfo` reads
it — and `base` cannot depend on `content` or `chrome`. That also makes it
reachable from every process type without a layering violation.

**It arrives on the command line**, because availability is the real constraint.
`content_main_runner_impl.cc` initialises field trials before mojo, so anything
delivered over a mojo interface cannot serve a read that happens earlier — and a
value read before the profile arrives is the host's real value. The profile
parses on first use rather than from a startup hook, which removes the ordering
hazard entirely: there is no call site to place correctly and no process type
that can be forgotten.

Command-line switches are not readable by web content, so carrying it there
discloses nothing to a page.

Every getter returns `nullopt` when the profile is absent or does not carry the
field, and callers fall through to the host value. **An absent field must stay
inherited.** A default invented in the loader is wrong in a way no consumer can
detect, which is worse than the truth.

## Process topology is the recurring trap

Eight surfaces have been found so far that are computed independently in more
than one process, so that patching one leaves another contradicting it:

| surface | processes |
|---|---|
| `deviceMemory` | browser writes the header, renderer serves the JS value |
| DPR | browser on navigation, renderer on subresources |
| Accept-Language | renderer takes the bare list, network service q-weights it |
| font enumeration | browser answers `queryLocalFonts`, renderer matches CSS |
| storage quota | browser only; the renderer computes nothing |
| permissions | browser only |
| speech voices | browser produces, renderer caches |
| CSS system colours | browser builds the map, renderer looks it up |

The reverse also occurs and is easy to miss: `UserAgentMetadata` is computed
once in the browser and pushed to renderers as a mojo copy, so one patch reaches
every Client Hint and every `userAgentData` value in window and worker scope.
Whether a surface needs one patch or two is a fact about Chromium, established
by reading, and it is recorded per emitter in the ledger rather than assumed.

## What cannot be patched

Two results bound the project, and both were measured rather than argued.

**Audio renders identify the host CPU.** The `OfflineAudioContext` graph touches
no audio hardware; its output is determined by the FFT kernel selected by CPUID
at process start and by the host libm. The same graph renders differently on
arm64 and x86-64, and the difference is the same order as deliberately injected
noise. Profiles must be partitioned by CPU ISA class.

**WebGL limit tables identify the backend, not the GPU.** On Metal, ANGLE
hard-codes the entire limit and precision table rather than querying the device,
so an M1 and an M4 Max return identical numbers. Profiles must be partitioned by
ANGLE backend, and a claimed GPU model cannot carry a limit profile it does not
have.

Font metrics, by contrast, turned out to be portable: given the same font file
both platforms produce identical advances, so the requirement is installing the
claimed platform's fonts rather than matching the host OS.

## The verification loop

```
capture a real device  ──►  derive a profile  ──►  launch with it
        ▲                                              │
        └──────────  conform.py diffs  ◄───────────────┘
```

The same collector takes the reference and measures the result, because a
harness that measures differently from the capture tool compares two
incompatible number systems. `conform.py` refuses outright when the two captures
came from different collector builds.

A profile is bound to a browser build. Apostate does not spoof its own version —
the binary really is the Chromium it reports — so a reference is only a valid V3
target for a binary of the same version, and rebasing means re-capturing
references rather than merely re-applying patches.

## Why the coherence graph is separate from the surfaces

Most real failures are a broken relationship rather than a wrong value, and
relationships have no natural owner among the surfaces they bind. The graph is
therefore first-class, with its own rows and its own severity, and it has already
earned that: applying the Client Hints patch alone produced a browser reporting
macOS through `userAgentData` and Linux through `navigator.platform`. The edge
fired on our own build, which is the point — a partial identity is more
detectable than none.
