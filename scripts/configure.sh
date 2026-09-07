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
[ -x "$GN" ] || die "no gn at $GN; the checkout is incomplete"

say "configuring $TARGET with $("$GN" --version)"
# compile_commands.json in the same pass — it is what makes the V1 single-file
# gate possible, and a second gn gen would only regenerate the same ninja files.
( cd "$SRC" && "$GN" gen "out/$TARGET" --export-compile-commands )
say "configured  out/$TARGET  ($(wc -l < "$OUT/args.gn") args)"
