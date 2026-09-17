# @heretic-hq/apostate

Node.js interface to the Apostate anti-detect Chromium build. A real browser
binary whose fingerprint is modified in C++ at the source level, driven through
the Playwright or Puppeteer API you already use.

Free and open source. No licence key, no account, no telemetry, no paid tier.

## Install

```sh
npm install @heretic-hq/apostate
npm install patchright   # or: playwright-core, or puppeteer-core
```

The browser is not bundled. On first use the package downloads the ~150 MB
archive for your platform from the GitHub release, checks its SHA-256 against a
manifest shipped inside the package, extracts it, and reuses it afterwards. No
driver needs to download a browser of its own — Apostate supplies it.

Supported hosts: `macos-arm64`, `linux-x64`, `linux-arm64`, `windows-x64`.

## Launch

`launch()` returns whatever driver you installed: a Playwright `Browser` or a
Puppeteer `Browser`. An existing script works with only the import changed.

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
| `fingerprintPlatform` | the build's own platform | `windows`, `macos` or `linux`. Needs that platform's fonts installed — see below. Cannot be combined with host inheritance. |
| `locale`, `timezone` | derived from the seed | Override just these. |
| `geoip` | `true` | Derive locale and timezone from the proxy's exit IP. |
| `proxy` | none | `http://`, `https://`, `socks5://`; credentials are kept out of the command line. |
| `headless` | `true` | |
| `userDataDir` | off-the-record | Persist cookies and storage. |
| `args` | none | Extra switches passed to the browser. |
| `driver` | first one installed | Force a specific driver by package name. |

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
