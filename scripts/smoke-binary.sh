#!/usr/bin/env bash
# Prove that the binary a target just built exists and identifies itself.
#
# The per-target binary path is a property of the Chromium build, not of a
# workflow, so it lives here rather than being retyped in every CI job.
source "$(dirname "$0")/lib.sh"

TARGET="${1:-$(target_default)}"
OUT="$SRC/out/$TARGET"

case "$TARGET" in
  macos-arm64) binary="$OUT/Chromium.app/Contents/MacOS/Chromium" ;;
  linux-x64|linux-arm64) binary="$OUT/chrome" ;;
  windows-x64) binary="$OUT/chrome.exe" ;;
  *) die "unsupported target: $TARGET" ;;
esac

if [ "$TARGET" = windows-x64 ]; then
  # The executable bit is not meaningful on the Windows filesystem the runner
  # exposes through its POSIX layer.
  [ -f "$binary" ] || die "no built binary at $binary; run scripts/build.sh $TARGET"
else
  [ -x "$binary" ] || die "no built binary at $binary; run scripts/build.sh $TARGET"
fi

# linux-arm64 is an x86_64-hosted cross-build (see docs/BUILD.md), so the
# binary cannot be executed on the machine that produced it. Check the machine
# type of the ELF instead of asking it for its version.
if [ "$TARGET" = linux-arm64 ]; then
  command -v file >/dev/null || die "file(1) is required to check a cross-built binary"
  format="$(file -b "$binary")"
  case "$format" in
    *aarch64*|*ARM64*|*"ARM aarch64"*) say "cross-built ARM64 binary: $format" ;;
    *) die "unexpected ARM64 binary format: $format" ;;
  esac
else
  say "smoke $TARGET: $binary"
  "$binary" --version
fi
