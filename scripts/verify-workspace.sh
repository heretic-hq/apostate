#!/usr/bin/env bash
# Validate and briefly lock a persistent per-target workspace after checkout.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$REPO_ROOT/scripts/lib.sh"
target="${1:-${TARGET:-}}"
[ -n "$target" ] || die "workspace target is required"
workspace="${APOSTATE_WORKSPACE:-}"
[ -n "$workspace" ] || die "APOSTATE_WORKSPACE is required"
mkdir -p "$workspace"
workspace="$(realpath "$workspace")"
case "$workspace" in
  "$REPO_ROOT"|"$REPO_ROOT"/*) die "persistent workspace must be outside checkout: $workspace" ;;
esac
marker="$workspace/.apostate-workspace"
expected="$(printf 'target=%s\nchromium=%s' "$target" "$CHROMIUM_VERSION")"
if [ -e "$marker" ] && [ "$(cat "$marker")" != "$expected" ]; then
  die "persistent workspace identity mismatch at $workspace"
fi
printf '%s\n' "$expected" > "$marker"
lock="$workspace/.lock"
if ! mkdir "$lock" 2>/dev/null; then
  die "persistent workspace is locked: $workspace"
fi
trap 'rmdir "$lock"' EXIT
printf 'workspace matches %s (%s)\n' "$target" "$workspace"
