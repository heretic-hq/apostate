# Known limitations

What this browser does not do, and what a page can still tell about the machine
it is running on. Read this before deciding it is a good fit.

Nothing here is hidden behind a flag or fixed by turning something on. Where a
limitation applies to your launch, `--fingerprint-explain` names it.

## What has not been exercised yet

Read this first. Several behaviours described on this page were written for this
release and have not been compiled or run. They apply to the source tree and
nothing more, so treat them as intent rather than as behaviour you can rely on
until you have seen them work.

- WebRTC UDP relaying through a SOCKS5 proxy, and the
  `--fingerprint-webrtc-udp` switch
- The eight per-field `--fingerprint-*` override switches
- Font enumeration filtering, meaning the profile's font list actually reaching
  a page
- On the Linux build only: Windows character fallback following Windows' own
  script table, and the coherence guard that stops text measurement using a
  family the font list says is absent
- Network information and battery state coming from the profile
- Capture devices the profile claims delivering frames and audio
- The whole of the GPU identity change, which is three patches: the claimed
  platform selecting the capability cluster on every host and the host-dependent
  default persona (`0102`), the claimed WebGL limits being served on a backend
  that enforces nothing (`0103`), and the five claimed extensions being served
  from Blink's own implementation classes (`0104`)
- The Web Share cancellation message
- V8's heap ceiling following the profile's memory figure
- The remote-debugging endpoint refusing what a page sends it

Everything else on this page describes a binary that has been built and run.

Two things to do before relying on any of the above. Run
`--fingerprint-explain` and confirm the surface you care about resolved the way
you expect. Then read that surface the way a page would, from a page, and check
the value. Where a behaviour above is a safety property rather than a
convenience, WebRTC in particular, assume it has not taken effect.

## The persona chooses the GPU, and the persona is a real choice

`--fingerprint-platform` sets OS identity, client hints, fonts, voices, locale,
screen geometry, hardware buckets — and the GPU. The claimed operating system
selects the capability cluster, on every host, whatever the host's own graphics
stack is running.

That works because a capability cluster belongs to a backend rather than to a
machine, and the catalogue holds exactly one backend per platform. Choosing the
persona therefore determines the backend, and the host has nothing left to
decide:

| Persona | Backend | Anchors drawn | Identities |
| --- | --- | --- | --- |
| `windows` | ANGLE/D3D11 | `windows-d3d11-intel-79dfeb5b4f99`, `windows-d3d11-nvidia-0947761dfbe9` | 15: six Intel UHD 630 device ids and nine NVIDIA boards |
| `macos` | ANGLE/Metal | `macos-metal-apple-850a91233555` | 12: M1 through M4 Max |
| `linux` | ANGLE/Vulkan | `linux-vulkan-nvidia-adf287b8f0ee` | 11: RTX 3090 through RTX PRO 4000 Blackwell |

The host column is gone from that table because the host no longer appears in
the answer. A Windows persona presents a Direct3D 11 cluster on a Windows
machine, on a Mac, on a Linux workstation with an NVIDIA card and on a Linux
server with no graphics device at all.

### The default persona depends on the host

A launch that does not pass `--fingerprint-platform` gets:

| Host | Default persona |
| --- | --- |
| macOS | `macos` |
| Windows | `windows` |
| Linux | `windows` |

The first two are the host's own OS, which is the safe case: a macOS host can
natively serve any macOS renderer the catalogue offers, and the same for
Windows. The third is deliberately not the host's own OS, and it is the one to
understand before deploying.

Windows-on-Linux is chosen as the Linux default because it is the least bad
cross-OS pairing and because it is what most deployments want. Least bad, not
free: what it costs is mostly a font question, and fonts are the one part the
operator can fix. See **Fonts are yours to install** below and
[docs/FONTS.md](FONTS.md), because on a Linux host this applies to a default
launch rather than to an opt-in.

`--fingerprint-platform=linux` is the opt-out. It composes the host's own OS on
a Linux machine and draws the Vulkan cluster.

### Cross-OS is a risk, not a free move

Serving a persona's GPU does not make every claimed OS equally safe. What the
persona controls is everything composed. What it does not control is everything
a fork cannot reach: which fonts are actually installed, and the kernel's own
timing behaviour. Those stay the host's.

So, in rough order of exposure: the host's own OS is safest; Windows on Linux is
the documented default and needs its fonts; macOS on Linux is the riskier
pairing, because the macOS core font set is 184 families and a Mac cannot be
claimed convincingly without them. A launch is given the pairing it asks for
either way, with the limitation named in `--fingerprint-explain` rather than
refused. On a Windows persona over a Linux host the report prints:

```text
  - the persona is windows on a linux host, which is this project's default
    pairing there and its least bad cross-OS one: the GPU cluster follows the
    persona, but the Windows font set does not install itself, and a Windows
    persona missing Windows faces is measurable in text metrics -- install the
    full set (docs/FONTS.md) or pass --fingerprint-platform=linux to compose
    the host's own OS
```

Any other cross-OS pairing gets the general form, naming installed fonts and
kernel timing as what stays the host's. Neither is a gate and neither is
page-visible.

### What the host still decides

One thing, and it is the subject of **Software rendering is measurable** below:
the rasteriser that actually draws. A claimed GPU's throughput and its rendered
bytes come from the host's backend, not from the claim, and no selection change
touches that.

## Fonts are yours to install

The browser can only show a page a font that is really on the machine, because
the moment a page draws text in a font, the shape and the width of that text
have to be real. So a profile subtracts: it hides the fonts the machine has that
the claimed device would not have. It never adds.

That makes installing the claimed platform's fonts a setup step you own, and
[docs/FONTS.md](FONTS.md) is the instructions. The browser assumes you have done
it, and nothing warns you if you have not. A persona running without its fonts
is a common reason a session gets blocked, and the strongest signal is absence,
since Menlo, Monaco, Zapfino, PingFang SC and Helvetica Neue ship with macOS
and cannot be removed.

What remains a limitation, rather than a setup step, is that no amount of
configuration substitutes for the files. A family the machine genuinely lacks
cannot be presented, so a Windows persona on a host without Windows fonts shows
a Windows computer with no Windows fonts.

All four routes a page has to ask about fonts go through one predicate, so they
cannot disagree: `document.fonts.check()`, `measureText` and CSS width,
`@font-face src:local()`, and `queryLocalFonts()`. A family the profile hides
takes the same branch an absent family already takes, so text still renders in
the next family the page asked for.

The filter serves whatever set the profile states, so the accuracy of the claim
is the accuracy of that list. A pack naming fewer families than the claimed
machine really has presents a machine with too few fonts, and
`queryLocalFonts()` reads the whole set at once. Widening a pack is a catalogue
change rather than a code change.

Generic CSS families, the claimed platform's own core UI faces, last-resort
fallback for glyph coverage, and web fonts a page loads with `@font-face` are
never filtered.

### Text measurement and script fallback

Under a Windows persona, text measurement matches Windows for every font family
installed on the host, because the metrics are read from the real font files
rather than replayed from a capture. Two things do not follow from that.

The generic families, meaning what `monospace`, `serif`, `sans-serif`, `cursive`
and `fantasy` resolve to, are Chrome preferences rather than font properties, so
a persona that does not carry them reports the host's choices. On Windows,
Chrome's `monospace` is Consolas, and a host without Consolas cannot report it.

What happens when a page uses a character no requested family covers depends on
which build you are running, because character fallback is implemented per
platform and only the Linux one consults a table of candidate family names.

On the Linux build, a Windows persona follows Windows' own fallback order, but
only 27 of Windows' 74 script entries name families a Windows font pack can
provide, and those 27 draw on just 11 distinct names: Times New Roman, Segoe UI,
Segoe UI Symbol, Tahoma, and the CJK set of Microsoft YaHei, SimSun, Microsoft
JhengHei, Malgun Gothic, Meiryo, Yu Gothic and MS PGothic. The other 47 name
families such as Nirmala UI, Segoe UI Historic, Leelawadee UI, David and
Sylfaen, so Devanagari, Tamil, Khmer, Hebrew, Georgian and similar scripts fall
back to the host's own font. Where the claimed pack does not carry a script's
families, the fallback is the host's font rather than the claimed platform's, so
a page renders that script slightly differently than the claimed OS would. That
is a difference rather than a contradiction, and it replaces a contradiction:
the font list used to say a family was absent while text measurement used it.

The Windows build already resolves fallback through the real Windows table,
because that is the platform whose behaviour is being described. The macOS build
does not have any of this: macOS fallback goes through CoreText, which never
consults a named candidate table, so a Windows persona on a Mac gets none of the
above rather than a reduced version of it. The visible symptom can still be the
same, a page rendering Han in the host's font, but the cause is different and no
source change to the fallback table would move it.

Installing the claimed platform's faces is the fix on every target, though for
different reasons on each. It is your part of the setup rather than something a
source change can substitute for. A Windows
persona whose Han fallback should pick Microsoft YaHei needs Microsoft YaHei on
the machine. The browser assumes that has been done rather than checking, so a
script whose Windows families the machine lacks keeps the host's own answer.
[docs/FONTS.md](FONTS.md) lists what to install.

## Software rendering is measurable

On a host with no usable GPU the browser renders through SwiftShader, a software
rasteriser, and a page can time that. Measured headed on an M4 Max, one page and
one binary across the two backends back to back: fill runs at 9.5 against 2517
giga-iterations per second, a factor of 264, and draw submission at 65 thousand
against 2703 thousand calls per second, a factor of 42, with the CPU baseline
between 3.9 and 4.1 ms in every run. Across seven runs the envelope was 130 to
264 times on fill and 28 to 42 times on draw submission. Dividing by host speed
does not hide that. Readback differs too: the same WebGL scene and the same 2D
canvas hash to different digests on the two paths.

No source change closes this. The only ways to close a timing gap are to make
software rendering fast or to slow real hardware down, and a deliberate timing
adjustment would itself be a new observable. What the fork does close on the
software path is the limit values, the extension list and the identity: the
backend a host happens to be running does not restrict which identity the
profile may serve. That is a narrower claim than it sounds, and it is worth
keeping narrow — it does not say any identity is safe on any host. Which
operating system the profile claims is a separate choice with its own
trade-offs, set out in **The persona chooses the GPU, and the persona is a real
choice** above. What the fork closes nothing about is render timing and
per-pixel output, and that is a property of the two rasterisers rather than of
this fork.

So the residual is a throughput and pixel question rather than a string
question. A site that times WebGL fill rate, or hashes a WebGL readback against
a corpus of known devices, can tell a software rasteriser from the card the
identity names. A site that reads the identity cannot. Prefer a host with a real
GPU for the workloads where WebGL throughput or canvas bytes are the thing being
scored; everything else runs on the GPU-less server most deployments actually
have.

Headless is not the problem, and never was. `--headless` does not imply software
rendering: on a host with a GPU new headless mode uses it, and on an M4 Max this
binary selects ANGLE/Metal and reports Apple M4 Max in every default
configuration, headed and headless alike.

WebGL numeric limits under software rendering are raised to real-hardware
values: `MAX_TEXTURE_SIZE` and `MAX_RENDERBUFFER_SIZE` 16384,
`MAX_VIEWPORT_DIMS` 32767 by 32767, `ALIASED_POINT_SIZE_RANGE` 1 to 1024. Stock
Chromium's SwiftShader reports 8192, 8192, 8192 and 1 to 1023. Those limits are
a property of the backend rather than of the silicon, so they are not universal:
this Mac's real Metal backend reports `MAX_TEXTURE_SIZE` 16384 but
`MAX_VIEWPORT_DIMS` 16384 by 16384 and `ALIASED_POINT_SIZE_RANGE` 1 to 511. The
32767 viewport and the 1024 point size are Windows Direct3D 11 values.

Raising the render-target limit doubles the rasteriser's span arrays from 64 MiB
to 128 MiB per GPU process: 4 bytes per span, 16384 spans per primitive, 128
primitives per batch, 16 pooled draw calls, and the pool never hands them back,
so resident memory climbs towards the full 128 MiB as a page touches more of it.
Measured GPU-process resident size under software rendering was 357 MB after a
heavy WebGL workload, against 173 MB for the same page on a real GPU and 221 MB
on a light page. None of it is readable by a page, so it is a deployment cost
rather than a fingerprint: budget roughly 128 MB of additional resident memory
per concurrent instance on hosts that render in software. Hosts with a real GPU
do not pay it.

GPU presence is detected from the host's DRM render nodes, which is implemented
for Linux only. On Windows and macOS a host with no usable GPU is not detected
automatically, and the backend has to be stated with `--use-angle`. Even on
Linux the check answers absence reliably and presence only probably: a host that
exposes a render node and still falls back to software rendering, a missing
Vulkan driver for instance, is not detected. `--use-angle` states the backend by
hand for that case too.

What that answer decides has changed with the GPU identity. It is no longer the
difference between claiming hardware and not claiming it: a hardware identity is
served either way. It is which cluster the persona is allowed to select. On a
GPU-less Linux server wrongly believed to have a device, the anchor is filtered
to the platform default, so a Linux persona lands on the same NVIDIA cluster it
would have drawn anyway and only a non-default persona sees a difference —
`--fingerprint-platform=windows` gets the Linux Vulkan cluster instead of the
Windows Direct3D 11 one, and the report says so.

On a host that renders in software and is given no profile to serve, with
`--fingerprint=host` for example, the browser presents its own SwiftShader as it
is. That build reports the raised limits above and, on Linux only, also offers
`KHR_parallel_shader_compile`, which stock Chromium's SwiftShader does not. Both
are enumerable differences from a stock software-rendering browser. Software
rendering on macOS and Windows stays stock in that respect, because the
extension's feature condition is Linux-only. The differences disappear once a
profile is served, because the profile then decides both the limits and the
extension list: see the subsections below.

### A GPU-less host serves a hardware GPU identity

This has not been built. Patches `0102`, `0103` and `0104` are in the series and
no binary has been produced from them, so read the rest of this subsection as
what the tree will do and check it yourself before relying on it.

It also replaces an earlier version of this subsection, which said a GPU-less
host would be given a measured software-rasteriser cluster and would "look like
ordinary headless Chrome". That behaviour was withdrawn before it ever shipped,
for the reason two paragraphs down.

A host with no graphics device draws a hardware anchor of the claimed platform.
On a Linux server with no usable GPU a seeded launch composes the whole profile,
and the graphics surfaces report the drawn anchor's renderer and vendor strings.
Under that host's default persona, which is Windows, that means one of the
fifteen Direct3D 11 identities the two Windows anchors offer, rotated by seed;
`--fingerprint-platform=linux` gets the eleven Vulkan NVIDIA ones instead.
Timezone, locale, Accept-Language, fonts, screen geometry, core count, memory,
media topology and voices compose as normal beside it.

The alternative was to report the host's own software rasteriser, and it is
worse on the axis that decides the outcome. `ANGLE (Google, Vulkan 1.3.0
(SwiftShader Device (LLVM 10.0...)))` names itself as a software rasteriser in a
field where no consumer machine does, and it is byte-identical on every host
that reports it, so one substring match sorts the launch into a population no
ordinary user is in. Getting the same answer out of a hardware identity running
over a software backend costs a page real work: time a fill, hash a readback,
allocate at the reported maximum. One of those is a string compare and the
others are probes, and the difference between them is the whole reason for this
decision.

`linux-swiftshader-google-6922d61bab83` is therefore not in the drawn candidate
set on any host. It stays in the corpus and stays reachable on request — by
`--fingerprint-anchor` naming it, or by `--fingerprint-gpu-renderer` and
`--fingerprint-gpu-vendor` stating the strings by hand — for the case where
presenting as stock headless Chrome is the thing you actually want.

`--fingerprint=host` is unaffected here as everywhere: it composes nothing, so a
GPU-less machine under it reports its own SwiftShader with the raised limits
described above. Host means host.

### Allocating at the reported maximum fails on a software backend

This is the residual the decision above leaves, it is measured, and it is the
one item on this page that a page can turn into a positive detection rather than
an inference.

On a software backend the reported WebGL limits are the claimed cluster's, and
the claimed maximum is not usable. Measured through the shipped
152.0.7977.83 artifact, allocating with `texStorage2D`, attaching, checking
framebuffer completeness, clearing to green and reading a pixel back:

| Backend | Reports `MAX_TEXTURE_SIZE` | 8192 | 16384 | 32768 |
| --- | --- | --- | --- | --- |
| ANGLE/SwiftShader | 16384 | complete, reads green | `FRAMEBUFFER_UNSUPPORTED`, reads black | same |
| ANGLE/Metal | 16384 | complete, reads green | complete, reads green | `GL_INVALID_VALUE` |

Read the shape rather than the numbers. On hardware the reported maximum is
usable and only sizes above it fail, which is what a real device does. On the
software backend the largest size that actually works is 8192, half what is
reported, and the failure is quiet: `texStorage2D` raises no GL error at all, the
framebuffer reports unsupported, and a texture cleared to green reads back
black. A page that allocates at the reported maximum and checks the pixel it
gets can tell the difference in one probe.

SwiftShader does not enforce any of these numbers. A 3D texture at eight times
the reported `MAX_3D_TEXTURE_SIZE` is accepted without an error, and multisample
renderbuffers at 4, 8 and 16 samples all succeed while granting 0. The reported
figures are soft constants there, not ceilings, which is what makes serving a
claim possible and what makes the claim unbacked at its own maximum.

Two things about the scope of this. It is not a regression from serving a
hardware identity: patch `0027` raised the software rasteriser's own
`MAX_TEXTURE_SIZE` from 8192 to 16384 by editing `OUTLINE_RESOLUTION`,
seventy-five patches before any of this work, and that is what put the reported
figure above the usable one. That patch's own header claims a 16384 texture
allocates and 32768 is correctly refused; the measurement above contradicts the
first half, so read the patch header for the change and this page for the
behaviour. And it is not unique to this fork: the product this one is measured
against reports 16384 on a software backend and fails at 16384 in the same way,
while stock Chrome reports 8192 and fails only above it. Stock is coherent here
because it reports what it can do.

`MAX_RENDERBUFFER_SIZE` is not measured. The renderbuffer arm of the probe
reported `FRAMEBUFFER_UNSUPPORTED` at 8192 as well as above it, so it does not
discriminate and no number for it belongs here. `MAX_VIEWPORT_DIMS` shows no
residual: SwiftShader reports 32767 by 32767 and a viewport at 32767 raises no
error.

### Pinning across platforms

`--fingerprint-anchor` is the one way to get a cluster from a platform the
persona does not claim, and it is honoured rather than refused, because a pin is
an explicit request. The launch records that it happened. Nothing else produces
that combination any more: a drawn launch takes its anchor from the claimed
platform, so the persona and the cluster always agree unless a pin makes them
disagree.

## Capabilities that depend on the machine, not the profile

A name in a profile does not create a capability. Where the machine cannot do
something, the browser reports that it cannot, because a name that does not do
what it promises is worse than the absence.

Two things commonly assumed missing from a Chromium fork are not missing here,
though one of them has a condition you have to know about.

**HEVC/H.265 is supported and decodes.**
`canPlayType('video/mp4; codecs="hvc1.1.6.L93.B0"')` returns `probably`,
`MediaSource.isTypeSupported` returns `true`, `MediaCapabilities.decodingInfo`
reports supported, smooth and power-efficient, WebCodecs
`VideoDecoder.isConfigSupported` returns true, and 8-bit Main and 10-bit Main10
files both decode with zero dropped and zero corrupted frames. Every value is
identical to stock Chrome 152 on the same machine. No GN argument is needed:
`enable_hevc_parser_and_hw_decoder` already defaults to true from
`proprietary_codecs` in `media/media_options.gni`, which makes
`enable_platform_hevc` true on macOS, Windows and Linux. That was measured on
macos-arm64, and it holds on macOS and Windows through the platform decoder and
on linux-x64 through patch `0061`'s software decoder. On linux-arm64 there is no
HEVC decoder unless the host exposes one, and Chromium reports that honestly
either way.

**Widevine DRM works, but the CDM is not part of the download.** Chromium
fetches it from Google at runtime, because the Widevine CDM is Google-licensed
proprietary software this project is not permitted to redistribute. Once the CDM
is present, `navigator.requestMediaKeySystemAccess('com.widevine.alpha', ...)`
resolves and `createMediaKeys()` succeeds, byte-identical to stock Chrome
including the `SW_SECURE_CRYPTO` and `SW_SECURE_DECODE` robustness levels, the
rejection of all three `HW_SECURE_*` levels, and the rejection of
persistent-license sessions. Until it is present, Widevine is absent and every
`requestMediaKeySystemAccess` call for it rejects with `NotSupportedError`,
which a detector can trivially distinguish from a real Chrome.

Three things follow, and the first is the one that bites.

**DRM needs a persistent `--user-data-dir`.** The CDM lands in
`<user-data-dir>/WidevineCdm/<version>/`, so a throwaway profile starts with no
CDM and no Widevine. The fetch is also not something you can count on: across
five fresh profiles with full network access, watched for 5 to 20 minutes each,
the CDM arrived exactly once. So "it will download on first run" is not a
promise, and the first profile to obtain it may take an unpredictable amount of
time.

**There is no race and nothing to retry.** Once the CDM is on disk it registers
before the first page paints. The first `requestMediaKeySystemAccess` call of
the first page succeeds, measured 9 to 40 ms after page load, on the first
attempt, and it works with no network to Google at all. So it is either there
from startup or not there for that launch, and a page that gets
`NotSupportedError` should not retry.

**The fetch honours `--proxy-server`.** The component update request goes
through the configured proxy and fails closed when the proxy blocks it, so it
does not leak the real address.

What a profile does control is `MediaCapabilities.decodingInfo()`'s
`powerEfficient`, which is a statement about the claimed GPU's fixed-function
decoder set. Whether a codec is `supported` is a statement about this machine
and answers from the decoders actually present, which is why the profile does
not touch it.

**Network speech voices.** The `localService: false` voices are served by
Chromium's network speech component against a Google endpoint that needs API
keys at build time. Without keys those voices cannot speak, so they are not
listed.

**Capture devices.** A profile describes how many microphones and cameras the
machine has and what they are called. Those devices appear in
`enumerateDevices()` and `getUserMedia()` opens them: a claimed camera delivers
video at the resolution and frame rate it advertises, and a claimed microphone
delivers audio at the sample rate and channel count it advertises. Real devices
the host has are never removed or replaced; the profile only tops the list up.
Labels still require a granted camera or microphone permission before a page can
read them, and `deviceId` and `groupId` are still per-origin values that differ
between sites. The residual is what is in the frames: a claimed camera delivers
a synthetic dim scene rather than a real room, and a claimed microphone delivers
a room-tone noise floor. A site that requires recognisable video of a person
will not get it.

Audio outputs are the exception inside that paragraph. `media.audiooutput_count`
is accepted by the schema and composed into every profile, and nothing reads it:
`enumerateDevices()` reports the host's real speakers, and a profile claiming a
different number of them changes nothing. Synthesising an output device would
mean synthesising one a page can actually play through, because a device that
enumerates and then fails to open is a worse signal than a truthful count, so
the count stays inert until that is built rather than being half-served.

**Web Share.** Linux has no platform share backend, so under a Windows or macOS
persona `share()` rejects with `AbortError` and the same message a share the
user dismissed produces. The residual is timing: no share sheet appears, so the
rejection is prompt where a real one waits for the user. That is inside the real
distribution rather than outside it, since a user can dismiss a sheet
immediately. Web Share also needs transient user activation, so a page that has
not been clicked gets `NotAllowedError` on every platform identically.

**Installed memory.** `navigator.deviceMemory` is not a limitation and is worth
stating because it is easy to assume otherwise. The profile's installed-memory
figure feeds Chromium's own rounding rather than overriding the result, so the
reported value is always one of 2, 4, 8, 16 or 32 GiB, the same set stock Chrome
can produce, and it always agrees with the `Device-Memory` request header
because both come from the same input. On a 36 GiB Mac, stock Chrome 152 and
this binary both report 32. V8 sizes its JavaScript heap from the same figure,
clamped to the host's real installed memory rather than from the raw claim, so
`performance.memory.jsHeapSizeLimit` and `console.memory.jsHeapSizeLimit` agree
with the claim and the machine can always actually back the ceiling it reports.
A page cannot falsify it by allocating. The honest consequence is that a profile
claiming 4 GiB gets a genuine 2 GiB heap ceiling, exactly as a real 4 GiB device
does, so a heavy page on that seed can run out of heap the way it would on that
machine. That is intended.

Until that change has compiled, the heap ceiling still comes from the host, and
the mismatch is worst exactly where this browser is most often run. On a 36 GiB
workstation nothing shows. On a 2 or 4 GiB VM the heap ceiling contradicts any
memory claim above it, and the contradiction is one property read away.

## Brand list

A Chromium-branded build emits two entries in `navigator.userAgentData.brands`
where Google Chrome emits three, because upstream adds the product brand only
under a Chrome-branded build. The GREASE brand and the version are correct,
since both derive from the major version. Sites that count brand entries can see
the difference.

## Capacity is presented downward only

A profile can claim fewer cores, less memory, a smaller screen, fewer codecs,
fewer fonts and fewer voices than the host has. It cannot claim more. A host
with 4 cores cannot present as a 16-core workstation, and requesting a screen
larger than the host's panel is refused rather than served.

Options the host cannot serve are dropped before the seed draws, so a small host
draws from a smaller set of identities than a large one.

WebGL limits on a software backend are the exception, and the next section is
about it. A backend that enforces nothing about the numbers it reports is not a
capacity the way core count is, so there the claim is served upward instead of
being clamped down. The price of that is a claimed maximum that cannot be
allocated at, which is stated in full under **Allocating at the reported maximum
fails on a software backend** above.

## The extension list, and the five names that are served

A profile whose GPU cluster matches the host's backend is served exactly.
Measured through the shipped binary on an Apple M4 Max running real ANGLE/Metal,
the `macos-metal-apple` anchor claimed 39 WebGL1 and 36 WebGL2 extensions and
delivered all of them, with nothing missing and nothing extra.

Everywhere else there is a gap between what a cluster claims and what the host's
GL stack implements, and it is measured. A SwiftShader host — the deployment
target — offers 36 WebGL1 and 30 WebGL2 names, byte-identical between this
project's macOS SwiftShader and the Linux SwiftShader capture in the corpus.
Against that list:

| Claimed cluster | WebGL1 short by | WebGL2 short by |
| --- | --- | --- |
| `linux-vulkan-nvidia` | 1 | 3 |
| `windows-d3d11-intel`, `windows-d3d11-nvidia` | 2 | 5 |
| `macos-metal-apple` | 3 | 7 |

The names involved are seven in total: `EXT_render_snorm`,
`EXT_texture_norm16`, `WEBGL_blend_func_extended`, `WEBGL_render_shared_exponent`,
`WEBGL_compressed_texture_pvrtc`, `WEBGL_provoking_vertex` and
`KHR_parallel_shader_compile`.

Five of those seven are served rather than dropped — every one whose extension
object exposes constants, internal formats or blend factors and no methods.
Blink has a complete implementation class for each; the only thing that was
refusing them on a software backend is a driver-support lookup. So a page
enumerates the list, reads the constants and hashes the result, and all of that
succeeds.

The remaining two of the seven are not served, and two further names that could
have been added are not either. Each for a reason rather than a size limit:

- `WEBGL_provoking_vertex`, and `OVR_multiview2` outside this list, carry
  methods. A page calls `getExtension()` and then calls methods on the object it
  gets back, so a name advertised without an implementation behind it fails at
  first use — a functional break rather than a tell.
- `EXT_disjoint_timer_query_webgl2` carries methods too, and serving it would be
  actively self-defeating: a working GPU timer on a software rasteriser hands a
  page the throughput ratio directly, and that ratio is the one GPU
  contradiction no string can cover.
- `KHR_parallel_shader_compile` has no methods, but its `COMPLETION_STATUS_KHR`
  is read through `getProgramParameter`, and a page polling it would never see a
  program finish. It is also unnecessary on the deployment target, where patch
  `0052` enables it natively on Linux SwiftShader — which is why the gap above
  is one name smaller on a Linux build for the three clusters that claim it.

The residual after that is narrow and worth stating precisely: a page that
*renders through* one of the five served names fails where a real device would
not. An R16 texture on a stack without `GL_EXT_texture_norm16` raises
`GL_INVALID_ENUM`. Every detector reads the extension list; almost none renders
through a norm16 format.

The removal direction is unchanged and still subtractive. A profile that does
not claim a name the host has still loses it, which is what keeps
`WEBGL_compressed_texture_astc`, `_etc` and `_etc1` off a Windows persona. An
empty profile list still disables both directions.

All of this is patch `0104`, which is in the series, is compile-unverified and is
in no binary. Read `getSupportedExtensions()` from a page on the host you deploy
on and compare it against what `--fingerprint-explain` says the profile claimed,
rather than trusting this page.

## Network quality and battery are profile values

Network information and battery state come from the profile rather than the
machine. `navigator.connection` reports the profile's `network.effective_type`,
`network.http_rtt_ms`, `network.downlink_mbps` and `network.save_data`, and the
Battery Status API reports the profile's `battery` values. A profile describing a
chassis with no battery reports what a real desktop reports: charging true, level
1.0, `chargingTime` 0, `dischargingTime` `Infinity`. The API is still there and
still resolves in that case. Neither section is invented when the profile omits
it: an absent section leaves that surface on the host's own value.

Those numbers do not track the real connection or the real battery, and there
are two residuals.

`navigator.connection.rtt` never equals a site's measured request timing, even
on stock Chrome: it is a network-quality percentile over recent observations,
multiplied by a per-origin salted factor between 0.90 and 1.10, rounded to the
nearest 50 ms and capped at 3 s. So a mismatch is not the tell. A persistent
gross mismatch is, a claimed 100 ms beside consistently measured 400 ms, and
nothing here addresses it because the browser does not measure the proxy exit's
round trip. That is the largest open residual on this surface.

The battery level is fixed for the launch. The dispatcher delivers one status
and never re-queries, so two reads agree and no `chargingchange`, `levelchange`
or `dischargingtimechange` event ever fires. That is deliberate, because
per-read drift would be a far stronger signal than a static level. The cost is
that a real laptop on battery does fire `levelchange` over a long session, so a
multi-hour session with a perfectly static level is itself a weak signal. A
fresh seed per launch means the level differs next launch.

## Every persona reports a desktop form factor

Chromium's form-factors client hint has no `Laptop` value, so
`Sec-CH-UA-Form-Factors` and `navigator.userAgentData` report `Desktop` for every
persona. A profile claiming a laptop panel while that header says `Desktop` is a
contradiction readable from one header. Closing it is a catalogue question rather
than something a patch can fix, since the vocabulary is Chromium's.

## The window chrome delta is the host's, not the profile's

`outerHeight - innerHeight` is the height of the browser's own frame, tabstrip
and toolbar, and `outerWidth - innerWidth` its side frame plus any classic
scrollbar. Both are platform-specific: 87 CSS pixels of height on macOS 26, 121
on Windows 11, 143 on both Linux reference hosts, and a zero width delta on all
four. A macOS persona served from a Linux host contradicts itself in that
subtraction with no screen value patched at all.

`window.outer_inner_delta_width` and `window.outer_inner_delta_height` are
accepted by the schema and composed into every profile, and nothing reads
either of them. That is not an oversight in the loader: the delta is the real
furniture of the window the host draws, so serving it from the profile would
mean reporting an `outerHeight` the window does not have, which then
contradicts `screenY` against the claimed available rect. The way to make the
subtraction true is to size the real window at launch so the host's own chrome
lands on the claimed delta, and neither the Python nor the Node launcher does
that yet. Until one of them does, the two fields record the reference
measurement and change nothing a page can read.

## The keyboard layout map is replayed, not composed

`navigator.keyboard.getLayoutMap()` is served from `keyboard.layout_map` when a
profile carries one, replaced whole rather than merged. Nothing composes one: no
dispersion table emits a keyboard section, so a launch that draws its identity
from a seed inherits the host's map, and the surface has no per-seed variation at
all. A profile derived from a capture does replay it, which is the path the
field exists for.

The catalogue used to compose a five-entry map — `KeyA`, `KeyQ`, `KeyW`, `KeyY`,
`KeyZ`, the letters that separate QWERTY from AZERTY and QWERTZ — on every
locale option. Because the loader replaces the host map whole, a profile carrying
that stub reported a keyboard with five keys, which no keyboard has, and the
reference machines report 48. The stubs are gone. An absent map inherits a real
one; a truncated map invents a device that cannot exist, and that is the worse
of the two.

Serving it per platform is not available yet, and the measurements say why the
obvious version of it would be wrong. The reference maps differ by platform in
one entry — `IntlBackslash` is `§` on macOS, `\` on Windows and `<` on Linux —
so a map is not interchangeable across a claimed platform even when the key count
matches. The 49-entry variant, which adds `IntlYen`, turned up on both Linux and
Intel-macOS captures of one host, so that key follows the physical keyboard
rather than the OS and no rule from a claimed platform can produce it.

## Canvas and audio are rendered, not replayed

There is no stored canvas bitmap or audio buffer to hand back. Those surfaces
come out of Chromium's own rasteriser and audio graph, running against the
profile's fonts, metrics, screen and GPU inputs. Two reads in one launch are
identical, which is what real hardware does, and the output is what this host
actually renders under those inputs rather than a recording of another machine.

An `OfflineAudioContext` render is fixed by the FFT kernel CPUID selects and by
the host libm, so it differs between arm64 and x86-64 hosts. Profiles are
partitioned by instruction set for that reason, and a profile does not move an
audio render across architectures.

**The audio device's own numbers are the host's.** `AudioContext.sampleRate` and
`destination.maxChannelCount` come out of the audio service from the real output
device, and the schema declares no key for either, so a profile cannot move them.
`audio.hardware_buffer_frames` IS served, and `baseLatency` is
`framesPerBuffer / sampleRate`, so the numerator follows the profile while the
denominator follows the host: a claimed 256 frames on a 44100 Hz host reports
0.005805 where the reference machine measured 0.005333. That is a different
value rather than a closer one, and 44100 against 48000 is itself a coarse
hint about the operating system. Both surfaces are recorded as unresolved in the
ledger rather than as served.

## Platform support

Four targets are the contract: `linux-x64`, `linux-arm64`, `macos-arm64` and
`windows-x64`. Three have built green on CI. No Windows build has completed, so
there is no Windows archive yet, and the packages' Windows acquisition path has
never been run against a real one.

There is no Intel macOS build, no 32-bit Windows build, and no Android or iOS
build. Personas are `windows`, `macos` and `linux`; there is no mobile persona,
and presenting as a phone would need touch input, mobile viewport behaviour and
a mobile GPU cluster that this catalogue does not have.

## The identity does not persist

A seed is never written to disk. Every launch with no `--fingerprint` draws a
new one and presents a new device, whether or not `--user-data-dir` is set.

A user-data directory carries cookies, storage and history. It used to carry the
seed as well; it no longer does. Playwright's `launch_persistent_context` reuses
one directory by design and therefore gets a different device each launch unless
the launch passes `--fingerprint`. Pin a seed for anything that should look like
a returning visitor.

An incognito or off-the-record context derives its identity from the same
profile. It does not get a second fingerprint.

## WebRTC

**Treat the address as leaking.** The relay below is written and applies to the
source tree. It has not been compiled, and nobody has yet watched the browser
gather ICE candidates through a real SOCKS5 proxy with no host candidate
emitted. Until that has happened, plan as though WebRTC publishes the host's
real public address, because that is what the previous behaviour did and it is
the assumption that costs you nothing if the relay works.

What the code does. WebRTC carries two separate things, the candidate text a
page reads over SDP and the packets themselves, and they used to disagree.
`--fingerprint-webrtc-ip` rewrites the candidate text and nothing else, so on
its own it left a cooperating peer reading the host's real public address off
the source of the arriving packets while the SDP said something else.

The packets are now meant to follow the proxy. `--fingerprint-webrtc-udp`
decides how, and with the switch absent the behaviour is automatic:

| Configured proxy | Result |
| --- | --- |
| none | direct UDP, as any browser does |
| single-hop SOCKS5 | every datagram relayed through the UDP association, so peers see the proxy |
| HTTP, HTTPS, SOCKS4, a proxy chain, a PAC script, per-scheme rules | no UDP socket is created |

That last row has a real cost. WebRTC gets no host candidate, no srflx candidate
and no UDP relay candidate, so a page that offers no TURN server over
`turn:...?transport=tcp` or `turns:` gets no working media. TCP is unaffected,
and a TURN server reached over TCP or TLS still produces a relay candidate
through the proxy.

`--fingerprint-webrtc-udp=direct` forces direct UDP under a proxy, which
publishes the host's real address. `block` never creates the socket.

Two residuals while the relay is in use. The host candidate carries the
association's ingress address, the address the proxy told the browser to send
to, and whether that is the same port a peer observes as the packet source
depends on the proxy implementation; the address peers actually see reaches the
page as the srflx candidate its own STUN server produces over the same
association. And the enterprise `WebRtcUdpPortRange` constraint no longer
applies, because the port a page sees is the proxy's rather than one this host
chose.

**The interface topology is the host's, whatever the address says.** Nothing in
the profile describes the machine's network interfaces, so the network service
still enumerates the real ones and every candidate carries their arithmetic. A
candidate's `priority` encodes which interface it came from and how that
interface ranks, `network-id` counts them, and `network-cost` is a direct readout
of the adapter type — 0 ethernet, 10 wifi, 50 unknown, 250 to 980 cellular, plus
one if the adapter is a VPN. mDNS does not cover any of it: the sanitiser
rewrites the address and leaves priority, foundation, `network-id` and
`network-cost` untouched. So a page that never learns an IP can still read how
many interfaces the machine has, what kind each is, and whether one of them is a
VPN.

## Proxies

HTTP, HTTPS, SOCKS4 and SOCKS5 all work, with authentication. UDP over SOCKS5
UDP ASSOCIATE carries proxied QUIC and HTTP/3.

`humanize: true` is rejected by the packages rather than accepted as a no-op.
There is no synthetic mouse or keyboard behaviour in this fork.

## Automation and remote debugging

The packages expose a Playwright-compatible launch API for Python and Node.
There are no .NET bindings, no Puppeteer adapter, no GUI profile manager and no
cloud profile sync.

**Use `--remote-debugging-pipe`, not `--remote-debugging-port`.** A page can
detect an open debugging port today, with no timing tricks:

```js
fetch('http://127.0.0.1:9222/json/version', { mode: 'no-cors' })
```

That promise resolves when the port is open and rejects when it is not, four out
of four trials. Two qualifications. It only works from a document in the local
or private address space; Local Network Access blocks it from an ordinary public
HTTPS page at this Chromium revision, where open and closed ports measured
indistinguishable. And `--remote-debugging-pipe` opens no socket at all and
never produced the signal.

Playwright uses the pipe by default and needs no change. Puppeteer defaults to a
TCP port, so pass the pipe explicitly.

There is a change written for this, and it has not been compiled. Its boundary
matters more than its existence, because people will assume more than it does:
it makes the port unreachable from a **page**, not from a **process**. Any
program on the machine that opens a socket still connects, which is exactly why
Playwright and Puppeteer keep working.

**A CDP session is detectable by timing, in ten lines of JavaScript.** This is
the largest automation tell and it is live in any binary built before the change
described below. Once a client sends `Runtime.enable`, exception handling and
console calls get measurably slower. Measured on the shipped artifact as
single-page ratios, so a detector needs no baseline of its own:

| Probe | Unattached | Attached, no `Runtime.enable` | After `Runtime.enable` |
| --- | --- | --- | --- |
| `try{throw 1}catch{}` at depth 240 over depth 2 | 1.0x | 1.0x | 26.5x |
| `console.log` | 1.0x | 1.0x | 10.8x |
| `console.trace` over `console.log` | 8.0x | 6.4x | 1.009x |

The third row is the sharpest, because it inverts: `console.trace` is normally
much more expensive than `console.log`, and under an attached session the two
cost the same. Both causes are in V8's debugger and both are now gated on the
delegate that only `Debugger.enable` installs, in the same unexercised change.
Until that has compiled, assume a site can see the session.

Three tells survive whatever happens to the port, and all three are real.

**JS coverage re-exposes the timing signal.** `page.coverage.startJSCoverage()`
sends `Debugger.enable`, which is the condition the change keys on, so a
coverage run puts the signal back. Do not collect coverage in a run you want to
be unremarkable.

**Heap size rises when a client attaches.** `usedJSHeapSize` and
`totalJSHeapSize` genuinely grow, because the inspector allocates in the
renderer's heap. Bucketing hides it on most pages, and a site-locked page reads
precise values. Nothing fixes this without lying about the heap.

**One loopback round trip remains.** With the port open the kernel completes the
TCP handshake before the browser hangs up, so an open port and a never-open one
differ by roughly 0.1 ms at the median. That is one tick of `performance.now`'s
resolution in a page that is not cross-origin isolated, which is why forty
samples did not separate them: the distributions overlap fully. Small, not zero.

The widely repeated console probes do not work, on this build or on stock
Chrome, and all of these measured identical attached and unattached: a getter on
an `Error`'s own `stack` property, `Proxy` traps, plain-object getters,
`console.table` and `console.dir`, `toString`, `valueOf` and
`Symbol.toPrimitive`, `Error.prepareStackTrace`, `Error.captureStackTrace`,
`Error.stackTraceLimit`, and `Function.prototype.toString`. What did leak was one
step past upstream's own guard: a `stack` accessor installed on
`Error.prototype` rather than on an instance. So the real signals were timing
and that one accessor, neither of which is what the folklore describes.

## Two measurement gaps ship open

Both need hardware nobody here has, so they are stated rather than closed.

**Cross-OS WebGL is unmeasured.** Every WebGL comparison behind the
cross-platform numbers was taken between two software rasterisers, so how much
of the difference is the operating system and how much is the test environment
is not known. Settling it needs a Linux host with a discrete GPU and a capture
from a Windows machine with the same GPU. Until then, treat a cross-OS WebGL
claim as untested rather than verified. That case is now the ordinary one rather
than the exotic one, because the persona picks the cluster on every host: a
Windows persona on a Linux server presents a Direct3D 11 cluster, and how that
composes against a real Windows machine is exactly what has not been measured.
It is the largest unmeasured thing behind the current default.

**Three GPU clusters were measured on another Chromium.**
`windows-d3d11-nvidia-0947761dfbe9` was captured on 153.0.8010.37, and
`linux-vulkan-nvidia-adf287b8f0ee` and `linux-swiftshader-google-6922d61bab83`
on 152.0.7977.82, against a 152.0.7977.83 binary. Capability tables move between
releases, so those three can differ from this binary in version-bearing fields.
The other two, `macos-metal-apple-850a91233555` and
`windows-d3d11-intel-79dfeb5b4f99`, are on the pinned build.
`--fingerprint-explain` reports the caveat when a launch draws one of the three.

## Reporting a limitation

`--fingerprint-explain` prints, per surface, the resolved value, where it came
from, and any limitation that applies to this host. It writes to stdout and is
not readable by a page. If a surface is wrong and this page does not explain it,
that is a bug worth filing.
