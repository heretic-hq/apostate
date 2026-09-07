#!/usr/bin/env bash
# V1 gate: compile one translation unit against the configured build.
#
# This is what keeps builds out of the debugging loop. A patch is validated in
# seconds here, so entering a full build is an expectation of success rather
# than an experiment.
#
#   scripts/checkfile.sh third_party/blink/renderer/core/frame/navigator.cc
source "$(dirname "$0")/lib.sh"

FILE="${1:?usage: checkfile.sh <chromium-relative-path> [target]}"
TARGET="${2:-$(target_default)}"
CC_JSON="$SRC/out/$TARGET/compile_commands.json"

[ -f "$CC_JSON" ] || die "no compile_commands.json for $TARGET; run scripts/configure.sh $TARGET"

cmd="$(python3 - "$CC_JSON" "$FILE" <<'PY'
import json, sys, os
db, want = sys.argv[1], sys.argv[2]
for e in json.load(open(db)):
    if os.path.normpath(e["file"]).endswith(os.path.normpath(want)):
        print(e.get("command") or " ".join(e["arguments"]))
        break
PY
)"
[ -n "$cmd" ] || die "$FILE is not in compile_commands.json (not built for $TARGET, or path is wrong)"

say "compiling $FILE"
( cd "$SRC/out/$TARGET" && eval "$cmd" ) && say "OK  $FILE compiles"
