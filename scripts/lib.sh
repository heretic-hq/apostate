# Shared setup for every build script. Sourced, not executed.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

CHROMIUM_VERSION="$(tr -d '[:space:]' < "$REPO_ROOT/build/CHROMIUM_VERSION")"
DEPOT_TOOLS_REVISION="$(tr -d '[:space:]' < "$REPO_ROOT/build/DEPOT_TOOLS_REVISION")"

# Workspace holds the checkout and toolchain. Outside the repo: it is ~100GB of
# generated content and none of it is ours.
WORKSPACE="${APOSTATE_WORKSPACE:-$REPO_ROOT/.workspace}"
DEPOT_TOOLS="$WORKSPACE/depot_tools"
SRC="$WORKSPACE/src"
export GOCACHE="${GOCACHE:-$WORKSPACE/.go-cache}"

# GN actions that compile our data into the binary have to read this
# repository, and they run from inside the checkout. When the workspace sits
# under the repo they can walk up and find it; on a hosted runner the workspace
# is in $RUNNER_TEMP, outside the repo, and the walk fails the build. Naming
# the root here is what makes the workspace location a free choice.
export APOSTATE_DATA_ROOT="${APOSTATE_DATA_ROOT:-$REPO_ROOT}"

# build/rust/gni_impl/run_bindgen.py refuses to run when TARGET is set, so a
# single inherited variable with a very common name fails the build thousands
# of actions in. Chromium's own instruction is to remove it, so remove it.
unset TARGET

# depot_tools updates itself on every invocation unless told not to. That single
# behaviour is the most common cause of a build that worked yesterday.
export DEPOT_TOOLS_UPDATE=0
export DEPOT_TOOLS_METRICS=0

# Build against the Visual Studio and Windows SDK installed on this machine
# rather than Google's internal packaged toolchain, which is what
# vs_toolchain.py reaches for when this is unset and which we cannot fetch.
# With this set, `gn` resolves visual_studio_path, visual_studio_version,
# windows_sdk_path, windows_sdk_version, wdk_path AND
# visual_studio_runtime_dirs from the local install as one consistent set --
# pinning those paths by hand in build/args instead leaves runtime_dirs empty,
# so the CRT redistributables never get staged into the package.
export DEPOT_TOOLS_WIN_TOOLCHAIN=0

# Git for Windows checks text files out with CRLF by default. depot_tools'
# POSIX cipd bootstrap then parses cipd_client_version.digests with a regex
# anchored on end-of-line, no line matches with a trailing \r, and it reports
# "Platform windows-amd64 is not supported by the CIPD client bootstrap" --
# which is false, the hash is right there in the file. Set through the
# environment rather than `git config` so every child git inherits it,
# including the ones gclient runs, and the machine's own config is untouched.
# Appended at the next free index so an existing GIT_CONFIG_COUNT survives.
case "$(uname -s)" in
  MINGW*|MSYS*|CYGWIN*)
    _git_config_next="${GIT_CONFIG_COUNT:-0}"
    export "GIT_CONFIG_KEY_${_git_config_next}=core.autocrlf"
    export "GIT_CONFIG_VALUE_${_git_config_next}=false"
    export GIT_CONFIG_COUNT="$((_git_config_next + 1))"
    unset _git_config_next
    ;;
esac
export PATH="$DEPOT_TOOLS:$PATH"

# Prefer the build tools the checkout pins through DEPS over depot_tools'
# wrappers. Their versions are then fixed by CHROMIUM_VERSION rather than
# floating with whatever depot_tools revision happens to be present, which is
# what the build contract actually promises.
#
# The directory names are DEPS' names for the gn CIPD packages, not the host
# triples: src/DEPS installs gn/gn/mac-${arch} to src/buildtools/mac and
# gn/gn/windows-amd64 to src/buildtools/win. Guessing "mac_arm64" and "win64"
# pointed GN at paths that never exist, so scripts/configure.sh could not run
# on macOS at all and every build on this host had been configured by hand.
case "$(uname -s)" in
  Darwin) _bt_dir="mac" ;;
  MINGW*|MSYS*|CYGWIN*) _bt_dir="win" ;;
  *) _bt_dir="linux64" ;;
esac
GN="$SRC/buildtools/$_bt_dir/gn"
NINJA="$SRC/third_party/ninja/ninja"

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[33m warn:\033[0m %s\n' "$*" >&2; }
die()  { printf '\033[31merror:\033[0m %s\n' "$*" >&2; exit 1; }

target_default() {
  case "$(uname -s)-$(uname -m)" in
    Darwin-arm64) echo "macos-arm64" ;;
    Linux-x86_64) echo "linux-x64" ;;
    # Git Bash reports MINGW64_NT-10.0-26100. Windows became a build target
    # without this branch being added, so every script that resolves its
    # target from the host died there with "unsupported host" instead.
    MINGW*-x86_64|MSYS*-x86_64|CYGWIN*-x86_64) echo "windows-x64" ;;
    *) die "unsupported host $(uname -s)-$(uname -m)" ;;
  esac
}

# Path to the built browser for a target. Only Linux names the executable
# "chrome": macOS produces an app bundle and Windows a .exe, so every caller
# that hardcoded out/$TARGET/chrome silently did not work off Linux.
browser_binary() {
  local target="${1:-$(target_default)}"
  case "$target" in
    macos-arm64) printf '%s\n' "$SRC/out/$target/Chromium.app/Contents/MacOS/Chromium" ;;
    windows-x64) printf '%s\n' "$SRC/out/$target/chrome.exe" ;;
    *) printf '%s\n' "$SRC/out/$target/chrome" ;;
  esac
}

# Wait for a named script to finish on this host. Matches "bash <name>" rather
# than the bare name, because a watcher whose own command line contains the
# pattern matches itself and never returns.
wait_for_script() {
  local name="$1"
  while pgrep -f "bash .*${name}" | grep -qv "^$$\$"; do sleep 30; done
}
