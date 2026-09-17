#!/usr/bin/env bash
# Install the Windows SDK's "Debugging Tools for Windows" feature, if absent.
#
# build/vs_toolchain.py treats <SDK>/Debuggers/x64/dbghelp.dll as mandatory and
# raises without it. The windows-2025 runner image carries the SDK but not that
# feature -- it is GitHub's image minus the full Visual Studio IDE, and the
# Debuggers appear to have gone with it. Measured on the image: every other SDK
# directory is present and only Debuggers is missing.
#
# Two rules make this safe to run inside a build:
#
#   1. The installer is PINNED to the 10.0.26100 release, in
#      build/WINDOWS_SDK_INSTALLER_URL. A "latest SDK" link would install a
#      second SDK version under Windows Kits\10, and vs_toolchain.py
#      autodetection may then prefer it over win_sdk_version -- the same silent
#      input drift the macOS SDK pin exists to prevent, arriving by a different
#      door.
#   2. It asserts afterwards that dbghelp.dll exists AND that no SDK version
#      directory appeared that was not there before. Installing the feature
#      must add Debuggers and nothing else.
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

url="$(tr -d '[:space:]' < "$REPO_ROOT/build/WINDOWS_SDK_INSTALLER_URL")"
version="$(tr -d '[:space:]' < "$REPO_ROOT/build/WINDOWS_SDK_INSTALLER_VERSION")"
[ -n "$url" ] || die "build/WINDOWS_SDK_INSTALLER_URL is empty"
[ -n "$version" ] || die "build/WINDOWS_SDK_INSTALLER_VERSION is empty"

# Recorded before the install so the assertion below can name exactly what
# appeared. These are directories like 10.0.26100.0.
before="$(ls "$sdk_root/bin" 2>/dev/null | sort || true)"

work="$(mktemp -d)"
trap 'rm -rf "$work"' EXIT
installer="$work/winsdksetup.exe"

say "downloading pinned Windows SDK $version installer"
curl --fail --location --silent --show-error --retry 3 --output "$installer" "$url" ||
  die "could not download the pinned SDK installer from $url"
[ -s "$installer" ] || die "downloaded installer is empty"

say "installing only OptionId.WindowsDesktopDebuggers"
# MSYS rewrites arguments that look like paths, so /features would become a
# Windows path and the installer would reject it.
MSYS_NO_PATHCONV=1 MSYS2_ARG_CONV_EXCL='*' \
  "$installer" /features OptionId.WindowsDesktopDebuggers /quiet /norestart ||
  die "winsdksetup.exe failed"

[ -f "$dbghelp" ] || die "install reported success but $dbghelp is still missing"
say "dbghelp.dll present"

after="$(ls "$sdk_root/bin" 2>/dev/null | sort || true)"
added="$(comm -13 <(printf '%s\n' "$before") <(printf '%s\n' "$after") || true)"
if [ -n "$added" ]; then
  printf 'error: the install added SDK version directories under %s/bin:\n' "$sdk_root" >&2
  printf '  %s\n' $added >&2
  printf 'Only the Debuggers feature was requested. A new SDK version here can\n' >&2
  printf 'be selected by vs_toolchain.py over the pinned win_sdk_version.\n' >&2
  exit 1
fi
say "no SDK version directory was added; the pin is intact"
