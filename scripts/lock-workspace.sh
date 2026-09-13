#!/usr/bin/env bash
# Hold a persistent workspace lock for the lifetime of a workflow job.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
source "$REPO_ROOT/scripts/lib.sh"
op="${1:-}"
target="${2:-${TARGET:-}}"
[ -n "$target" ] || die "workspace target is required"
workspace="${APOSTATE_WORKSPACE:-}"
[ -n "$workspace" ] || die "APOSTATE_WORKSPACE is required"
mkdir -p "$workspace"
workspace="$(realpath "$workspace")"
case "$workspace" in
  "$REPO_ROOT"|"$REPO_ROOT"/*) die "persistent workspace must be outside checkout: $workspace" ;;
esac
lock="$workspace/.lock"
owner="$lock/owner"
case "$op" in
  acquire)
    if [ -e "$owner" ]; then
      pid="$(sed -n 's/^pid=//p' "$owner")"
      if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
        die "persistent workspace is locked by pid $pid: $workspace"
      fi
      rm -rf "$lock"
    fi
    mkdir "$lock"
    holder_pid="$(nohup bash -c 'trap "exit 0" TERM INT; while :; do sleep 30; done' >/dev/null 2>&1 & echo $!)"
    {
      printf 'pid=%s\n' "$holder_pid"
      printf 'target=%s\n' "$target"
      printf 'run=%s\n' "${GITHUB_RUN_ID:-manual}"
      printf 'started=%s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    } > "$owner"
    printf 'workspace lock acquired for %s\n' "$target"
    if [ -n "${GITHUB_ENV:-}" ]; then
      printf 'APOSTATE_WORKSPACE_LOCK_ACQUIRED=1\n' >> "$GITHUB_ENV"
    fi
    ;;
  release)
    if [ ! -e "$owner" ]; then
      printf 'workspace lock already released for %s\n' "$target"
      exit 0
    fi
    lock_target="$(sed -n 's/^target=//p' "$owner")"
    [ "$lock_target" = "$target" ] || die "workspace lock target mismatch: $lock_target vs $target"
    lock_run="$(sed -n 's/^run=//p' "$owner")"
    current_run="${GITHUB_RUN_ID:-manual}"
    [ "$lock_run" = "$current_run" ] || die "workspace lock owner mismatch: $lock_run vs $current_run"
    pid="$(sed -n 's/^pid=//p' "$owner")"
    if [ -n "$pid" ]; then kill "$pid" 2>/dev/null || true; fi
    rm -rf "$lock"
    printf 'workspace lock released for %s\n' "$target"
    ;;
  *)
    die "usage: lock-workspace.sh acquire|release [target]"
    ;;
esac
