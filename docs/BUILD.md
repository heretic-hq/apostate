# Build contract

The pipeline is reproducible and deterministic: same pins in, byte-identical
binary out, on any machine, with no manual steps.

This is a hard requirement rather than an aspiration. A build environment
configured outside the repository drifts, and in a fingerprinting project drift
produces failures that cannot be attributed — a surface regresses and there is
no way to tell whether the cause was a patch, a toolchain bump, or a host
difference. Every input is therefore pinned in-tree, and every step is a script.

## Pinned inputs

Everything that can change the output binary lives in `build/`:

| File | Pins |
|---|---|
| `build/CHROMIUM_VERSION` | Exact Chromium tag, e.g. `152.0.7935.61` |
| `build/DEPOT_TOOLS_REVISION` | depot_tools commit SHA |
| `build/args/<platform>.gn` | Complete GN args, one file per target |
| `build/linux/Dockerfile` | Base image pinned **by digest**, never by tag |
| `build/MANIFEST.lock` | Generated: resolved hashes of all of the above |

`patches/series` pins the fork itself. The tuple
`(chromium, depot_tools, args, image, series)` fully determines the output.

## Determinism requirements

These are the ones that actually bite; each is enforced by
`scripts/verify-reproducible.sh` or asserted at configure time.

1. **depot_tools must not self-update.** `DEPOT_TOOLS_UPDATE=0` is exported by
   every script. depot_tools updating itself mid-project is the single most
   common source of "it built yesterday".
2. **Hermetic toolchain only.** Chromium fetches its own clang, rustc and
   sysroot. The system compiler is never used; `use_sysroot=true` on Linux.
   Scripts assert that no system toolchain leaked into the configure step.
3. **Absolute paths must not enter the binary.**
   `strip_absolute_paths_from_debug_symbols` is on by default for Linux and
   Windows/lld builds; it is set explicitly regardless, so the checkout
   location cannot change the output.
4. **`is_official_build=true`.** Not only for size and speed: an unofficial
   build differs observably from real Chrome, which makes it wrong under axiom
   A2. Official builds also select `chrome_pgo_phase=2`, whose profiles are
   pinned through DEPS — so PGO stays deterministic as long as the checkout is
   pinned. Do not disable PGO to make builds faster; the resulting timing
   profile is itself a fingerprint surface.
5. **Containerised Linux builds.** The base image is pinned by digest. Host
   libraries never participate.
6. **macOS cannot be containerised.** The Xcode and SDK versions are pinned in
   `build/args/macos.gn` and asserted by `scripts/bootstrap.sh`, which fails
   loudly rather than building against whatever is installed.

## Steps

Each is idempotent and safe to re-run. Nothing here is ever done by hand; if a
step needs manual intervention, the script is wrong and gets fixed.

| Script | Does |
|---|---|
| `scripts/bootstrap.sh` | Fetch pinned depot_tools, assert host prerequisites |
| `scripts/fetch-sources.sh` | Sync Chromium to the pinned revision |
| `scripts/apply-patches.sh` | Apply `patches/series` in order; refuses on fuzz |
| `scripts/configure.sh` | Write `args.gn` from `build/args/`, run `gn gen` |
| `scripts/build.sh` | `autoninja`; emits `build/MANIFEST.lock` and the output hash |
| `scripts/checkfile.sh` | Compile a single translation unit — the V1 gate |
| `scripts/verify-reproducible.sh` | Build twice from clean, compare hashes |

`scripts/checkfile.sh` is what keeps builds out of the debugging loop. It uses
`compile_commands.json` from the configured output directory to rebuild one
file, so a patch is validated in seconds rather than hours. It requires one
completed build to exist first — that bootstrap build is the only unavoidably
slow step in the project.

## Build manifest

Every build writes `build/MANIFEST.lock` recording the resolved Chromium
revision, depot_tools revision, patch series hash, `args.gn` hash, toolchain
hashes, and the SHA-256 of each output binary. A release is only publishable
when an independent rebuild from the same pins reproduces every hash.

## Hosts

Linux x64 builds run in the pinned container, on the remote build machine.
macOS builds run natively on pinned Xcode. Both produce a manifest; both are
verified by rebuild.
