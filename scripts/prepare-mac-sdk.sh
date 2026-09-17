#!/usr/bin/env bash
# Resolve the pinned macOS SDK and link it into the checkout at a stable path.
#
# macOS cannot be containerised, so the SDK is this project's reproducibility
# boundary. Xcode 27.0's SDK declares an `arm64e.x1-macos` target in libSystem.tbd
# that the bundled lld cannot parse, leaving libSystem symbols such as `strlen`
# undefined at link time.
#
# Chromium pins the exact SDK it ships against in build/config/mac/mac_sdk.gni
# (`mac_sdk_official_version`/`mac_sdk_official_build_version`). This script
# pins the same way, from build/MAC_SDK_VERSION and build/MAC_SDK_BUILD, and
# links the result under the output directory's sdk/xcode_links so args.gn
# can name it with a relative path that is identical on every machine.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

[ "$(uname -s)" = Darwin ] || die "prepare-mac-sdk.sh only runs on macOS"

version="$(tr -d '[:space:]' < "$REPO_ROOT/build/MAC_SDK_VERSION")"
build_version="$(tr -d '[:space:]' < "$REPO_ROOT/build/MAC_SDK_BUILD")"
[ -n "$version" ] || die "build/MAC_SDK_VERSION is empty"
[ -n "$build_version" ] || die "build/MAC_SDK_BUILD is empty"

# Two modes, one search. scripts/bootstrap.sh has to fail early when the
# pinned SDK is missing, but it runs before a checkout exists and so has
# nowhere to put a symlink; --resolve-only does the version and build
# resolution with no side effects and prints the absolute SDK path. Splitting
# the search into a second copy would let the two drift, which for a
# reproducibility boundary is the whole problem restated.
mode="link"
out_dir="${1:-}"
case "$out_dir" in
  --resolve-only) mode="resolve" ;;
  "") die "usage: prepare-mac-sdk.sh <out-dir> | --resolve-only" ;;
esac

if [ "$mode" = link ]; then
  [ -d "$SRC" ] || die "no checkout at $SRC; run scripts/fetch-sources.sh"
fi

# Include versioned Xcode bundles used on hosted runners as well as the active
# developer directory, default Xcode, and standalone Command Line Tools.
roots=(
  "$(xcode-select -p 2>/dev/null || true)/Platforms/MacOSX.platform/Developer/SDKs"
  "/Applications/Xcode.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs"
  "/Library/Developer/CommandLineTools/SDKs"
)
for root in /Applications/Xcode*.app/Contents/Developer/Platforms/MacOSX.platform/Developer/SDKs; do
  [ ! -d "$root" ] || roots+=("$root")
done
# Bytewise order also removes duplicate roots, including active Xcode.app.
sorted_roots="$(printf '%s\n' "${roots[@]}" | LC_ALL=C sort -u)"

sdk=""
while IFS= read -r root; do
  candidate="$root/MacOSX$version.sdk"
  [ -d "$candidate" ] || continue
  plist="$candidate/System/Library/CoreServices/SystemVersion.plist"
  actual_version="$(plutil -extract ProductVersion raw "$plist" 2>/dev/null || true)"
  actual_build="$(plutil -extract ProductBuildVersion raw "$plist" 2>/dev/null || true)"
  [ "$actual_version" = "$version" ] && [ "$actual_build" = "$build_version" ] || continue
  sdk="$candidate"
  break
done <<< "$sorted_roots"

if [ -z "$sdk" ]; then
  printf 'error: macOS SDK %s with ProductBuildVersion %s is not installed.\n' "$version" "$build_version" >&2
  printf '  Searched roots:\n' >&2
  while IFS= read -r root; do
    printf '    %s\n' "$root" >&2
  done <<< "$sorted_roots"
  printf '  Installed macOS SDKs:\n' >&2
  found=false
  while IFS= read -r root; do
    for candidate in "$root"/MacOSX*.sdk; do
      [ -d "$candidate" ] || continue
      plist="$candidate/System/Library/CoreServices/SystemVersion.plist"
      actual_version="$(plutil -extract ProductVersion raw "$plist" 2>/dev/null || true)"
      actual_build="$(plutil -extract ProductBuildVersion raw "$plist" 2>/dev/null || true)"
      printf '    %s: version=%s ProductBuildVersion=%s\n' \
        "$candidate" "${actual_version:-unknown}" "${actual_build:-unknown}" >&2
      found=true
    done
  done <<< "$sorted_roots"
  [ "$found" = true ] || printf '    none\n' >&2
  printf '  Install Xcode or Command Line Tools containing SDK %s, build %s, and re-run.\n' "$version" "$build_version" >&2
  printf '  Apple downloads: https://developer.apple.com/download/all/\n' >&2
  printf '  Refusing a different SDK. Verify bundled lld compatibility before changing both pins.\n' >&2
  exit 1
fi

if [ "$mode" = resolve ]; then
  printf '%s\n' "$sdk"
  exit 0
fi

# gn requires every file it reads to live inside the source tree or the output
# directory, and build/config/mac/BUILD.gn reads SDK headers (mach/exc.defs)
# directly. That is why Chromium's system-Xcode path symlinks the SDK into
# root_build_dir as sdk/xcode_links and passes a build-dir-relative -isysroot.
# The pinned SDK goes through the same door, so the value in args.gn contains
# no absolute path and is identical on every machine.

link_dir="$out_dir/sdk/xcode_links"
link="$link_dir/MacOSX$version.sdk"
mkdir -p "$link_dir"
if [ -L "$link" ] && [ "$(readlink "$link")" = "$sdk" ]; then
  say "macOS SDK $version ($build_version) already linked"
else
  rm -rf "$link"
  ln -s "$sdk" "$link"
  say "linked macOS SDK $version ($build_version) from $sdk"
fi

printf 'sdk/xcode_links/MacOSX%s.sdk\n' "$version"
