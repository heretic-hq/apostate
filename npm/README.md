# @heretic-hq/apostate

Node.js interface to the Apostate anti-detect Chromium build. A real browser
binary whose fingerprint is modified in C++ at the source level, driven through
the Playwright or Puppeteer API you already use.

Free and open source. No licence key, no account, no telemetry, no paid tier.

## Install

```sh
npm install @heretic-hq/apostate
npm install patchright        # recommended
```

Patchright is the default driver. `playwright`, `playwright-core`, `puppeteer`
and `puppeteer-core` all work and Apostate falls back to them in that order.

The division of labour is that the browser handles what a page can observe about
the browser, and the driver's remaining job is to avoid *creating* artifacts of
its own — main-world `addInitScript` and `exposeFunction` bindings,
`Runtime.addBinding`, evaluation-script names visible in stack traces, and its
automation argv. Patchright is the hardened fork of that family, which is why it
is the default.

Be aware of what has and has not been measured here. The classic sentinels
(`$cdc_`, `__webdriver_evaluate`, `__playwright` and eight others) were **absent
under every driver tested**, and the `window` key set was byte-identical between
a bare launch and a driven page — so the folklore checks are not what
distinguishes these drivers. Patchright's advantage over Playwright has not been
measured on this project, and the default reflects the fork's intent rather than
a result. Treat any claim otherwise, including ours, as unverified.

Two things about Puppeteer *are* measured, and are why it sits last in the order.
Stack traces from driver-evaluated code carry your **absolute filesystem path**
(`at pptr:evaluate;file%3A%2F%2F%2FUsers%2F...`), where Playwright's equivalent
names no driver, scheme or path. And `exposeFunction` installs
`puppeteer___yourName` alongside the name you asked for. Both are driver-side;
nothing in the browser can remove them. Choose Puppeteer knowing that.

```javascript
import { driverInfo } from "@heretic-hq/apostate";
console.log(await driverInfo());
```

Pass `driver: "puppeteer-core"` to force one, and read
`browser.apostateDriverName` to see what was used.

The browser is not bundled. On first use the package downloads the ~150 MB
archive for your platform from the GitHub release, checks its SHA-256 against a
manifest shipped inside the package, extracts it, and reuses it afterwards. No
driver needs to download a browser of its own — Apostate supplies it.

Supported hosts: `macos-arm64`, `linux-x64`, `linux-arm64`, `windows-x64`.

## Launch

`launch()` returns whatever driver you installed: a Playwright `Browser` or a
Puppeteer `Browser`. An existing script works with only the import changed.

Verified against the macos-arm64 build for Patchright, Playwright and
puppeteer-core, including `launchPersistentContext` and `launchContext`.

```javascript
import { launch } from "@heretic-hq/apostate";

const browser = await launch();
const page = await browser.newPage();
await page.goto("https://example.com");
console.log(await page.title());
await browser.close();
```

With no arguments the browser draws a fresh fingerprint seed and composes a
coherent identity — GPU, CPU count, memory, screen, fonts, locale and timezone
all agree with each other. Every launch is a different device.

### A stable identity

A fresh seed every launch means a site you revisit sees a different device each
time. Pass a seed to get the same one back:

```javascript
const browser = await launch({ fingerprint: 42 });
```

Same seed, same fingerprint, on every launch and on every machine. This is the
only thing that makes an identity persist; a persistent `userDataDir` keeps
cookies but does not pin the device.

Viewport geometry is handled for you: the drivers' default viewports report
impossible values (Playwright: `screen == inner == avail` with
`devicePixelRatio` flattened to 1; Puppeteer: an inner viewport *larger* than
its own window), so Apostate lets the real window size through and the composed
profile's geometry survives. Pass a viewport explicitly and yours wins.

One case Apostate cannot fix: `fingerprint: "host"` under `headless: true` has no
display to inherit, so headless Chrome reports its synthetic 800x600 with
`availHeight == height`. No real desktop looks like that. Use a seed — any
composed profile supplies coherent geometry, measured at screen 1710x1112
against avail 1710x1079 with `devicePixelRatio` 2 — or run headful.

### Other options

```javascript
const browser = await launch({
  fingerprint: 42,
  fingerprintPlatform: "windows",   // present as a Windows desktop
  proxy: "http://user:pass@host:8080",
  headless: false,
  args: ["--fingerprint-hardware-concurrency=8"],
});
```

| Option | Default | Meaning |
|---|---|---|
| `fingerprint` | a fresh random seed | Seed for the whole identity. `"host"` (also `"off"`, `"false"`, `"0"`, `"disable"`, `"disabled"`) inherits the real machine and composes nothing. |
| `fingerprintPlatform` | host's own OS on macOS and Windows; `"windows"` on Linux | `windows`, `macos` or `linux`. Selects the GPU cluster as well as the OS identity. Needs that platform's fonts installed — see below. Cannot be combined with host inheritance. |
| `locale`, `timezone` | derived from the seed | Override just these. |
| `geoip` | `true` | Derive locale and timezone from the proxy's exit IP. |
| `proxy` | none | `http://`, `https://`, `socks5://`; credentials are kept out of the command line. |
| `headless` | `true` | |
| `userDataDir` | off-the-record | Persist cookies and storage. |
| `args` | none | Extra switches passed to the browser. |
| `driver` | first one installed | Force a specific driver by package name. |

### Headless Linux servers

The usual deployment, and the one this is built for: a Linux server with no
graphics device. Nothing extra is needed and no GPU is required. `headless` is
already the default, and the claimed operating system rather than the host's
graphics stack selects the GPU identity, so a GPU-less server presents the
capability cluster and renderer string of the OS it claims.

**On a Linux host the default claimed OS is Windows, so install the Windows
fonts.** It is the one setup step here and the most common cause of a block: a
Windows persona missing Windows faces is measurable in text metrics. The
families and where to copy them from are in
[docs/FONTS.md](https://github.com/heretic-hq/apostate/blob/main/docs/FONTS.md).
Passing `fingerprintPlatform: "linux"` composes the host's own OS instead and
needs nothing installed. Windows-on-Linux is the default because it is the least
bad cross-OS pairing, not because it is free; `fingerprintPlatform: "macos"` on
a Linux host is the riskier one, at 184 core families.

For the most aggressive targets, the suites that score behaviour as well as the
fingerprint, run headed on a virtual display instead. Still no GPU: the display
is an X server with nothing behind it.

```sh
sudo apt install xvfb
Xvfb :99 -screen 0 1920x1080x24 &
export DISPLAY=:99
```

```javascript
const browser = await launch({
  fingerprint: 42,
  headless: false,
  proxy: "http://user:pass@residential-host:8080",
});
```

`DISPLAY` reaches the browser through the environment: `launch()` passes
`process.env` through to the driver, so exporting it before the script runs is
the whole of the wiring.

What a page can still tell on such a host is render timing and per-pixel output,
which come from the rasteriser rather than from the identity. The measured
numbers, the exact extension names a software backend does not currently serve,
and the status of the served GPU identity — new in this release, and on that
page's list of behaviours written but not yet run — are in
[docs/LIMITATIONS.md](https://github.com/heretic-hq/apostate/blob/main/docs/LIMITATIONS.md).

### Fonts for a cross-platform persona

Asking for a platform other than the one you are running on means that
platform's fonts have to be on the host. Apple and Microsoft fonts cannot be
redistributed, so you supply them; a persona without its fonts is a common
cause of blocks. Install them into the normal OS font directories (on Linux,
run `fc-cache -f` afterwards).
[docs/FONTS.md](https://github.com/heretic-hq/apostate/blob/main/docs/FONTS.md)
has the per-persona family lists, source directories and a verification
command.

The browser assumes you have done this and does not check. What it will not do
is claim a face that is absent: the font list a page sees is filtered down from
what the host actually has, never added to.

`launchProcess()` returns the raw child process instead, for when no driver is
installed and you only need the browser running.

## Command line

```sh
npx apostate install        # download, verify and extract
npx apostate path           # print the executable path
npx apostate info           # install and manifest state as JSON
npx apostate run -- --version
npx apostate clear          # delete the cache
```

## DRM (Widevine)

Chromium fetches the Widevine CDM from Google at runtime into the profile
directory, and it cannot be redistributed, so Apostate does not ship it. A
default `launch()` uses a throwaway profile, so there is no CDM and
`navigator.requestMediaKeySystemAccess("com.widevine.alpha", ...)` rejects with
`NotSupportedError` — which a site can read in a single call.

If you need DRM, or you want that call to answer the way a real browser does,
provision a CDM that is already on your machine:

```javascript
import { provisionWidevine } from "@heretic-hq/apostate";

await provisionWidevine({ source: "/path/to/WidevineCdm" });
```

The pip package can find one for you: `python -m apostate provision-drm --list`.
Both packages share one browser install, so provisioning from either serves both.

That copies it into the browser's preinstalled-component directory, where it
registers at startup for every profile including a throwaway one, with no
network and without writing into the profile. It survives
`apostate install --force` and a Chromium upgrade.

Nothing is redistributed: the CDM travels from Google to your machine exactly as
it does for Chrome, and this only moves a file already there. If no CDM is
found, run any Chromium-based browser with a persistent profile and play a DRM
video once, then re-run. Measured on macOS; Linux and Windows use the same
command but have not been verified.

One switch matters: `--disable-component-update` gates the whole of
Chromium's component registration, not just downloading. With it set, **no**
preinstalled component registers — measured offline, zero of them — so a
provisioned CDM is silently inert and the browser diverges from a real Chrome
across every preinstalled component at once. `launch()` removes it from the
driver's default arguments for you. If you drive the binary yourself, do not
pass it — Playwright passes it by default, so use
`ignore_default_args=["--disable-component-update"]` (Python) or
`ignoreDefaultArgs: ["--disable-component-update"]` (Node). Patchright and
Puppeteer do not pass it.

## The browser cache

The install lives under `~/Library/Caches/apostate` on macOS,
`$XDG_CACHE_HOME/apostate` (or `~/.cache/apostate`) on Linux, and
`%LOCALAPPDATA%\apostate\cache` on Windows, keyed by Chromium version and
platform. `APOSTATE_CACHE_DIR` overrides it. The pip package uses the same
layout, so both share one install.

| Variable | Effect |
|---|---|
| `APOSTATE_CACHE_DIR` | Where the browser is installed. |
| `APOSTATE_BINARY` | Use this executable and skip acquisition entirely. |
| `APOSTATE_DOWNLOAD_BASE_URL` | Fetch archives from a mirror. The digest still comes from the package manifest. |
| `APOSTATE_KEEP_ARCHIVE` | Keep the verified archive after extracting, for `gh attestation verify`. |

## Integrity

The archive's SHA-256 is checked against the manifest **before** the archive is
opened, and a mismatch aborts without extracting anything. That manifest ships
inside this package rather than being fetched alongside the download, because a
digest served from the same place as the bytes it describes proves nothing.

Releases also carry GitHub build-provenance attestations. There is no signing
key to hold or rotate; verification is an optional extra step:

```sh
gh attestation verify apostate-152.0.7977.83-macos-arm64.tar.zst --repo heretic-hq/apostate
```

That needs the archive, so run `npx apostate install --keep-archive` first, or
download it from the release page.

## Licence

GPL-3.0-or-later.
