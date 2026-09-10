#!/usr/bin/env bash
# Build and execute the real TTS controller's profile/provider regression tests.
source "$(dirname "$0")/lib.sh"

if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/test-speech-providers.sh "$@"
fi

TARGET="${1:-$(target_default)}"
OUT="$SRC/out/$TARGET"
[ -f "$OUT/args.gn" ] || die "not configured: $TARGET"
[ -x "$NINJA" ] || die "pinned ninja is missing"
JOBS="${APOSTATE_JOBS:-$(python3 -c 'import os; print(max(1, int(os.cpu_count() * 0.75)))')}"

say "building the TTS controller test target"
nice -n 10 "$NINJA" -j "$JOBS" -C "$OUT" content_unittests
say "running profile/provider routing tests"
cd "$OUT"
./content_unittests --gtest_filter='TtsControllerTest.Profile*' \
  --test-launcher-jobs=1 --test-launcher-retry-limit=0 \
  --ozone-platform=headless
