#!/usr/bin/env bash
# Run Chromium's pinned hook set and verify generated build metadata.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/run-chromium-hooks.sh "$@"
fi
source "$REPO_ROOT/scripts/lib.sh"
target="${1:-${TARGET:-$(target_default)}}"
case "$target" in
  linux-x64|linux-arm64|macos-arm64|windows-x64) ;;
  *) die "unsupported target: $target" ;;
esac
cd "$WORKSPACE"
say "running pinned Chromium hooks for $target"
"$DEPOT_TOOLS/gclient" runhooks
[ -s "$SRC/build/util/LASTCHANGE" ] || die "Chromium hooks did not generate build/util/LASTCHANGE"
[ -s "$SRC/build/util/LASTCHANGE.committime" ] || die "Chromium hooks did not generate build/util/LASTCHANGE.committime"
[ -x "$GN" ] || die "pinned GN is missing or not executable: $GN"
say "Chromium hook outputs ready"
