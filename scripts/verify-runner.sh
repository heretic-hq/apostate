#!/usr/bin/env bash
set -euo pipefail

target="${1:-}"

# Both Linux targets build in the pinned linux/amd64 container, including the
# linux-arm64 cross-build. scripts/in-linux-build-container.sh requires an x64
# host; the container defines the Linux reproducibility boundary, which is why
# a native arm64 host is rejected rather than accepted as equivalent.
# windows-x64 builds natively against the Visual Studio and Windows SDK
# installations named in build/args/windows-x64.gn.
case "$target:${RUNNER_OS:-}:${RUNNER_ARCH:-}" in
  macos-arm64:macOS:ARM64|linux-x64:Linux:X64|linux-arm64:Linux:X64|windows-x64:Windows:X64)
    ;;
  *)
    printf 'error: runner does not match target %s (OS=%s ARCH=%s)\n' \
      "$target" "${RUNNER_OS:-unset}" "${RUNNER_ARCH:-unset}" >&2
    exit 1
    ;;
esac

printf 'runner matches %s (OS=%s ARCH=%s)\n' "$target" "$RUNNER_OS" "$RUNNER_ARCH"
