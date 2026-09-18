# Flag reference

Every switch the browser accepts for fingerprint control, what it takes, and
what it changes. None of them are required: a launch with no flags composes a
complete device and presents it.

Command-line switches are not readable from a page. Passing one of these does
not add an observable.

## The seed

```sh
./chrome --fingerprint=12345
```

`--fingerprint` selects the whole identity. The same seed produces the same
device on any host that can serve it, on every launch, with nothing stored on
disk. Without it, each launch draws a fresh seed and presents a different
device.

The value is any printable ASCII up to 512 bytes. Integers are the usual choice.
An empty, over-long or non-printable value refuses the launch on stderr and
exits non-zero. It does not fall back to anything, because a typo that quietly
presents the operator's real machine is indistinguishable from success until a
site has already clustered the real device.

The seed feeds a SHA-256 derivation over the profile schema version, the
catalogue version, the Chromium build, the persona and the seed itself. Changing
the browser build or the catalogue therefore changes what a seed selects, by
design: a seed names a device drawn from one specific table, and silently
remapping it onto a new table would be worse than a new identity.

## Turning composition off

```sh
./chrome --fingerprint=host
```

Every surface reports the machine's own value:

```text
apostate fingerprint composition

  composition         disabled
  seed source         none: the host is inherited

Every surface is the host's own value, which is what --fingerprint=host
asks for. No layer below it is active, so there is nothing to report per
surface: the profile is the host.
```

Six spellings mean this, all case-insensitive: `host`, `off`, `false`, `0`,
`disable` and `disabled`. `host` is the canonical one.

Nothing is composed under any of them, so there is no profile for a persona, a
pinned anchor or a per-field override to land on. Combining one of those with
host mode refuses the launch rather than half-applying it. `--fingerprint-explain`
still works.

Use it to find out whether a problem is this browser or the environment. On a
genuine Windows machine it is the fastest way to tell those two apart.

## The persona

```sh
./chrome --fingerprint=12345 --fingerprint-platform=windows
```

`--fingerprint-platform` takes `windows`, `macos` or `linux` and defaults to the
host's own platform. It sets `navigator.platform`, the User Agent, the Client
Hints platform and version, the OS release, the font set, the voice table, the
screen geometry pool, the window chrome deltas and the hardware buckets.

It does not move the GPU. The graphics capability cluster is selected from what
the host's graphics stack can serve, so a Windows persona on a Mac presents an
Apple GPU. [docs/LIMITATIONS.md](LIMITATIONS.md) has the table of which
host-and-persona pairs are coherent.

## Per-field overrides

An explicit switch sets one field and the seed fills in the rest, so the result
is one device with a correction rather than two partial identities.

| Flag | Value | Field it sets |
| --- | --- | --- |
| `--fingerprint-gpu-vendor` | exact `UNMASKED_VENDOR_WEBGL` string | WebGL unmasked vendor |
| `--fingerprint-gpu-renderer` | exact `UNMASKED_RENDERER_WEBGL` string | WebGL unmasked renderer |
| `--fingerprint-hardware-concurrency` | positive integer | `navigator.hardwareConcurrency` |
| `--fingerprint-device-memory` | positive integer, GiB | Installed memory |
| `--fingerprint-screen-width` | positive integer, CSS px | `screen.width` |
| `--fingerprint-screen-height` | positive integer, CSS px | `screen.height` |
| `--fingerprint-timezone` | IANA name, such as `America/New_York` | Timezone for `Intl` and `Date` |
| `--fingerprint-locale` | `Accept-Language` list, such as `en-US,en` | Accept-Language, and the voice table keyed from it |

Every refusal writes to stderr and exits non-zero. Nothing is silently ignored,
because an operator who mistypes a switch and is not told runs a whole session
believing a field was spoofed when it was their own hardware.

The two GPU strings narrow the choice before the seed draws, rather than
overwriting a finished profile. The limits, extensions, shader precisions and
WebGPU adapter all come from the anchor those strings were measured on, so a
renderer written over a finished profile would sit on a capability table
belonging to different silicon. A name the resolved anchor never measured is
refused, with the servable identities listed.

`--fingerprint-hardware-concurrency` is refused above the host's real logical
core count and `--fingerprint-device-memory` above the host's installed memory.
A claim below the machine is unfalsifiable and a claim above it is not: a page
can measure parallel throughput and can allocate until allocation fails.

The screen switches are applied before the available rectangle is derived, so
`availWidth`, `availHeight`, `availLeft` and `availTop` follow from the same
desktop furniture the seed drew. A size smaller than a `--window-size` the same
launch asked for is refused, and so is one the drawn insets cannot fit inside.
There is no display yet at composition time, so `--window-size` is the only host
bound available and the host's real panel size is not checked.

`--fingerprint-locale` is applied where the locale policy resolves, not over the
finished profile, because the speech-voice table is keyed on the resolved
Accept-Language list. It moves no keyboard layout, because the locale policy no
longer carries one: the maps it used to set were a five-key stub identical on
every option, and the layout a machine reports follows its physical keyboard
rather than its Accept-Language list. A locale that does not match the physical
layout is an ordinary thing on a real machine, so the map stays the host's unless
a capture-derived profile replays one. See
[known limitations](LIMITATIONS.md).

A value that is not one of the catalogue's own buckets is honoured and reported.
`--fingerprint-explain` names the field, the value, the command line as the
layer that decided it, and the drawn value it displaced, and says that a
hand-chosen value has no prevalence data behind it and may be more distinctive
than a drawn one.

## Inspecting a launch

```sh
./chrome --fingerprint=12345 --fingerprint-explain
```

Prints the composition report to stdout and exits without opening a window. The
report is three parts: what the launch resolved, one row per surface, and the
limitations that apply on this host.

```text
apostate fingerprint composition

  chromium            152.0.7977.83
  profile schema      3
  catalogue           v2 (tables 0dff1dd6658e...)
  platform persona    macos
  host platform       macos
  host cores          14
  host memory         38654705664 bytes
  host backend        metal (platform default, not probed)
  seed                12345
  seed source         --fingerprint
  reproduce with      --fingerprint=12345
  root                60eab51485a4...

surface                          layer       evidence               value
anchor                           anchor      physical-ground-truth  macos-metal-apple-850a91233555
os_release                       dispersion  catalogue-value        macos-26-5-0
gpu_identity                     dispersion  physical-ground-truth  apple-m4-max
cpu                              dispersion  physical-ground-truth  cores-14
memory                           dispersion  catalogue-value        gib-8
panel                            dispersion  catalogue-value        mba13-default
locale                           dispersion  catalogue-value        en-gb

limitations
  - anchor macos-metal-apple-850a91233555 has one measured member, so no
    identity rotation is offered on its capability cluster
  - the host graphics backend was taken to be the platform default (metal)
    because the GPU process does not exist yet at composition time; pass
    --use-angle to state it
```

The `evidence` column says where each value came from:
`physical-ground-truth` is a measurement from a real device, `catalogue-value`
is authored from platform release history, and a surface left to the host says
so.

The `reproduce with` line carries the exact argument that recreates the launch
being reported. No seed is stored on disk, so that line is how you keep a random
identity you liked. A launch with no `--fingerprint` reports the seed it drew:

```text
  seed                616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
  seed source         drawn from OS entropy
  reproduce with      --fingerprint=616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
```

The report goes to stdout and is never exposed to a page.

## Troubleshooting a block

Run `--fingerprint-explain` first. It is the only thing that will tell you what
this launch actually claimed and what this host could not serve, and most blocks
turn out to be named in its `limitations` block.

```text
limitations
  - anchor macos-metal-apple-850a91233555 has one measured member, so no
    identity rotation is offered on its capability cluster
  - the host graphics backend was taken to be the platform default (metal)
    because the GPU process does not exist yet at composition time; pass
    --use-angle to state it
```

Each line has an action:

- **A graphics-backend line** means composition guessed the backend. If the
  guess is wrong, every GPU value is drawn against the wrong cluster. State the
  real one with `--use-angle`.
- **An identity-rotation line** means this anchor has one measured member, so
  every launch on this host presents the same GPU strings regardless of seed.

Fonts are not in that list and are not meant to be. The report cannot tell you
whether the claimed platform's faces are on the machine, and it does not pretend
to. That one is yours to confirm:
[docs/FONTS.md](FONTS.md) says what to install and how to read which packs this
launch drew.

If the report looks right and the block persists, check the two things it does
not cover. Whether the site needs WebRTC, and whether the host renders in
software. Both are in [docs/LIMITATIONS.md](LIMITATIONS.md).

## Pinning the GPU cluster

```sh
./chrome --fingerprint-anchor=macos-metal-apple-850a91233555
```

`--fingerprint-anchor` pins the measured GPU capability cluster instead of
letting the seed draw one. A pin deliberately skips the backend filter, because
an explicit request is worth honouring, so a cross-backend pin is accepted and
records a limitation: the backend decides which extensions exist, and a pinned
cluster from another backend serves a shorter extension list than the card it
names. Anchor ids are listed in
[corpus/anchors/README.md](../corpus/anchors/README.md).

## WebRTC

```sh
./chrome --fingerprint-webrtc-ip=203.0.113.7
```

`--fingerprint-webrtc-ip` takes an IP literal and replaces the address in the
host and srflx ICE candidates with it. It rewrites the candidate text and
nothing else: the packets still leave from wherever they were going to leave
from. There is no `auto` value; passing one puts the literal string `auto` in
the SDP.

```sh
./chrome --fingerprint-webrtc-udp=block
```

`--fingerprint-webrtc-udp` decides where the packets go.

| Value | Behaviour |
| --- | --- |
| absent | Automatic. Relay through the configured proxy when it can carry datagrams, go direct when no proxy is configured, and create no UDP socket at all when the configured proxy cannot relay. |
| `direct` | Force direct UDP even under a proxy. An explicit opt-in to publishing the host's real address. |
| `block` | Never create a WebRTC UDP socket. |

[docs/LIMITATIONS.md](LIMITATIONS.md) has what WebRTC does and does not hide.

## Supplying a profile directly

```sh
./chrome --apostate-profile="$(base64 < profile.json | tr -d '\n')"
```

`--apostate-profile` takes a base64-encoded profile JSON object and skips
composition entirely. Coherence and servability are then yours to get right, not
the catalogue's. A value that does not decode refuses the launch and exits
non-zero rather than composing something else, and the message names the form
above, because passing a path or raw JSON used to log one line and then present
a different device.

Validate the file first:

```sh
python3 scripts/validate-profile.py profile.json
```

`resources/fingerprints/` holds valid example profiles that load as they are.
The field contract is [docs/PROFILE_SPEC.md](PROFILE_SPEC.md) and the schema is
[config/profile.schema.json](../config/profile.schema.json).

## Precedence

Strongest first:

```text
--apostate-profile        a profile you supply; nothing is recomposed
--fingerprint=host        no composition at all; every surface is the host's
--fingerprint-*           one field each, on top of the seed's values
--fingerprint=<seed>      the identity that seed selects
fresh OS entropy          the default, a new identity every launch
```

No seed is stored anywhere in this chain. A seed is either on the command line
or drawn for that one launch.

## Chromium flags worth knowing

These are upstream switches, unchanged, that interact with the identity.

| Flag | Why it matters |
| --- | --- |
| `--user-data-dir=DIR` | Keeps cookies, storage and history. Does not keep the identity. |
| `--proxy-server=URL` | HTTP, HTTPS, SOCKS4 and SOCKS5, with authentication. UDP over SOCKS5 UDP ASSOCIATE carries proxied QUIC and HTTP/3. |
| `--use-angle=BACKEND` | States the graphics backend by hand. Composition cannot probe it, because the GPU process does not exist yet when the profile is built, so it assumes the platform default. |
| `--headless` | Supported, and it does not imply software rendering. On a Mac this binary selects ANGLE/Metal in every default configuration including `--headless=new`. |
| `--lang=TAG` | Sets the UI language independently of the profile's locale, which is usually not what you want. |
| `--remote-debugging-pipe` | Opens no socket. Use this rather than a port: a page in the local or private address space can detect an open debugging port. Playwright uses the pipe by default; Puppeteer defaults to a port. |
| `--window-size=W,H` | Sets the window, not the viewport. The viewport is smaller by the browser chrome and it settles shortly after load rather than immediately. |

That last row matters if you assert on it. Reading `window.innerHeight`,
`visualViewport.height` or `documentElement.clientHeight` in the first script of
a page gives a provisional number that is corrected within about a second: with
`--window-size=1280,800`, twelve launches out of twelve reported 684, 685 or 692
first and 657 once settled, and the early value varied run to run while the
settled one did not. So read viewport height after load, and treat an early
reading as noise if you are recording a fingerprint. This is ordinary browser
behaviour and a real Chrome restoring a window does the same thing.

Two to avoid:

`--disable-gpu` makes Chromium's GPU info report the literal strings `Disabled`
for vendor, renderer and version, because `CollectGraphicsInfoGL` returns early
without calling `glGetString`. WebGL reaches the driver through the command
buffer and keeps reporting a real ANGLE string, so the two contradict each
other. This has already produced a wrong result here once.

`--no-sandbox` is a deviation from a normal launch in its own right. It is
unavoidable as root, so run as a normal user instead.

## The packages

The Python and Node packages take the same values through named arguments, and
anything else through `args`:

```python
from apostate import launch

browser = launch(
    fingerprint=12345,
    fingerprint_platform="windows",
    proxy="http://user:pass@proxy:8080",
    geoip=True,
)
```

```javascript
import { launch } from "@heretic-hq/apostate";

const browser = await launch({
  fingerprint: 12345,
  fingerprintPlatform: "windows",
  proxy: "http://user:pass@proxy:8080",
  geoip: true,
});
```

`geoip: true` resolves the locale and timezone from the network exit before the
browser starts, through the proxy when one is configured. A lookup failure is
reported rather than replaced with `UTC` and `en-US`.

Proxy credentials go into Chromium's in-memory `HttpAuthCache`, the same place
interactively typed credentials go. That cache is per network context, is never
written to disk, and is not reachable from a page. Without it every new
connection costs a 407 round trip and multi-round authentication schemes never
finish.

`humanize: true` is rejected rather than accepted as a no-op. There is no
synthetic input behaviour in this fork.

[docs/PROFILE_SPEC.md](PROFILE_SPEC.md) documents the full launch configuration
and the package entry points.
