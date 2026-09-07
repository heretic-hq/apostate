#!/usr/bin/env bash
# Write args.gn from the pinned target file and run gn gen.
source "$(dirname "$0")/lib.sh"

TARGET="${1:-$(target_default)}"
ARGS_FILE="$REPO_ROOT/build/args/$TARGET.gn"
[ -f "$ARGS_FILE" ] || die "no args for target '$TARGET' (build/args/$TARGET.gn)"
[ -d "$SRC" ] || die "no checkout; run scripts/fetch-sources.sh"

OUT="$SRC/out/$TARGET"
mkdir -p "$OUT"

# common.gni is imported as //apostate/common.gni, so expose build/args there.
ln -sfn "$REPO_ROOT/build/args" "$SRC/apostate"

cp "$ARGS_FILE" "$OUT/args.gn"
say "configuring $TARGET"
( cd "$SRC" && gn gen "out/$TARGET" )

# compile_commands.json is what makes the V1 single-file gate possible.
( cd "$SRC" && gn gen "out/$TARGET" --export-compile-commands >/dev/null )
say "configured  out/$TARGET  ($(wc -l < "$OUT/args.gn") args)"
