#!/usr/bin/env bash
# Build twice from clean and compare. This is the check that the build contract
# in docs/BUILD.md is actually being honoured rather than merely intended.
source "$(dirname "$0")/lib.sh"

TARGET="${1:-$(target_default)}"

# --against-manifest compares one fresh build against the committed
# build/MANIFEST.lock instead of building twice. Same guarantee for half the
# compute whenever a recorded baseline already exists, which after the first
# build it always does.
AGAINST_MANIFEST=0
[ "${2:-}" = "--against-manifest" ] && AGAINST_MANIFEST=1

manifest_outputs() {
  grep -A20 '\[outputs\]' "$1" | grep -v '^\[outputs\]' | grep -v '^$'
}

run_once() {
  rm -rf "$SRC/out/$TARGET"
  "$REPO_ROOT/scripts/apply-patches.sh"
  "$REPO_ROOT/scripts/configure.sh" "$TARGET"
  "$REPO_ROOT/scripts/build.sh" "$TARGET" >/dev/null
  grep -A20 '\[outputs\]' "$REPO_ROOT/build/MANIFEST.lock" | grep -v '^\[outputs\]'
}

if [ "$AGAINST_MANIFEST" = "1" ]; then
  [ -f "$REPO_ROOT/build/MANIFEST.lock" ] || die "no build/MANIFEST.lock to compare against"
  first="$(manifest_outputs "$REPO_ROOT/build/MANIFEST.lock")"
  say "recorded baseline:"
  printf '%s\n' "$first"
  say "rebuilding from clean to compare"
  second="$(run_once)"
else
  say "build 1 of 2"
  first="$(run_once)"
  say "build 2 of 2"
  second="$(run_once)"
fi

if [ "$first" = "$second" ]; then
  say "reproducible: both builds produced identical outputs"
  printf '%s\n' "$first"
else
  printf '\033[31mNOT REPRODUCIBLE\033[0m\n' >&2
  diff <(printf '%s\n' "$first") <(printf '%s\n' "$second") >&2 || true
  exit 1
fi
