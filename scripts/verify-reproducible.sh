#!/usr/bin/env bash
# Build twice from clean and compare. This is the check that the build contract
# in docs/BUILD.md is actually being honoured rather than merely intended.
source "$(dirname "$0")/lib.sh"

TARGET="${1:-$(target_default)}"

run_once() {
  rm -rf "$SRC/out/$TARGET"
  "$REPO_ROOT/scripts/apply-patches.sh"
  "$REPO_ROOT/scripts/configure.sh" "$TARGET"
  "$REPO_ROOT/scripts/build.sh" "$TARGET" >/dev/null
  grep -A20 '\[outputs\]' "$REPO_ROOT/build/MANIFEST.lock" | grep -v '^\[outputs\]'
}

say "build 1 of 2"
first="$(run_once)"
say "build 2 of 2"
second="$(run_once)"

if [ "$first" = "$second" ]; then
  say "reproducible: both builds produced identical outputs"
  printf '%s\n' "$first"
else
  printf '\033[31mNOT REPRODUCIBLE\033[0m\n' >&2
  diff <(printf '%s\n' "$first") <(printf '%s\n' "$second") >&2 || true
  exit 1
fi
