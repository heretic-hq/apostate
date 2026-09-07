# Subsystem 1 — Profile Loader & Flag Plumbing

How a profile reaches every process that needs it, early enough to matter, and
without becoming observable itself.

Built first because every other subsystem inherits its decisions, and because
its failure mode is invisible in the surface it fixes.

Entry points marked **verified** were confirmed against the pinned checkout of
152.0.7977.82, not codesearch, which indexes `main`.

## The constraint that decides the design

Not secrecy — **timing and reach**.

A value read before the profile arrives is the real value. A process that never
receives the profile reports the real value. Both are page-observable, and
neither is visible in the surface the patch was written for.

`content/app/content_main_runner_impl.cc:621,624` — **verified**:

```
InitializeFieldTrialAndFeatureList();   // line 621
if (delegate->ShouldInitializeMojo(...))
  InitializeMojoCore();                 // line 624
```

**Mojo is not available early enough.** Anything delivered over a mojo interface
cannot serve a read that happens before mojo core exists. The profile has to be
present at process start, which leaves the command line — exactly what field
trials use, and for the same reason.

## Reach: this is not a renderer-only problem

`deviceMemory` proves it. `ApproximatedDeviceMemory` is a static computed
independently in each process, and it has three consumers across two:

| Consumer | Process | Produces |
|---|---|---|
| `content/browser/client_hints/client_hints.cc:243` | **browser** | the `Device-Memory` HTTP header |
| `third_party/blink/renderer/core/loader/frame_fetch_context.cc:687,696` | renderer | client hints on subresource requests |
| `third_party/blink/renderer/core/frame/navigator_device_memory.cc:15` | renderer | `navigator.deviceMemory` |

All **verified**. Patch the renderer only and the HTTP header a server receives
disagrees with the value JavaScript reports — a contradiction visible to any
detector that compares them, and one that no amount of renderer-side work fixes.

So the profile must reach at minimum: **browser** (client hints, window
geometry), **renderer** (most surfaces), **GPU** (GPUInfo strings), and
**network** (proxy credentials, per `docs/subsystems/network-proxy.md`).

## Mechanism

Follow the field trial pattern, which already solves this problem in-tree and
already crosses the sandbox correctly:
`content/browser/child_process_launcher_helper.cc:120` — **verified** — passes
`switches::kFieldTrialHandle` with a descriptor, delivering a shared memory
region whose handle travels on the command line.

- **A scalar switch** carries the profile id and a handle to the region.
- **Shared memory** carries the profile body. Profiles include font lists, WebGL
  parameter tables and render payloads; those do not belong on a command line.
- **Parsed immediately after field trials, before mojo**, so no page-observable
  read can precede it.
- The browser process loads the profile from disk directly and is the single
  source; children receive the region rather than re-reading the file, so no
  process can disagree with another.

Child command lines are assembled at
`content/browser/renderer_host/render_process_host_impl.cc:3751` via
`AppendExtraCommandLineSwitches` — **verified**.

## What is actually observable

Correcting an assumption made earlier in this project: **command-line switches
are not readable by web content.** Switch presence is not a page-visible signal,
and the design does not need to hide it. What is observable is narrower and more
dangerous:

1. **Late arrival.** Any read before the profile is parsed returns the real
   value. This is why mojo was rejected.
2. **Partial reach.** A process without the profile contradicts one with it —
   the `Device-Memory` case above.
3. **Behavioural side effects.** A new `base::Feature`, a changed default, or an
   altered startup sequence can shift page-observable behaviour even when no
   value changes. The loader introduces no features and changes no defaults.
4. **Impossible claims.** See below.

## Resolving the parallelism escalate

`compute.measured-parallelism` was left open in the ledger because reading could
not settle it. It settles here, and the answer generalises.

Patching `NavigatorConcurrentHardware::hardwareConcurrency` alone gives the right
number while the process keeps thread pools sized for the host's real core count,
so a worker scaling curve contradicts the claim. Patching
`base::SysInfo::NumberOfProcessors` makes the claim true, because thread pools
size from it.

**Decision: patch the source, and constrain the claim.** The direction is not
symmetric:

- Claiming **fewer** cores than the host has is safe. Pools are smaller, the
  scaling curve plateaus where claimed, and the claim is true.
- Claiming **more** is not achievable. The curve plateaus at the host's real
  count regardless of what the number says, and oversubscribed pools degrade
  behaviour besides.

The same asymmetry applies to memory: claiming more RAM than physically present
is contradicted by real allocation behaviour.

### How strong is this constraint, really

Weaker than the asymmetry above suggests, and the reason matters for the product.

`base::SysInfo::NumberOfProcessors` uses `sysconf` for the count of max
available logical processors and has **no cgroup or CFS-quota handling** —
**verified**, there is none anywhere in `base/system/`. So stock Chrome in a
container limited to two CPUs on a thirty-two core host reports thirty-two while
achieving two-core parallelism. That contradiction exists in unmodified Chrome,
on real users' machines, with nothing spoofed.

The same holds for a VM with CPU limits, a loaded machine, a thermally throttled
laptop, or a browser with busy background tabs. A detector ringing on "reported
cores disagree with measured scaling" fires on all of them, so it cannot be used
for a binary ruling — it is weak evidence, and weak evidence never accumulates
into certainty.

Memory is weaker again. Contradicting a 32GB claim on an 8GB host requires
actually allocating past the host's capacity, which kills the tab and breaks
real users. It is not a probe anything ships.

So the rule is a **preference with a severity gradient**, not a portability wall:

- Claiming at or below host capacity is free, and is the default.
- Claiming above it carries weak-evidence risk only, and is permitted.
- The loader **clamps and warns**; it does not refuse. Refusing would trade a
  weak, rarely-probed signal for a hard usability failure, which is the worse
  deal.

### What this actually costs

Two fields out of roughly fifty are host-coupled. The device's *identity* —
GPU strings, fonts, screen geometry, timezone, audio, codecs, voices — is fully
portable and unaffected.

And the pressure runs in a useful direction. A cheap VPS should be presenting a
mid-range device rather than a top-tier workstation: a median laptop blends into
ordinary traffic, while an M4 Max arriving from a datacenter range is
conspicuous on its own. Preferring profiles that fit the host mostly pushes
toward more common hardware, which is where a profile wants to be regardless.

Real product lines also ship multiple SKUs at a given identity, so there is
usually a legal lower configuration within the same device family rather than a
jump to a different device.

## Deliverables

1. `ConfiguredProfile` — the in-memory representation, loaded once in the
   browser process.
2. The switch and shared memory region, parsed after field trials and before
   mojo, in every process type listed above.
3. A host-capability check that refuses under-capacity profiles at launch.
4. The precedence rule for explicit overrides (`--fingerprint-*` style flags)
   against profile values, so a single field can be changed without the result
   silently becoming incoherent.

Item 4 is where the coherence graph earns its place: an override that breaks an
edge should fail loudly at launch rather than ship a contradiction.
