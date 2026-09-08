#!/usr/bin/env bash
# V3: launch the built browser with a profile, collect, diff against the
# reference the profile claims to be.
#
#   scripts/run-v3.sh PROFILE.json REFERENCE.json [FONTCONFIG_FILE]
#
# This exists because the V3 run was being assembled by hand each time, and a
# scoreboard you rebuild by hand is one where a changed flag looks like a
# changed patch. Everything the run depends on is an argument or a pin.
#
# Process handling note: every background process is killed by a PID captured
# at launch, never by pkill -f. A pattern broad enough to match the browser is
# also broad enough to match the shell script that contains it, and that has
# already killed this script's own shell once.
set -euo pipefail
source "$(dirname "$0")/lib.sh"

PROFILE="${1:?usage: run-v3.sh PROFILE.json REFERENCE.json [FONTCONFIG_FILE]}"
REFERENCE="${2:?usage: run-v3.sh PROFILE.json REFERENCE.json [FONTCONFIG_FILE]}"
FONTCONF="${3:-}"

TARGET="${APOSTATE_TARGET:-$(target_default)}"
CHROME="$SRC/out/$TARGET/chrome"
PORT="${APOSTATE_V3_PORT:-8177}"
OUTDIR="$(mktemp -d /tmp/v3run.XXXXXX)"

[ -x "$CHROME" ] || die "no browser at $CHROME; run scripts/build.sh"
[ -f "$PROFILE" ] || die "no profile at $PROFILE"
[ -f "$REFERENCE" ] || die "no reference at $REFERENCE"

python3 "$REPO_ROOT/scripts/validate-profile.py" "$PROFILE" >/dev/null \
  || die "profile fails V0; fix it before measuring V3"

say "serving collector on :$PORT"
python3 "$REPO_ROOT/capture/server/receive.py" --out "$OUTDIR" --port "$PORT" \
        --bind 127.0.0.1 --once > "$OUTDIR/server.log" 2>&1 &
SERVER_PID=$!
cleanup() { kill "$SERVER_PID" 2>/dev/null || true; kill "${BROWSER_PID:-}" 2>/dev/null || true; }
trap cleanup EXIT

for _ in $(seq 1 40); do
  curl -sf "http://127.0.0.1:$PORT/" -o /dev/null && break
  sleep 0.25
done

B64="$(base64 -w0 < "$PROFILE" 2>/dev/null || base64 < "$PROFILE" | tr -d '\n')"
USERDIR="$OUTDIR/userdata"

say "launching browser"
env ${FONTCONF:+FONTCONFIG_FILE="$FONTCONF"} \
  "$CHROME" --headless=new --no-sandbox --disable-gpu-sandbox \
  --no-first-run --no-default-browser-check \
  --user-data-dir="$USERDIR" \
  --apostate-profile="$B64" \
  --use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader \
  "http://127.0.0.1:$PORT/?auto=1&label=${APOSTATE_V3_LABEL:-v3}" \
  > "$OUTDIR/browser.log" 2>&1 &
BROWSER_PID=$!

say "waiting for the capture"
CAPTURE=""
for _ in $(seq 1 300); do
  CAPTURE="$(find "$OUTDIR" -maxdepth 1 -name '*.json' -print -quit 2>/dev/null || true)"
  [ -n "$CAPTURE" ] && break
  sleep 1
done
[ -n "$CAPTURE" ] || { tail -20 "$OUTDIR/browser.log" >&2; die "no capture arrived; see $OUTDIR"; }

kill "$BROWSER_PID" 2>/dev/null || true
say "captured $CAPTURE"

# Surface loader complaints. A profile field the loader rejected is a field the
# browser is not serving, and that reads as a patch defect at the diff.
if grep -q "apostate:" "$OUTDIR/browser.log"; then
  echo
  say "profile loader messages:"
  grep -o "apostate:.*" "$OUTDIR/browser.log" | sort -u | sed 's/^/  /'
fi

echo
python3 "$REPO_ROOT/capture/derive/conform.py" "$REFERENCE" "$CAPTURE" "${@:4}"
