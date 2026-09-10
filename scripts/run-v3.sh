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
ANGLE_BACKEND="${APOSTATE_V3_ANGLE_BACKEND:-swiftshader}"
case "$ANGLE_BACKEND" in
  swiftshader) GPU_ARGS=(--use-gl=angle --use-angle=swiftshader --enable-unsafe-swiftshader) ;;
  default) GPU_ARGS=() ;;
  gl|gl-egl|gles|gles-egl|vulkan) GPU_ARGS=(--use-gl=angle "--use-angle=$ANGLE_BACKEND") ;;
  *) die "unsupported APOSTATE_V3_ANGLE_BACKEND: $ANGLE_BACKEND" ;;
esac

TARGET="${APOSTATE_TARGET:-$(target_default)}"
CHROME="$SRC/out/$TARGET/chrome"
PORT="${APOSTATE_V3_PORT:-8177}"
OUTDIR="$(mktemp -d /tmp/v3run.XXXXXX)"

[ -x "$CHROME" ] || die "no browser at $CHROME; run scripts/build.sh"
[ -f "$PROFILE" ] || die "no profile at $PROFILE"
[ -f "$REFERENCE" ] || die "no reference at $REFERENCE"
if [ -n "$FONTCONF" ]; then
  [ -f "$FONTCONF" ] || die "no fontconfig at $FONTCONF"
  FONTCONF="$(cd "$(dirname "$FONTCONF")" && pwd)/$(basename "$FONTCONF")"
fi

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

# Grant the collector origin access to display details. Chromium registers this
# as "window-placement" but stores preference keys with underscores:
# components/content_settings/core/browser/website_settings_info.cc:23-26
# at CHROMIUM_VERSION. No debugger attachment is needed.
mkdir -p "$USERDIR/Default"
cat > "$USERDIR/Default/Preferences" <<PREFS
{"profile":{"content_settings":{"exceptions":{"window_placement":{
  "http://127.0.0.1:$PORT,*":{"setting":1}}}}}}
PREFS

if [ -n "${APOSTATE_V3_WIDEVINE_DIR:-}" ]; then
  python3 "$REPO_ROOT/scripts/provision-widevine.py" \
    --source "$APOSTATE_V3_WIDEVINE_DIR" --user-data-dir "$USERDIR"
fi

say "launching browser (ANGLE backend: $ANGLE_BACKEND)"
# APOSTATE_V3_HEADFUL=1 runs under Xvfb for comparisons that require a headed
# browser. Display permissions are pre-granted in either launch mode.
if [ -n "${APOSTATE_V3_HEADFUL:-}" ]; then
  command -v xvfb-run >/dev/null || die "APOSTATE_V3_HEADFUL set but xvfb-run is missing"
  LAUNCH=(xvfb-run -a --server-args="-screen 0 ${APOSTATE_V3_SCREEN:-1920x1080x24}" "$CHROME")
  MODE=()
else
  LAUNCH=("$CHROME")
  MODE=(--headless=new)
fi

env ${FONTCONF:+FONTCONFIG_FILE="$FONTCONF"} \
  "${LAUNCH[@]}" "${MODE[@]}" --no-sandbox --disable-gpu-sandbox \
  --no-first-run --no-default-browser-check \
  --user-data-dir="$USERDIR" \
  --apostate-profile="$B64" \
  "${GPU_ARGS[@]}" \
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
