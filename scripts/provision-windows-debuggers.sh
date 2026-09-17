#!/usr/bin/env bash
# Install the Windows SDK's "Debugging Tools for Windows" feature, if absent.
#
# build/vs_toolchain.py treats <SDK>/Debuggers/x64/dbghelp.dll as mandatory and
# raises without it. The windows-2025 runner image carries the SDK but not that
# feature -- it is GitHub's image minus the full Visual Studio IDE, and the
# Debuggers appear to have gone with it. Measured on the image: python3, git,
# 7z, the DIA SDK and msdia140.dll are all present, Build Tools is at
# "C:\Program Files (x86)\Microsoft Visual Studio\2022\BuildTools", every other
# SDK directory exists, and only Debuggers is missing.
#
# The installer is pinned by URL AND by SHA-256, because a pinned URL only
# promises a name. build/WINDOWS_SDK_INSTALLER_URL is the version-specific
# 10.0.26100 link, not a "latest SDK" link -- the same link Chromium's own
# upstream neighbour (microsoft/WindowsAppSDK build/scripts/windows-sdk.ps1)
# uses for this SDK version. Only OptionId.WindowsDesktopDebuggers is
# requested, never Microsoft's "/features +".
#
# Which SDK version the build uses is NOT decided here and cannot drift:
# build/vs_toolchain.py hardcodes SDK_VERSION = '10.0.26100.0' and prints it
# verbatim as gn's sdk_version, with build/toolchain/win/setup_toolchain.py
# holding a second copy as a cross-check. There is no version autodetection to
# mislead, so an SDK directory appearing here can never be selected over the
# intended one. That pin travels with build/CHROMIUM_VERSION. This script still
# logs the version directories before and after, because a component of the
# pinned version DISAPPEARING is a real failure and the listing is how it would
# be recognised.
#
# Idempotent: with the feature already present this does nothing and exits 0,
# so an image that gains it later costs no download.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"

say() { printf '==> %s\n' "$*"; }
die() { printf 'error: %s\n' "$*" >&2; exit 1; }

case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*) ;;
  *) die "provision-windows-debuggers.sh only runs on Windows" ;;
esac

sdk_root="${WINDOWSSDKDIR:-/c/Program Files (x86)/Windows Kits/10}"
[ -d "$sdk_root" ] || die "no Windows SDK at $sdk_root"
dbghelp="$sdk_root/Debuggers/x64/dbghelp.dll"

if [ -f "$dbghelp" ]; then
  say "debugging tools already present at $sdk_root/Debuggers"
  exit 0
fi

pin() {
  local file="$REPO_ROOT/build/$1" value
  [ -f "$file" ] || die "missing build/$1"
  value="$(tr -d '[:space:]' < "$file")"
  [ -n "$value" ] || die "build/$1 is empty"
  printf '%s' "$value"
}
url="$(pin WINDOWS_SDK_INSTALLER_URL)"
version="$(pin WINDOWS_SDK_INSTALLER_VERSION)"
want_sha="$(pin WINDOWS_SDK_INSTALLER_SHA256)"

list_versions() {
  for dir in bin Include Lib; do
    printf '  %s/%s: %s\n' "$(basename "$sdk_root")" "$dir" \
      "$(ls "$sdk_root/$dir" 2>/dev/null | tr '\n' ' ' || true)"
  done
}
say "SDK version directories before install"
list_versions

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
installer="$work/winsdksetup.exe"

say "downloading pinned Windows SDK $version installer"
curl --fail --location --silent --show-error --retry 3 --output "$installer" "$url" ||
  die "could not download the pinned SDK installer from $url"
[ -s "$installer" ] || die "downloaded installer is empty"

got_sha="$(sha256sum "$installer" | cut -d' ' -f1)"
say "installer sha256 $got_sha"
[ "$got_sha" = "$want_sha" ] ||
  die "installer digest does not match build/WINDOWS_SDK_INSTALLER_SHA256
  expected $want_sha
  got      $got_sha
The pinned URL served different bytes. Verify the release before repinning."

say "installing only OptionId.WindowsDesktopDebuggers"
# MSYS rewrites arguments that look like paths, so /features would arrive as a
# Windows path and the installer would reject it.
set +e
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
  "$installer" /features OptionId.WindowsDesktopDebuggers /quiet /norestart
rc=$?
set -e
# 3010 is ERROR_SUCCESS_REBOOT_REQUIRED: installed, reboot pending. The files
# are in place, and the dbghelp.dll check below is the real success signal.
case "$rc" in
  0) ;;
  3010) say "installer reported a pending reboot (3010); files are in place" ;;
  *) die "winsdksetup.exe exited $rc" ;;
esac

say "SDK version directories after install"
list_versions

[ -f "$dbghelp" ] || die "install reported success but $dbghelp is still missing"
say "dbghelp.dll present at $sdk_root/Debuggers/x64"
