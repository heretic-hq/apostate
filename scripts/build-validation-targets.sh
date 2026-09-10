#!/usr/bin/env bash
# Build named validation executables using the pinned toolchain and GN args.
source "$(dirname "$0")/lib.sh"

if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/build-validation-targets.sh "$@"
fi

TARGET="${1:?usage: build-validation-targets.sh <target> <ninja-target>...}"
shift
[ "$#" -gt 0 ] || die "provide at least one validation target"
OUT="$SRC/out/$TARGET"
[ -f "$OUT/args.gn" ] || die "not configured: $TARGET"
[ -x "$NINJA" ] || die "pinned ninja is missing"
for validation_target in "$@"; do
  [[ "$validation_target" =~ ^[a-zA-Z0-9_]+$ ]] || die "invalid validation target: $validation_target"
done
JOBS="${APOSTATE_JOBS:-$(python3 -c 'import os; print(max(1, int(os.cpu_count() * 0.75)))')}"
say "building validation targets for $TARGET with $JOBS jobs"
nice -n 10 "$NINJA" -j "$JOBS" -C "$OUT" "$@"
