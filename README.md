# Apostate

An anti-detect Chromium fork. The fingerprint is changed in C++ where Chromium
produces the value, so there is no injected JavaScript, no CDP override and no
wrapped property for a page to find.

Free and open source, GPL-3.0. No paid tier, no licence key, no gated build, no
telemetry, no phone-home.

- [Flag reference](docs/FLAGS.md)
- [Known limitations](docs/LIMITATIONS.md)
- [Profile and launch specification](docs/PROFILE_SPEC.md)

## Install

```sh
pip install apostate
pip install patchright        # or: pip install playwright
```

```sh
npm install @heretic-hq/apostate
npm install patchright        # or: playwright-core, or puppeteer-core
```

**The browser is not bundled.** The package is a launcher; the browser is a
separate 150 MB archive that it fetches on first use, verifies against a SHA-256
manifest shipped inside the package, extracts and reuses. You do not run
`playwright install`: Apostate supplies its own browser and a Playwright
download would give you the wrong one.

Both packages share one browser install on every platform, so having both does
not download twice. `python/README.md` and `npm/README.md` have the option
tables and the cache layout.

The install and launch paths are complete and verified end to end against a
local copy of the release archive. No release is tagged yet, so there is nothing
published to fetch and `launch()` reports that instead of downloading. Until
then, [build the archive yourself](docs/BUILD.md) and point the package at it.
Once a tag exists, first use fetches
`https://github.com/heretic-hq/apostate/releases/download/v<version>/apostate-<chromium-version>-<platform>.tar.zst`,
which is what `binary_info()["artifact_url"]` returns. [docs/RELEASE.md](docs/RELEASE.md)
covers how releases are cut and verified.

## Launch

```python
from apostate import launch

browser = launch()
page = browser.new_page()
page.goto("https://example.com")
print(page.title())
browser.close()
```

```javascript
import { launch } from "@heretic-hq/apostate";

const browser = await launch();
const page = await browser.newPage();
await page.goto("https://example.com");
console.log(await page.title());
await browser.close();
```

Or run the binary directly, which needs no flags at all:

| Platform | Command |
| --- | --- |
| Linux | `./chrome` |
| macOS | `./Chromium.app/Contents/MacOS/Chromium` |
| Windows | `chrome.exe` |

`--user-data-dir` keeps cookies, storage and history across launches. It does
not keep the fingerprint:

```sh
./chrome --user-data-dir=./work-profile
```

## Default behaviour

A launch with no flags composes a complete device profile and presents it. GPU
identity and capability tables, CPU core count, installed memory, screen
geometry and window chrome, fonts, media devices, speech voices, locale,
timezone and theme all come from that profile. The host's own values are not
what a page sees.

Every launch draws its own seed, so every launch is a different coherent
device. Nothing about the identity is written to disk.

| Launch | Identity |
| --- | --- |
| no flags | a fresh device, every launch |
| `--fingerprint=SEED` | the device that seed selects, every launch, on any host |
| `--fingerprint=host` | no profile; the host's real values |

Two reads of the same value inside one launch always agree. There is no canvas
noise, no WebGL noise and no audio noise anywhere in this browser. Variation
comes from a different seed, which is a different device.

## Headless Linux servers

This is the deployment most Apostate installs run in, and it is supported
directly rather than tolerated. No GPU is required and no graphics switch has to
be passed:

```sh
./chrome --headless=new --fingerprint=12345
```

No GPU is needed because the claimed operating system, not the host's graphics
stack, selects the GPU identity. A GPU-less server presents the capability
cluster and renderer string of the OS it claims, and the host's own software
rasteriser is not what a page sees.

**On a Linux host the default claimed OS is Windows, so install the Windows
fonts.** That is the one setup step this deployment has, and it is the most
common cause of a session being blocked. A Windows persona whose Windows faces
are missing is a Windows machine without Arial, which is not a machine that
exists, and text metrics measure it. The families, where to copy them from and
how to check are in [docs/FONTS.md](docs/FONTS.md). The alternative is
`--fingerprint-platform=linux`, which composes the host's own OS and needs
nothing installed.

Windows-on-Linux is the default because it is the least bad cross-OS pairing and
what most deployments want, not because it is free. A macOS persona on a Linux
host is the riskier one: the macOS core set is 184 families.

What a page can still tell on such a host is render *timing* and per-pixel
output, which come from the rasteriser rather than from the identity, and one
allocation probe: a texture at the reported `MAX_TEXTURE_SIZE` does not
actually allocate on a software backend.
[docs/LIMITATIONS.md](docs/LIMITATIONS.md) has the measured numbers for all of
it, and the status: this is new in this release and is on that page's list of
things written but not yet run, so read the renderer string and
`getSupportedExtensions()` from a page on your own host before relying on
either.

Both packages default to headless, so the `launch()` examples above run
unchanged on such a host.

### Headed on a virtual display

For the most aggressive targets, the suites that score behaviour as well as the
fingerprint, run headed on a virtual display instead. There is still no GPU
involved: the display is an X server with nothing behind it.

```sh
sudo apt install xvfb
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99
```

Then launch headed, behind a residential proxy:

```python
from apostate import launch

browser = launch(
    fingerprint=12345,
    headless=False,
    proxy="http://user:pass@residential-host:8080",
)
```

```javascript
import { launch } from "@heretic-hq/apostate";

const browser = await launch({
  fingerprint: 12345,
  headless: false,
  proxy: "http://user:pass@residential-host:8080",
});
```

`DISPLAY` is read from the environment the launcher runs in, so exporting it
before the script is the whole of the wiring. The binary run directly takes the
same environment:

```sh
DISPLAY=:99 ./chrome --fingerprint=12345
```

## Pin an identity

A random identity per session looks like a different machine every time. Hitting
one site repeatedly from one address with a new machine each time is its own
signal, and scoring systems such as reCAPTCHA v3 reward a returning visitor. Pass
a seed to get the same device back:

```sh
./chrome --fingerprint=12345
```

```python
browser = launch(args=["--fingerprint=12345"])
```

The seed is any printable ASCII up to 512 bytes. Integers are the usual choice.
Nothing is stored, so the same seed gives the same device on any host that can
serve it, which makes a seed portable in a way a copied profile directory is
not.

`--fingerprint-platform` chooses which operating system the identity presents
as, and the GPU follows it:

```sh
./chrome --fingerprint=12345 --fingerprint-platform=windows
```

The default depends on the host: a macOS host claims macOS, a Windows host
claims Windows, and a Linux host claims Windows. The first two are the host's
own OS. The third is not, deliberately — it is the least bad cross-OS pairing
and what most deployments want — and it is the case that needs fonts.

A persona that does not match the host needs that platform's fonts installed on
the machine, which is a one-time setup step you do yourself:
[docs/FONTS.md](docs/FONTS.md). Running without them is a common reason a
session gets blocked. Read [known limitations](docs/LIMITATIONS.md) too, because
the persona moves the GPU identity on every host while the rasteriser that
actually draws stays the host's.

To see exactly what a launch decided and why, ask it:

```sh
./chrome --fingerprint=12345 --fingerprint-explain
```

It prints the resolved value per surface, where the value came from, and the
limitations that apply on this host, then exits. Output goes to stdout and is
not reachable from a page.

The report ends with the argument that recreates the launch, which is how you
keep a random identity you liked:

```text
  seed                616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
  seed source         drawn from OS entropy
  reproduce with      --fingerprint=616c9fdee878b07b0ffab172936c21a7da947f77fa7153f5b1aba863c19acb0d
```

It is also the first thing to run when a site blocks you. The report carries a
`limitations` block naming what this host could not serve, and most blocks turn
out to be listed there. [docs/FLAGS.md](docs/FLAGS.md) walks through the three
common ones and what to do about each.

## Coming from a persistent profile directory

A user-data directory used to carry the seed, so relaunching the same directory
reproduced the identity. It no longer does. `--user-data-dir` keeps cookies,
storage and history; the device is drawn fresh every launch.

This matters most for Playwright's `launch_persistent_context`, which is the
common automation entry point and reuses one directory by design. Under the
current default it gets a new device each launch. Pass `--fingerprint` to get
the old behaviour:

```python
browser = launch_persistent_context(
    user_data_dir="./work-profile",
    args=["--fingerprint=12345"],
)
```

Pin the seed whenever a site should recognise the visitor. Returning-visitor
scoring is the case where a rotating device actively hurts: the same cookies and
the same address arriving on different hardware every session is a worse story
than either signal alone.

## Flags

| Flag | Value | Effect |
| --- | --- | --- |
| `--fingerprint` | printable ASCII, up to 512 bytes | Seed for the whole identity. Same seed, same device. |
| `--fingerprint=host` | `host` | Compose nothing and present the host's real values. |
| `--fingerprint-platform` | `windows`, `macos`, `linux` | Which OS the identity presents as, GPU included. Defaults to the host's own OS on macOS and Windows, and to `windows` on Linux. |
| `--fingerprint-anchor` | anchor id | Pin the GPU capability cluster instead of letting the seed draw one. |
| `--fingerprint-explain` | none | Print the composition report to stdout and exit. |
| `--fingerprint-gpu-vendor` | exact WebGL vendor string | WebGL `UNMASKED_VENDOR_WEBGL`. |
| `--fingerprint-gpu-renderer` | exact WebGL renderer string | WebGL `UNMASKED_RENDERER_WEBGL`. |
| `--fingerprint-hardware-concurrency` | positive integer | `navigator.hardwareConcurrency`. |
| `--fingerprint-device-memory` | positive integer, GiB | Installed memory. |
| `--fingerprint-screen-width` | positive integer, CSS px | `screen.width`. |
| `--fingerprint-screen-height` | positive integer, CSS px | `screen.height`. |
| `--fingerprint-timezone` | IANA name | Timezone, for example `America/New_York`. |
| `--fingerprint-locale` | `Accept-Language` list | For example `en-US,en`. |
| `--fingerprint-webrtc-ip` | IP address | Replace the address in WebRTC ICE candidate text. |
| `--fingerprint-webrtc-udp` | `direct`, `block` | Where WebRTC's UDP packets go. Absent means relay through a proxy that can carry datagrams, direct with no proxy, and no socket at all under a proxy that cannot. |
| `--apostate-profile` | base64 JSON | Launch a profile you composed yourself, bypassing the seed. |

A per-field switch sets that one field and the seed fills in the rest. A value
the host cannot serve is refused on stderr and the launch exits non-zero rather
than quietly ignoring it.

Precedence, strongest first: `--apostate-profile`, then `--fingerprint=host`,
then the per-field overrides, then `--fingerprint=<seed>`, then the fresh seed a
bare launch draws. `--fingerprint=host` is the one row that refuses rather than
outranks: it composes nothing, so pairing it with a persona, an anchor pin or a
per-field override stops the launch instead of quietly winning.
`--fingerprint-explain` still works with it.

Every standard Chromium flag still works, including `--proxy-server`,
`--headless`, `--user-data-dir` and `--lang`. Drive automation with
`--remote-debugging-pipe` rather than `--remote-debugging-port`: a page can
detect an open debugging port. Playwright already uses the pipe.

[docs/FLAGS.md](docs/FLAGS.md) is the full reference, with accepted values,
precedence and the profile fields behind each flag.

## What is and is not changed

Changed, from the profile:

- GPU vendor and renderer strings, WebGL and WebGPU extensions, numeric limits
  and shader precision
- Logical core count and installed memory
- Screen size, available area, device pixel ratio, colour depth, colour gamut,
  HDR, window chrome deltas
- Platform, OS version, architecture, bitness, WoW64, form factor, and the User
  Agent and Client Hints built from them
- Font enumeration and generic family mapping
- Media device counts, as a floor: extra inputs are added to reach the count,
  and the host's own devices are never removed
- Network information and battery state
- Speech voice list
- Locale, Accept-Language, timezone, keyboard layout
- Colour scheme, accent colours, pointer and hover capability
- Hardware video decode reported by `MediaCapabilities`

Rendered against those inputs rather than replayed: canvas, text metrics, client
rects and audio come out of Chromium's own rasteriser and audio graph, running
under the profile's fonts, screen and GPU values.

Not changed, and not claimable:

- The Chromium version. The binary really is the version it reports.
- Anything the machine cannot do. A profile presents fewer cores, less memory,
  a smaller screen and fewer codecs than the host has, never more.
- The rasteriser that actually draws. The persona moves the GPU identity, so the
  graphics backend *is* claimable now; what is not is throughput and rendered
  bytes. A software backend under a discrete-GPU identity renders at software
  speed and hashes differently, and a texture at the reported
  `MAX_TEXTURE_SIZE` does not allocate — see
  [Headless Linux servers](#headless-linux-servers) and
  [docs/LIMITATIONS.md](docs/LIMITATIONS.md).
- Fonts that are not installed. Enumeration removes families; it cannot add one
  without the font file.
- The WebRTC packet source, for now. A relay through a SOCKS5 proxy is written
  and has not been exercised, so plan as though a peer that completes a
  connectivity check sees the real address.
- Widevine DRM without a persistent `--user-data-dir`. The CDM is fetched at
  runtime and stored there, so a throwaway profile has no DRM.

[docs/LIMITATIONS.md](docs/LIMITATIONS.md) has the full list with the reason for
each, and it opens with the behaviours in this release that have been written
but not yet run.

## Platform support

| Target | Archive | Built |
| --- | --- | --- |
| `linux-x64` | `apostate-152.0.7977.83-linux-x64.tar.zst` | yes |
| `linux-arm64` | `apostate-152.0.7977.83-linux-arm64.tar.zst` | yes |
| `macos-arm64` | `apostate-152.0.7977.83-macos-arm64.zip` | yes |
| `windows-x64` | `apostate-152.0.7977.83-windows-x64.zip` | not yet |

All four are the contract. Three have built green on CI; no Windows build has
completed yet, so no `windows-x64.zip` exists. The packages' Windows acquisition
path is written to the same contract as the others and has never been run: the
`.zip` branch and the `chrome.exe` location are covered only by
synthetic-archive tests.

Based on Chromium 152.0.7977.83. There is no Intel macOS build, no 32-bit
Windows build and no mobile build.

## Build from source

Every input is pinned: Chromium revision, depot_tools revision, GN args,
container image digest and patch series. Same pins in, same binary out.
[docs/BUILD.md](docs/BUILD.md) has the steps.

## Documentation

| Page | What is in it |
| --- | --- |
| [docs/FLAGS.md](docs/FLAGS.md) | Every switch, its values, and precedence |
| [docs/LIMITATIONS.md](docs/LIMITATIONS.md) | What a page can still tell, and why |
| [docs/FONTS.md](docs/FONTS.md) | Installing a persona's fonts |
| [docs/PROFILE_SPEC.md](docs/PROFILE_SPEC.md) | Profile fields, launch configuration, package API |
| [docs/FINGERPRINTS.md](docs/FINGERPRINTS.md) | How a seed becomes a device |
| [docs/BUILD.md](docs/BUILD.md) | Building from source |
| [docs/METHODOLOGY.md](docs/METHODOLOGY.md) | The engineering rules the patches follow |

## Licence

GPL-3.0. See [LICENSE](LICENSE).
