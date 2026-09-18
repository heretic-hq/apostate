#!/usr/bin/env bash
# Check that this host carries everything the build for <target> will reach for.
#
# Every tool here is used by a script that runs deep into a build measured in
# hours -- package-artifact.sh is the very last step of all -- so a missing one
# otherwise surfaces after the expensive work is already paid for. This runs in
# seconds instead.
#
# It reports EVERY gap rather than exiting at the first, and enumerates what it
# did find when something is missing. One cheap run on the smallest runner of a
# family therefore answers the whole question for that image; exiting early
# meant learning about one missing file per run.
#
# Usage: scripts/verify-host-tooling.sh <target>
set -uo pipefail

target="${1:-${APOSTATE_TARGET:-}}"
[ -n "$target" ] || { echo "usage: scripts/verify-host-tooling.sh <target>" >&2; exit 2; }

problems=()
note() { printf '  %s\n' "$*"; }
fail() { problems+=("$1"); }

have() { command -v "$1" >/dev/null 2>&1; }
need() {
  if have "$1"; then
    note "$1 present"
  else
    fail "$1 is missing; $2 needs it"
  fi
}

printf 'host tooling for %s (%s %s)\n' "$target" "$(uname -s)" "$(uname -m)"

need python3 scripts/build.sh
need git scripts/fetch-sources.sh

case "$target" in
  windows-*)
    # windows-x64 packages a .zip through 7z; the others write .tar.zst.
    # Probing the wrong format would fail a runner that is actually fine.
    need 7z scripts/package-artifact.sh

    # Two files Chromium's Windows build hard-requires that VS Build Tools
    # alone does not provide. The Blacksmith image is GitHub's windows-2025
    # minus the full Visual Studio IDE, so neither can be assumed from
    # GitHub's published inventory.
    sdk_root="${WINDOWSSDKDIR:-/c/Program Files (x86)/Windows Kits/10}"
    # build/vs_toolchain.py marks dbghelp.dll NOT optional and raises without
    # the SDK's "Debugging Tools for Windows" feature.
    if [ -f "$sdk_root/Debuggers/x64/dbghelp.dll" ]; then
      note "dbghelp.dll present"
    else
      fail "missing $sdk_root/Debuggers/x64/dbghelp.dll; install the Windows SDK feature 'Debugging Tools for Windows'"
      note "SDK root contents ($sdk_root):"
      if [ -d "$sdk_root" ]; then
        for entry in "$sdk_root"/*; do note "    $(basename "$entry")"; done
      else
        note "    the SDK root itself does not exist"
      fi
    fi

    # msdia140.dll comes from the VS install's DIA SDK, located through vswhere
    # so a Build Tools install is found as readily as a full IDE one.
    vswhere="/c/Program Files (x86)/Microsoft Visual Studio/Installer/vswhere.exe"
    if [ -f "$vswhere" ]; then
      vs_path="$("$vswhere" -latest -products '*' -property installationPath 2>/dev/null | tr -d '\r')"
      if [ -z "$vs_path" ]; then
        fail "vswhere reported no Visual Studio installation; vs_toolchain.py cannot locate a toolchain"
      else
        note "visual studio: $vs_path"
        vs_unix="$(cygpath -u "$vs_path" 2>/dev/null || printf '%s' "$vs_path")"
        if [ -f "$vs_unix/DIA SDK/bin/amd64/msdia140.dll" ]; then
          note "msdia140.dll present"
        else
          fail "missing $vs_unix/DIA SDK/bin/amd64/msdia140.dll; build/vs_toolchain.py copies it unconditionally"
        fi

        # Whether Chromium can actually FIND this install is asserted in
        # configure.sh, not here. This script runs before the checkout exists,
        # so build/vs_toolchain.py is not present to ask, and a guarded check
        # that finds no file would skip silently -- a check rendering as green
        # because it never ran.
      fi
    else
      fail "vswhere.exe not found at $vswhere; vs_toolchain.py cannot locate Visual Studio"
    fi
    ;;
  *)
    need tar scripts/package-artifact.sh
    probe="$(mktemp -d)"
    trap 'rm -rf "$probe"' EXIT
    : > "$probe/member"
    if tar --zstd -cf "$probe/probe.tar.zst" -C "$probe" member 2>/dev/null; then
      note "tar can write .tar.zst"
    else
      msg="tar here cannot write a .tar.zst archive, which scripts/package-artifact.sh requires"
      have zstd && msg="$msg (a standalone zstd binary IS present, but tar cannot use it)"
      fail "$msg"
    fi
    ;;
esac

case "$target" in
  linux-*)
    need docker scripts/in-linux-build-container.sh
    if have docker && docker info >/dev/null 2>&1; then
      note "docker daemon reachable"
    elif have docker; then
      fail "the docker daemon is unreachable; scripts/in-linux-build-container.sh cannot run"
    fi
    ;;
  macos-*)
    for tool in xcodebuild xcrun plutil; do
      need "$tool" scripts/prepare-mac-sdk.sh
    done
    ;;
esac

if [ "${#problems[@]}" -gt 0 ]; then
  printf '\n%s problem(s) on this host:\n' "${#problems[@]}" >&2
  for problem in "${problems[@]}"; do printf '  - %s\n' "$problem" >&2; done
  exit 1
fi
printf 'host tooling complete for %s\n' "$target"
