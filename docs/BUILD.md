# Build contract

Apostate builds Chromium from repository-pinned inputs. Reproducibility means
that independent clean builds of the same target produce the same output
hashes. `scripts/verify-reproducible.sh` performs that comparison; a successful
CI build alone does not establish it.

## Pinned inputs

| File | Input |
| --- | --- |
| `build/CHROMIUM_VERSION` | Exact Chromium tag, `152.0.7977.83` |
| `build/DEPOT_TOOLS_REVISION` | depot_tools commit SHA |
| `build/args/common.gni` | GN settings shared by every target |
| `build/args/linux-x64.gn` | Linux x64 GN settings |
| `build/args/linux-arm64.gn` | Linux arm64 GN settings |
| `build/args/macos-arm64.gn` | macOS arm64 GN settings |
| `build/args/windows-x64.gn` | Windows x64 GN settings |
| `build/MAC_SDK_VERSION` | Exact macOS SDK version, `26.5` |
| `build/MAC_SDK_BUILD` | SDK `ProductBuildVersion`, `25F70` |
| `build/WINDOWS_SDK_INSTALLER_URL` | Version-specific 10.0.26100 SDK installer, for the Debuggers feature |
| `build/WINDOWS_SDK_INSTALLER_VERSION` | Which SDK release that URL serves, `10.0.26100.4654` |
| `build/WINDOWS_SDK_INSTALLER_SHA256` | Digest of those installer bytes |
| `build/linux/Dockerfile` | Linux base image digest and build environment |
| `patches/series` | Patch names and application order |
| `build/MANIFEST.lock` | Generated record of resolved build inputs and outputs |

The patch bytes are inputs too. Editing a patch without changing its name or
its position in the series changes `patch_contents_sha256` in the build
manifest.

Chromium's pinned dependencies supply Clang, LLD, Rust and the Linux sysroots.
The optional Google API environment values read by `build/args/common.gni`
also affect the binary. Reproducibility comparisons must use the same values,
or leave them unset for both builds.

## Determinism requirements

- `scripts/lib.sh` exports `DEPOT_TOOLS_UPDATE=0` so depot_tools stays at the
  pinned revision.
- Linux targets use Chromium's bundled Clang and LLD with `use_sysroot=true`.
  The pinned container excludes host libraries from compilation.
- `build/args/common.gni` selects `is_official_build=true` and keeps PGO at
  Chromium's official-build default. The profiles come from pinned DEPS.
- The common GN settings disable debug symbols and dSYMs. Chromium supplies
  absolute-path stripping for these Linux and macOS configurations; the
  shared arguments do not set an undeclared override.
- macOS uses the exact SDK version and build named in the two pin files,
  rather than whichever SDK the active Xcode supplies.

## Build steps

The build scripts share workspace and tool paths through `scripts/lib.sh`.

| Script | Purpose |
| --- | --- |
| `scripts/resolve-build-targets.sh` | Resolve the CI target list and hosted runner labels |
| `scripts/verify-runner.sh` | Check the target against the runner OS and architecture |
| `scripts/verify-host-tooling.sh` | Report every tool or SDK component the target's build will need and this host lacks |
| `scripts/provision-windows-debuggers.sh` | Install the pinned SDK's Debugging Tools feature when the image lacks it |
| `scripts/bootstrap.sh` | Fetch pinned depot_tools and check host prerequisites |
| `scripts/fetch-sources.sh` | Fetch and sync the pinned Chromium revision |
| `scripts/apply-patches.sh` | Apply the patch series without fuzz |
| `scripts/run-chromium-hooks.sh` | Run Chromium hooks in the pinned Linux container |
| `scripts/prepare-linux-sysroot.sh` | Install the pinned Linux target sysroot |
| `scripts/prepare-mac-sdk.sh` | Resolve and link the pinned macOS SDK |
| `scripts/configure.sh` | Assemble target arguments and run GN |
| `scripts/build.sh` | Run Ninja and write the build manifest |
| `scripts/smoke-binary.sh` | Run the native binary's version command, or check the cross-built ARM64 ELF machine type |
| `scripts/package-artifact.sh` | Stage the runtime payload and write the archive and release manifest |
| `scripts/checkfile.sh` | Recompile one translation unit using generated compilation commands |
| `scripts/verify-reproducible.sh` | Compare clean-build output hashes |

On Linux, `scripts/fetch-sources.sh` treats a failed Chromium build-dependency
installation as fatal. A missing dependency otherwise tends to surface much
later as a header or linker error.

### Configure through the script

`scripts/configure.sh` combines `build/args/common.gni` with the target's GN
file into a self-contained generated `args.gn`. The common settings enable
proprietary codecs and Widevine registration and disable Chromium's field-trial
testing configuration. A target file alone does not contain those settings.
Configuring it by hand changes browser behavior and invalidates comparison
with the reference build.

The build manifest records the generated arguments' `args_sha256`. It covers
both common and target settings, plus the pinned SDK path on macOS.

### Pin the macOS SDK exactly

`build/MAC_SDK_VERSION` and `build/MAC_SDK_BUILD` require SDK `26.5`, build
`25F70`. The SDK is the macOS reproducibility boundary. Chromium supplies the
compiler, but the SDK determines the libraries and headers it links against.

Xcode 27.0's `libSystem.tbd` declares an `arm64e.x1-macos` target that the
bundled LLD cannot parse. LLD then loads no libSystem symbols, and linking
fails on `strlen`. A minimum SDK version cannot prevent this failure because
27.0 satisfies a 15.0 minimum. Do not raise the pin just to match the active
Xcode installation.

`scripts/prepare-mac-sdk.sh` searches the active developer directory, default
and versioned Xcode bundles, and the Command Line Tools SDKs. It checks both
the product version and build, then links the selected SDK into the generated
output directory. `scripts/configure.sh` writes a build-relative
`mac_sdk_path` so the checkout's absolute location does not enter the
arguments hash.

`scripts/bootstrap.sh` resolves the SDK before fetching depot_tools. A
different active Xcode SDK is acceptable when the pinned SDK is installed.
If the pin is missing, bootstrap fails and lists the searched locations and
installed SDKs. Install an Xcode or Command Line Tools package containing the
required SDK from <https://developer.apple.com/download/all/>. A deliberate
SDK update changes both pin files and requires an LLD compatibility check,
reproducibility verification and new reference measurements.

## Build manifest

`scripts/build.sh` writes `build/MANIFEST.lock` after a successful build. It
records the build target and timestamp, Chromium version and commit,
depot_tools revision, container image identity or `native`, patch-series and
patch-content hashes, generated GN-argument hash, build mode and output hashes.

`patch_series_sha256` hashes the series file. `patch_contents_sha256` hashes
each entry's name and complete patch bytes, in series order with NUL
separators. Any edit to any listed patch changes that digest, even if the
series file is unchanged.

The `release-gate` job in `.github/workflows/release.yml` runs
`scripts/validate-release-baseline.py` before scheduling any Chromium build.
It checks the committed manifest against the current version, patch series
and patch bytes. After changing a patch, rebuild locally with the build
scripts to regenerate `build/MANIFEST.lock` and include it with the source
change before tagging. A stale digest fails the gate immediately; the CI
build cannot refresh it because that build has not started yet.

### Fresh builds and local lineage

Both nightly and release CI builds set `APOSTATE_FRESH_BUILD=1` and start in
an empty hosted-job workspace. Local builds can retain output for incremental
compilation. Setting `APOSTATE_FRESH_BUILD=1` locally tells
`scripts/configure.sh` to delete the target output directory before generating
its configuration.

| Manifest field | Meaning |
| --- | --- |
| `build_mode` | `fresh` when `APOSTATE_FRESH_BUILD=1`, otherwise `incremental` |
| `parent_manifest_sha256` | Hash of the manifest replaced by this build, or `none` |
| `fresh_ancestor_sha256` | `self` for a fresh build; for an incremental build, the most recent recorded fresh manifest for this target, or `unknown` |

`scripts/build.sh` appends a sorted compact JSON record to the generated
`.apostate-build-lineage.jsonl` file in the workspace. The record contains
the timestamp, target, build mode, input and output hashes, and manifest
digest. Local history lasts as long as that workspace. In CI the file is
under `$RUNNER_TEMP/apostate-workspace`, lasts only for the job, and is not
uploaded. The parent manifest in a fresh CI checkout is the checked-in
baseline, not a previous hosted job's output.

An incremental build can report `fresh_ancestor_sha256=unknown` when the
workspace has no recorded fresh build for that target. Both CI paths record
`build_mode=fresh` and `fresh_ancestor_sha256=self`.

## CI workflows

`.github/workflows/build-target.yml` defines the reusable build job. It
initializes the workspace, checks host tooling, checks out the requested
revision, verifies build inputs and runner identity, resolves the artifact
name, bootstraps, fetches, applies patches, runs Linux hooks and sysroot
installation, configures, builds, checks, packages, optionally attests and
uploads.

The host-tooling check runs before checkout. Every target needs Python, Git
and tar, and must pass a real `tar --zstd` write probe. Linux also needs Docker
and a working daemon; macOS needs `xcodebuild`, `xcrun` and `plutil`. Testing
archive support early avoids completing a full Chromium build only to fail at
the final packaging step.

The two callers are `.github/workflows/build-nightly.yml` and
`.github/workflows/release.yml`:

| Setting | Nightly | Release |
| --- | --- | --- |
| Trigger | Daily at 03:17 UTC, or manual dispatch | A `v*` tag push, or manual dispatch of an existing version tag |
| Build mode | Fresh | Fresh |
| `attest` | `false` | `true` |
| Revision | Triggering ref | Semver tag |
| Actions artifact retention | 14 days | 7 days |
| Matrix `max-parallel` | `1` | `1` |
| Matrix `fail-fast` | `false` | `true` |
| Additional jobs | Resolve targets | Version-tag and baseline gate, target resolution, publication |

Each build job has a 360-minute timeout. Both callers use the workflow-level
`apostate-build` concurrency group with `cancel-in-progress: false`, so a
nightly and a release cannot overlap. Each matrix leg has its own VM and
could run concurrently. Serialization contains costs while the fresh-build
pipeline is unproven; it is not a shared-workspace requirement. Release
fail-fast cancels remaining legs after a failure. Nightly continues through
the selected targets one at a time.

Release builds create provenance with the pinned
`actions/attest-build-provenance` action, using the archive as its subject.
The reusable job and its callers grant `id-token: write` and
`attestations: write`; even the nightly caller grants them to satisfy the
reusable workflow's permission requirements. The nightly attestation step
is skipped. See [Release policy](RELEASE.md) for installer hash checks and
out-of-band provenance verification.

### Pull-request gates

`.github/workflows/check.yml` runs on manual dispatch and on pull requests
touching the paths listed in that workflow. Its hosted jobs need no Chromium
checkout. They validate the ledger, release baseline, patch headers, schemas,
profile catalogue and resolver, GeoIP, and Python and Node packages.

`scripts/validate-patch-headers.py` checks every patch's hunk counts against
its body. Incorrect counts can cause patch application to omit lines, so CI
runs the validator read-only. Use its `--fix` option locally when repairing a
patch, then rebuild to refresh the manifest's patch-content hash.

### Packaging

`scripts/package-artifact.sh` stages the runtime payload, writes the archive
and creates a sibling release manifest with the archive's SHA-256. The release
build then attests the archive before uploading it with the manifest.

Packaging fails if required runtime files are absent. Linux needs the browser,
crash handler, ICU data, resource packs, V8 snapshot and English locale pack.
Windows needs the executable, Chromium DLLs and the corresponding runtime
data. Optional graphics libraries are copied when present. On macOS the app
bundle contains the runtime payload. Every target also carries the repository
license, an optional notice, the generated build manifest and the profile
resources. The archive root derives from its filename and has no nightly or
release marker.

The artifact filename and version come from
`.github/release/artifact-policy.json`. The Chromium version must agree with
`build/CHROMIUM_VERSION`. Publication requires exactly one valid manifest
and hash-matching archive for each resolved build target, not every platform
recognized by the policy.

`catalogue_version` comes from `resources/profiles/catalogue.json`. The
publish job checks out the tag and requires the manifest to name the
catalogue at that revision. The catalogue has its own version sequence, so the
release policy defines this relationship instead of pinning a catalogue
integer.

## Hosted runners and workspaces

`scripts/resolve-build-targets.sh` selects these Blacksmith runners:

| Target | Runner label | Build environment |
| --- | --- | --- |
| `linux-x64` | `blacksmith-32vcpu-ubuntu-2404` | Pinned `linux/amd64` container on Linux x64 |
| `linux-arm64` | `blacksmith-32vcpu-ubuntu-2404` | ARM64 cross-build in the same container architecture |
| `macos-arm64` | `blacksmith-12vcpu-macos-latest` | Native macOS ARM64 with the pinned SDK |
| `windows-x64` | `blacksmith-32vcpu-windows-2025` | Native Windows x64 against the runner's own VS Build Tools and SDK |

Linux arm64 runs on x64 because the pinned `linux/amd64` container is the
reproducibility boundary. `scripts/in-linux-build-container.sh` explicitly
requires a Linux x86_64 host. Chromium's hermetic Clang and LLD plus the
pinned ARM64 sysroot produce the target binary; an ARM64 runner is not a
substitute for this host environment. `scripts/verify-runner.sh` checks both
Linux targets against `RUNNER_OS=Linux` and `RUNNER_ARCH=X64` before bootstrap.
The cross-built binary's smoke check inspects its ELF architecture rather
than executing it on x64.

`windows-x64` builds natively on `blacksmith-32vcpu-windows-2025`. It cannot
use the Linux container. `scripts/lib.sh` exports
`DEPOT_TOOLS_WIN_TOOLCHAIN=0` so `vs_toolchain.py` resolves Visual Studio and
the Windows SDK from the runner's own installation; `build/args/windows-x64.gn`
pins no toolchain path. Pinning `visual_studio_path` there would oblige it to
pin `visual_studio_version`, `windows_sdk_version` and `wdk_path` as well, and
would force `visual_studio_runtime_dirs` empty so the CRT redistributables
never reach the package.

That image excludes the full Visual Studio IDE and provides VS Build Tools
2022 instead. `build/vs_toolchain.py` searches `BuildTools` alongside
`Enterprise`, `Professional`, `Community`, `Preview` and `Insiders`, so
autodetection finds it — which is the reason no path is pinned. Two files the
Windows build hard-requires come from outside Build Tools, and the workflow's
host-tooling step checks both before fetching Chromium rather than failing
hours later:

- `<SDK>/Debuggers/x64/dbghelp.dll`, which `vs_toolchain.py` marks
  non-optional; it needs the Windows SDK feature "Debugging Tools for
  Windows".
- `<VS>/DIA SDK/bin/amd64/msdia140.dll`, copied unconditionally and located
  through `vswhere`.

Measured on the image: `dbghelp.dll` is absent, so the Debugging Tools feature
is not installed. `scripts/verify-host-tooling.sh` reports it in about a
minute, and `.github/workflows/probe-runners.yml` runs that check on the
smallest instance of each family — instance size changes compute, not image
contents or the storage figure, so the cheapest runner is equivalent evidence.

Measured on the image, `msdia140.dll` and the DIA SDK ARE present, and Build
Tools sits at `C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools`
— under `Program Files (x86)`, not `Program Files`. Only the Debuggers feature
is missing, which is why the toolchain paths are autodetected rather than
written down.

`scripts/provision-windows-debuggers.sh` installs that one feature, and both
the build and the probe run it. It is idempotent, so it costs nothing once the
image ships the feature. It pins the installer by URL and by digest:
`build/WINDOWS_SDK_INSTALLER_URL` is the version-specific 10.0.26100 link
rather than a "latest SDK" link, `build/WINDOWS_SDK_INSTALLER_VERSION` records
which release that serves, and `build/WINDOWS_SDK_INSTALLER_SHA256` is checked
before the installer runs, because a pinned URL only promises a name. Only
`OptionId.WindowsDesktopDebuggers` is requested.

Which SDK version the build uses is not decided by any pin of ours, and cannot
drift. `build/vs_toolchain.py` hardcodes `SDK_VERSION = '10.0.26100.0'` and
prints it verbatim as GN's `sdk_version`, with
`build/toolchain/win/setup_toolchain.py` holding a second copy as a
cross-check. There is no version autodetection to mislead, so an SDK
directory appearing alongside can never be selected over the intended one —
that pin travels with `build/CHROMIUM_VERSION`, which is why
`build/args/windows-x64.gn` names no version either. The script still logs the
`bin`, `Include` and `Lib` version directories before and after, because a
component of the pinned version *disappearing* is a real failure and the
listing is how it would be recognised.

Windows has 130 GB of storage at every instance size, the least of the four
targets. A complete `macos-arm64` build measures 66 GB — 49 GB checkout, 16 GB
output, 0.7 GB depot_tools — so 130 GB carries a full build with headroom.
`scripts/bootstrap.sh` reports free space on every run, and the workflow
reports disk again after the build even when it fails, so each target's real
consumption ends up in its log.

Each job sets `APOSTATE_WORKSPACE` to the generated directory
`$RUNNER_TEMP/apostate-workspace`. Chromium, depot_tools, caches, output and
lineage are local to that VM; the workflow restores no build cache and adopts
no existing output directory. Every nightly and release fetches and builds
from scratch. Outside CI, `scripts/lib.sh` defaults to a workspace beneath
the repository and accepts an `APOSTATE_WORKSPACE` override.

### Select the platform set

The repository variable `APOSTATE_BUILD_TARGETS` accepts a comma-separated
list such as `macos-arm64,linux-x64`. When unset or empty, it resolves to all
four targets. The resolver requires both a target GN file and a runner
mapping, so an unknown target fails during setup rather than entering a build
queue.

The nightly workflow's `targets` dispatch input overrides the repository
variable for one run. Release builds use the repository variable and publish
exactly that resolved platform set. A subset release does not need artifacts
for unselected platforms.

## Keep extensions enabled

The common GN arguments leave `enable_extensions` at Chromium's default.
The extensions layer supplies page-visible `chrome.app` behavior. Disabling
it to reduce binary size changes browser capabilities before a profile is
loaded; runtime profile settings cannot restore compiled-out features.
