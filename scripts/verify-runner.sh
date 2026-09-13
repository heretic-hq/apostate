#!/usr/bin/env bash
set -euo pipefail

target="${1:-}"
case "$target:${RUNNER_OS:-}:${RUNNER_ARCH:-}" in
  macos-arm64:macOS:ARM64|linux-arm64:Linux:ARM64|linux-x64:Linux:X64|windows-x64:Windows:X64)
    ;;
  *)
    printf 'error: runner does not match target %s (OS=%s ARCH=%s)\n' \
      "$target" "${RUNNER_OS:-unset}" "${RUNNER_ARCH:-unset}" >&2
    exit 1
    ;;
esac

printf 'runner matches %s (OS=%s ARCH=%s)\n' "$target" "$RUNNER_OS" "$RUNNER_ARCH"
