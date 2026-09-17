# Fingerprint composition

How a launch gets a fingerprint. `docs/PROFILE_SPEC.md` is the field contract;
this document is the model that produces the fields, and it defers to
`docs/METHODOLOGY.md` on what counts as evidence.

## 1. Why the catalogue model was replaced

The first model shipped a fixed catalogue: fourteen families, each a complete
device contract, selected by seed. Three properties made it unusable as a
product.

**It could not be unique.** Fourteen families is fourteen fingerprints. Two
users who drew the same family were byte-identical to each other, which is a
stronger correlation signal than either user's original host.

**It required a host match.** A profile was only offered when the host could
serve every surface in it, so a macOS user had macOS families and nothing else.
The product requirement is the opposite: a user asks for Windows and gets
Windows, with the cost of that choice stated rather than the choice refused.

**It was not backed by measurement.** All fourteen families carry
`compatibility-capture` provenance derived from one external runtime's output.
The measurement that closes the question: across the fourteen, the WebGL1
capability digest, the WebGL2 capability digest *and* the canvas pixel digest
are each a single value — one digest shared by supposedly distinct Intel HD
Graphics, GeForce GTX 960, Radeon RX 9060 XT and Apple M4 machines. They are
identity-string swaps taken on one Mac. The host's real capability tables were
never touched.

That last result is also the most useful thing the old corpus produced, because
it is exactly the failure mode a detector looks for, and it is the reason
section 4 exists.

## 2. Three layers

Every observable belongs to exactly one layer. The layer decides who owns the
value and what may vary it.

| Layer | Owner | Varies | Evidence |
| --- | --- | --- | --- |
| **Invariant** | the binary | never | the build itself |
| **Anchor** | a measured device cluster | per anchor, atomically | T0 capture |
| **Dispersion** | the compositor | per profile, by seed | enumerated real-world options |

**Invariants** are what the binary is: Chromium version, brand list, API
surface, `Function.prototype.toString` output, which codecs are compiled in,
whether a CDM is registered. A profile never varies these. When one is wrong it
is a build or patch defect, not a profile defect — see §8.

**Anchors** are the clusters that cannot be recombined without contradiction.
The GPU cluster is the canonical one: WebGL1 and WebGL2 extension lists,
parameter tables, shader precision, context attributes, sample counts, the
pixel render digest, and the WebGPU feature and limit sets. These values are
produced by one piece of silicon running one driver through one backend. Any
mixture of two anchors is a device that does not exist.

An anchor is atomic: selecting it takes the whole cluster. Anchors come from
`corpus/anchors/`, each one derived from admitted T0 captures, each carrying
the digests that were compared to establish its membership.

**Dispersion** is everything a real machine varies because of what its owner
installed, plugged in, or configured: which fonts are present, how many
microphones, which voice packs, panel size, taskbar position, core count. This
is where per-profile uniqueness comes from, and it is large enough to carry the
whole product: the dispersion space is combinatorially big while every point in
it remains a machine someone could own.

## 3. The two rules that keep dispersion honest

**Selection, never synthesis.** A dispersion draw chooses among options that
were observed on real systems. It never produces a novel value. Five randomly
chosen font families is synthesis and is a tell, because installed fonts arrive
in bundles: a machine with Myriad Pro has the rest of Creative Cloud, a machine
with Cascadia Code has a developer's toolchain. Dispersion selects *bundles*.

**Servability: capacity is only ever reduced.** A profile may claim fewer cores
than the host has, never more. Same for memory, GPU limits, codec support, font
families, speech voices, and display area relative to window bounds. The reason
is falsifiability, not modesty: a page can measure parallel throughput,
allocate until it fails, compile a shader at the advertised limit, or ask a
voice to speak. A claim below host capability survives every one of those
probes; a claim above it fails the first. `scripts/check-servable.py` is the
gate.

This is the specific respect in which clamping beats spoofing. Reporting the
host's true 14 cores and 32GB identifies one model of laptop. Reporting a
fabricated 20 cores contradicts any timing probe. Reporting 8 cores — a real
bucket, servable by a 14-core host — is both common and unfalsifiable.

## 4. Anchors, identity strings, and the limit of string swapping

Within an anchor, the identity strings (`unmaskedVendor`,
`unmaskedRenderer`, WebGPU `vendor`/`architecture`) are rotatable, because the
anchor's members measurably agree on everything else. Across anchors they are
not.

Measured, from the project's own T0 captures:

The four anchors in `corpus/anchors/`, all T0, digests as emitted by
`scripts/build-anchors.py` (first 8 hex of the full SHA-256 in the anchor
file):

| Anchor | Backend | Members | WebGL1 caps | WebGL2 caps | WebGL pixels |
| --- | --- | --- | --- | --- | --- |
| `linux-vulkan-nvidia-adf287b8f0ee` | ANGLE/Vulkan | RTX 4070 Ti SUPER, RTX 4080 SUPER, RTX 3090, RTX PRO 4000 Blackwell | `2327f6ae` | `c7aa8b52` | `7e8e3b73` |
| `windows-d3d11-nvidia-0947761dfbe9` | ANGLE/D3D11 | RTX 3070 Ti, RTX A4500 | `ff875414` | `a34893d0` | `a895ab2a` |
| `macos-metal-apple-850a91233555` | ANGLE/Metal | M4 Max | `53ec118e` | `102f6613` | `918f09f6` |
| `windows-d3d11-intel-79dfeb5b4f99` | ANGLE/D3D11 | UHD 630 | `be8acf33` | `e695c2da` | `918f09f6` |

Four results follow, and they do not all point the same way.

**Within a backend, silicon generation does not matter.** Ada, Ampere and
Blackwell produce byte-identical WebGL1 and WebGL2 capability tables *and* an
identical WebGL render digest on Linux/Vulkan. The two Ampere-class cards agree
with each other on Windows/D3D11. So one anchor legitimately covers a range of
cards and the renderer string is cosmetic relative to it. This is measured.

**Across backends, the capability tables transfer nothing.** The same NVIDIA
silicon produces a different capability digest through D3D11 than through
Vulkan, and Apple Metal differs from both. Presenting a Windows/D3D11 renderer
string on top of an Apple Metal capability cluster is precisely the Gologin
failure from §1.

**The WebGL render digest is not a backend discriminator.** Apple/Metal and
Intel/D3D11 produce the same `918f09f6` pixels. A matching render digest is
therefore necessary but not sufficient evidence of interchangeability; the
capability tables are what discriminate, and the anchor is keyed on them.

**Two surfaces are inside an anchor's members but outside the anchor.** The
WebGPU cluster differs between members of the Linux/Vulkan anchor, because it
belongs to each host's driver stack rather than to the silicon class, so WebGPU
identity is *not* rotatable within an anchor. Canvas 2D differs too, and that
one is not a GPU measurement at all — it is fonts and raster, which is why it
is excluded from the anchor key and recorded separately so the exclusion stays
checkable.

Therefore: **`--fingerprint-platform` does not move the GPU cluster.** It
changes OS identity, client hints, fonts, voices, locale, screen geometry and
hardware buckets. The GPU cluster is selected from anchors the host can
actually serve. On a macOS host a Windows persona keeps an Apple GPU cluster,
and that mismatch is reported as a limitation rather than hidden.

The consequence for deployment is worth stating plainly: a coherent Windows
fingerprint wants a Windows or Linux host with the corresponding silicon. That
is a property of graphics drivers, not of this codebase, and no competitor
avoids it — they only decline to report it.

### Build binding limits what these anchors can gate

A capture is bound to the build it was taken on, so an anchor is only a valid
V3 conformance target for a binary of the same version. Two of the four are
not, and the anchor files record it:

- `windows-d3d11-nvidia-0947761dfbe9` was measured on Chromium 153.0.8010.37,
  an off-major build. Capability tables are build-bound, so this anchor is
  evidence about Ampere-class D3D11 silicon but cannot gate a 152 binary.
- `linux-vulkan-nvidia-adf287b8f0ee` was measured on 152.0.7977.82 against a
  152.0.7977.83 pin: same major, different patch, so version-bearing fields
  differ and the conformance runner reports them separately.

Only `macos-metal-apple-850a91233555` and `windows-d3d11-intel-79dfeb5b4f99`
are on the pinned build. Closing the other two means one re-capture round on
the pinned binary, not new analysis.

## 5. Seed, and what a seed is worth

A seed is a deterministic selector over the dispersion space. It is not a
randomizer and it never reaches a value at read time.

| Launch | Seed source | Result |
| --- | --- | --- |
| no arguments | OS entropy, once, then persisted | unique per browser profile |
| `--fingerprint=<seed>` | the argument | reproducible anywhere |
| `--fingerprint=host` | none | host inheritance, today's default |

**Seed lifetime is the user-data-dir, not the launch.** A new fingerprint on
every launch of the same browser profile is itself a signal: returning-visitor
checks, long-lived cookies and storage all expect the machine to stay the same
machine. So the seed is written to `<user-data-dir>/apostate/seed` on first use
and reused afterwards. An ephemeral user-data-dir therefore yields a new
identity, and a persistent one yields a stable identity, which is what the
directory already means for cookies.

This satisfies A3 without qualification: the profile is fully materialized
before the first renderer starts, and nothing inside the session varies.

### Derivation

```text
root = SHA-256( "apostate/fp/v1" 0x00
                profile_schema_version 0x00
                catalogue_version      0x00
                chromium_version       0x00
                fingerprint_platform   0x00
                seed )

draw(label, i) = SHA-256( root 0x00 label 0x00 uint32_be(i) )
```

Every axis draws from a substream keyed by its own label. This is the property
that lets the catalogue grow: adding an axis, or changing the option list of
one axis, cannot shift any other axis's choice, so an old seed keeps producing
the same profile for everything that did not change.

Weighted selection from an option list uses the high 64 bits of `draw` as
`u64`, and picks index `(u64 * total_weight) >> 64` by cumulative weight. No
modulo, no rejection loop, no floating point.

### Resolution order

Axes resolve in dependency order, and a dependent axis draws from an option set
that is *a function of its parents' resolved values*. There is no rejection
sampling and no re-draw, so every profile is coherent by construction rather
than by validation.

```text
platform persona -> os release -> anchor (host-servable) -> identity string
  -> cpu bucket -> memory bucket -> panel -> furniture -> font packs
  -> media topology -> voice table -> locale/timezone
```

A coherence-graph violation after resolution is a defect in the option tables,
never something the compositor works around at runtime.

## 6. The dispersion axes

Each axis has an option table under `resources/profiles/dispersion/`, and each
option carries its evidence class and a weight. Weights encode real-world
prevalence; they are not uniform.

| Axis | Option unit | Conditioned on | Servability limit |
| --- | --- | --- | --- |
| `os_release` | OS build + client-hint `platformVersion` | platform | — |
| `anchor` | GPU capability cluster | platform, host backend | host must serve the cluster |
| `gpu_identity` | vendor + renderer string pair | anchor | must be a member of the anchor |
| `cpu` | core-count bucket | platform, device class | `<=` host logical cores |
| `memory` | `deviceMemory` bucket | device class | `<=` host physical memory |
| `panel` | width x height x DPR | platform, device class | `>=` window bounds |
| `furniture` | taskbar/dock edge, size, autohide | platform, os release | — |
| `font_packs` | an installed-software bundle | platform, os release | files must be present or provisioned |
| `media_topology` | input/output/camera counts + labels + group pairing | platform | — |
| `voices` | voice table for an OS release and language set | platform, os release, languages | provider must be able to speak |
| `locale` | language list + timezone | launch precedence, GeoIP | — |

Notes that matter per axis:

**Font packs.** The mandatory core set for an OS release is not optional — a
machine claiming macOS without Menlo, Monaco, Zapfino, PingFang SC and
Helvetica Neue is not a Mac, and that absence is the defensible detection. On
top of the core set, packs model what software installs: Office, Creative
Cloud, the CJK language packs, LibreOffice's Liberation/DejaVu, a developer's
Cascadia. Enumeration is subtractive and always servable; *addition* requires
the font files, so a pack is only offered when its files are present on the
host or provisioned into the profile. Anything else is reported as a
limitation, per `docs/METHODOLOGY.md` on font provisioning.

**Media topology.** `deviceId` and `groupId` are already per-origin HMACs in
Chromium, so the fingerprint content is the count, the kind mix, the group
pairing, and the labels once permission is granted. Labels are drawn from
per-platform vendor tables: a Realtek codec name is a Windows artifact and must
never appear on macOS.

**Voices.** The table is a function of OS release and installed language packs.
Network voices (`localService: false`) are a build-level capability, not a
profile value — see §8.

**Panel and furniture.** `availLeft`/`availTop`/`availWidth`/`availHeight`
follow from the panel plus the furniture model; `outerWidth`/`outerHeight`
follow from the OS's window chrome for that release. A forced device scale
factor makes a claimed DPR real at rasterization time rather than only at the
accessor.

## 7. Where composition runs

The compositor lives in the browser process, in C++, and is the only
implementation.

The bare binary must produce a fingerprint with no arguments, which puts the
compositor inside the binary by necessity. Reimplementing it in the Python and
Node packages would create three sources of truth for one deterministic
function, and the drift between them would be silent. So the packages stop
composing: they call the binary to materialize a profile, and keep doing what
only they can do — CLI ergonomics, schema validation, launch orchestration,
GeoIP.

Determinism across languages is then a property of the binary, and is pinned by
golden seed vectors rather than by cross-language byte comparison.

## 8. What belongs to the build, not the profile

Three surfaces in the current gap list are invariants, and no profile field can
fix them:

- **Brand list.** `components/embedder_support/user_agent_utils.cc` adds the
  product brand only under `#if !BUILDFLAG(CHROMIUM_BRANDING)`, so a Chromium
  build emits two brands where Chrome emits three. The GREASE brand and version
  are already correct, because both derive from the major version.
- **Codecs.** `proprietary_codecs` and `ffmpeg_branding = "Chrome"` live in
  `build/args/common.gni`. A build configured without that file has no H.264
  at all. HEVC follows the platform decoder on macOS and Windows and patch
  `0061` on Linux x64.
- **Network speech voices.** The `localService: false` voices are served by
  Chromium's network speech synthesis component against a Google endpoint, which
  requires API keys at build time. Without keys the voices cannot be listed,
  because a listed voice that cannot speak violates A2 and patch `0043`.

## 9. Verification

A composition change is done when all five pass:

1. **Determinism** — the same tuple yields byte-identical profiles, across
   processes and hosts. Golden seed vectors.
2. **Coherence** — every edge in `ledger/coherence.jsonl` holds for N seeds.
3. **Servability** — every claim is `<=` host capability, checked by
   `scripts/check-servable.py` for N seeds.
4. **Dispersion** — for N seeds, no value falls outside its option table, and
   the realized distribution matches the table weights.
5. **Conformance** — V3-C per anchor: launch with the profile, collect, diff
   against the anchor's captures.

## 10. The launch contract

One switch family, all resolved before the first renderer starts.

| Switch | Meaning |
| --- | --- |
| `--fingerprint=<seed>` | Deterministic seed. An integer or an arbitrary string. |
| `--fingerprint=host` | Inherit the host. No composition. |
| `--fingerprint-platform=<windows\|macos\|linux>` | Platform persona. Defaults to the host's. |
| `--fingerprint-anchor=<id>` | Pin the GPU anchor instead of drawing one. |
| `--fingerprint-explain` | Write the composition report to stdout and exit. |
| `--apostate-profile=<base64>` | An already-composed profile. Existing switch, unchanged. |

Precedence, strongest first: `--apostate-profile` > `--fingerprint` > the seed
persisted in the user-data-dir > a seed drawn from OS entropy. `host` disables
every layer below it.

`--fingerprint-explain` prints, per surface, the resolved value, the layer that
owns it (invariant, anchor, dispersion, host-inherited), the evidence class, and
any limitation. It is the honest answer to "what does this profile actually
claim, and what can this host actually serve" — and on a cross-platform launch
it is where the GPU-cluster mismatch from §4 is reported rather than hidden.
It writes to stdout, never to a page-visible API.

### Table transport

The dispersion tables and anchors are compiled into the binary by a GN action
that generates C++ from `resources/profiles/`. They are not read from disk at
runtime: a data directory beside the executable would be one more thing to
lose, to mismatch against the binary, and to diverge per install. The compiled
digest of the tables is part of the composition identity in §5, so a binary
and a profile cannot silently disagree about what the catalogue said.
