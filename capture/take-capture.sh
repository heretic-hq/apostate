#!/usr/bin/env bash
# Take a reference capture on a rented GPU host and submit it to the probe.
#
# The host has no display and nobody is watching, so this runs a real headed
# browser on a virtual X display and lets the page drive itself through
# ?auto=1 -- the same code path a person's click takes. There is no CDP, no
# WebDriver and no injected script, because the receiver rejects any capture
# carrying an automation signal.
#
# Chrome is pinned. Stable has moved to 153 and our release is 152, and a
# capture on the wrong major is refused by conform.py for the rest of its
# life. Two captures have already been stranded that way.
#
# Usage:  ./take-capture.sh <label>
# Example: ./take-capture.sh rtx-4090-vast
set -euo pipefail

CHROME_VERSION="${CHROME_VERSION:-152.0.7977.82}"
PROBE="${PROBE:-https://probe.chaser.sh}"
LABEL="${1:-}"
WORK="${WORK:-$HOME/.apostate-capture}"
SCREEN="${SCREEN:-1920x1080x24}"

if [ -z "$LABEL" ]; then
  echo "usage: $0 <label>    e.g. $0 rtx-4090-vast" >&2
  exit 2
fi

say() { printf '==> %s\n' "$*"; }
die() { printf 'FAILED: %s\n' "$*" >&2; exit 1; }

mkdir -p "$WORK"
cd "$WORK"

# ---------------------------------------------------------------- dependencies
say "installing dependencies"
if command -v apt-get >/dev/null 2>&1; then
  export DEBIAN_FRONTEND=noninteractive
  SUDO=""; [ "$(id -u)" -ne 0 ] && SUDO="sudo"
  $SUDO apt-get update -qq
  # Chrome for Testing ships no dependencies; these are what it dlopens.
  $SUDO apt-get install -y -qq --no-install-recommends \
    xvfb unzip curl ca-certificates fonts-liberation \
    libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libcups2t64 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2t64 libatspi2.0-0t64 \
    >/dev/null 2>&1 || $SUDO apt-get install -y -qq --no-install-recommends \
    xvfb unzip curl ca-certificates fonts-liberation \
    libnss3 libnspr4 libatk1.0-0 libatk-bridge2.0-0 libcups2 \
    libdrm2 libxkbcommon0 libxcomposite1 libxdamage1 libxfixes3 libxrandr2 \
    libgbm1 libpango-1.0-0 libcairo2 libasound2 libatspi2.0-0 >/dev/null
else
  command -v Xvfb  >/dev/null || die "no apt-get and no Xvfb; install Xvfb and unzip first"
  command -v unzip >/dev/null || die "no apt-get and no unzip; install unzip first"
fi

# --------------------------------------------------------------- pinned chrome
CHROME="$WORK/chrome-linux64/chrome"
if [ ! -x "$CHROME" ]; then
  say "fetching Chrome $CHROME_VERSION (pinned; stable is 153 and would be refused)"
  URL="https://storage.googleapis.com/chrome-for-testing-public/${CHROME_VERSION}/linux64/chrome-linux64.zip"
  curl -fsSL --retry 3 -o chrome.zip "$URL" || die "could not fetch $URL"
  unzip -q -o chrome.zip && rm -f chrome.zip
fi
[ -x "$CHROME" ] || die "chrome not executable at $CHROME"

GOT="$("$CHROME" --version 2>/dev/null | grep -oE '[0-9]+(\.[0-9]+){3}' || true)"
[ "$GOT" = "$CHROME_VERSION" ] || die "expected Chrome $CHROME_VERSION, got '${GOT:-nothing}'"
say "Chrome $GOT"

# ------------------------------------------------------------------- gpu check
say "GPU as the host reports it"
if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,driver_version --format=csv,noheader | sed 's/^/    /'
else
  echo "    no nvidia-smi -- this will capture a software renderer, which is not a GPU reference" >&2
fi

# ------------------------------------------------------------------ virtual X
DISPLAY_NUM="${DISPLAY_NUM:-99}"
if ! xdpyinfo -display ":$DISPLAY_NUM" >/dev/null 2>&1; then
  say "starting Xvfb on :$DISPLAY_NUM at $SCREEN"
  Xvfb ":$DISPLAY_NUM" -screen 0 "$SCREEN" -nolisten tcp >"$WORK/xvfb.log" 2>&1 &
  XVFB_PID=$!
  trap 'kill "$XVFB_PID" 2>/dev/null || true' EXIT
  for _ in $(seq 1 50); do
    xdpyinfo -display ":$DISPLAY_NUM" >/dev/null 2>&1 && break
    sleep 0.2
  done
  xdpyinfo -display ":$DISPLAY_NUM" >/dev/null 2>&1 || die "Xvfb did not come up; see $WORK/xvfb.log"
fi
export DISPLAY=":$DISPLAY_NUM"

# ------------------------------------------------------------------ the run
# window-management is granted up front because screen.details refuses to
# prompt during a capture and nobody is here to click Allow.
PROFILE="$WORK/profile"
rm -rf "$PROFILE"; mkdir -p "$PROFILE/Default"
ORIGIN="$(printf '%s' "$PROBE" | sed -E 's#(https?://[^/]+).*#\1#')"
cat > "$PROFILE/Default/Preferences" <<PREFS
{"profile":{"content_settings":{"exceptions":{"window_placement":{"${ORIGIN},*":{"setting":1}}}}}}
PREFS

say "capturing as '$LABEL' against $PROBE"
# No --headless, no --remote-debugging-port, no --enable-automation: every one
# of those is a signal the receiver refuses. --no-sandbox is required as root
# in a container and is not page-visible. --log-net-log is browser-side only;
# the page cannot observe it, and it is how we confirm the submit landed
# without driving the page from outside.
NETLOG="$WORK/netlog.json"
rm -f "$NETLOG"
"$CHROME" \
  --user-data-dir="$PROFILE" \
  --no-sandbox \
  --no-first-run \
  --no-default-browser-check \
  --disable-dev-shm-usage \
  --log-net-log="$NETLOG" \
  --net-log-capture-mode=IncludeSensitive \
  --window-size="${SCREEN%x*}" \
  "$PROBE/?auto=1&label=$LABEL" \
  >"$WORK/chrome.log" 2>&1 &
CHROME_PID=$!

say "waiting for the page to finish and submit"
SUBMIT_STATUS=""
for _ in $(seq 1 180); do
  sleep 1
  if [ -f "$NETLOG" ] && grep -q '/submit' "$NETLOG" 2>/dev/null; then
    SUBMIT_STATUS="$(python3 - "$NETLOG" <<'PY' 2>/dev/null || true
import json,sys,re
raw=open(sys.argv[1],encoding='utf-8',errors='replace').read()
m=[x for x in re.findall(r'HTTP/[\d.]+ (\d{3})',raw)]
s=set()
for chunk in raw.split('"/submit"')[1:]:
    for c in re.findall(r'(?:status|:status)[^\d]{0,12}(\d{3})',chunk[:4000]): s.add(c)
print(','.join(sorted(s)) or ','.join(sorted(set(m))[-1:]))
PY
)"
    [ -n "$SUBMIT_STATUS" ] && break
  fi
  kill -0 "$CHROME_PID" 2>/dev/null || break
done
sleep 4
kill "$CHROME_PID" 2>/dev/null || true
wait "$CHROME_PID" 2>/dev/null || true

# ------------------------------------------------------------------- verdict
if [ -z "$SUBMIT_STATUS" ]; then
  die "the page never POSTed to /submit -- see $WORK/chrome.log and $NETLOG"
fi
case "$SUBMIT_STATUS" in
  *200*|*201*|*204*)
    say "submitted, $PROBE accepted it (HTTP $SUBMIT_STATUS)" ;;
  *422*)
    die "$PROBE REFUSED the capture (HTTP $SUBMIT_STATUS). That is the admission
     gate doing its job: something looked automated, headless, or insecure.
     The usual cause is a driver attached to the browser -- do not run this
     under Playwright, Puppeteer or WebDriver, which set navigator.webdriver
     and are refused by design. This script drives nothing; the page drives
     itself." ;;
  *)
    die "unexpected response from $PROBE (HTTP $SUBMIT_STATUS)" ;;
esac

say "now confirm the capture is worth keeping:"
echo "    - context.ua reports Chrome $CHROME_VERSION"
echo "    - context.automation_signals is empty and headed is true"
echo "    - webgl1/webgl2 report the real GPU, not SwiftShader"
echo "    - webgpu.value.adapters are objects, not null -- two of our four"
echo "      NVIDIA captures came back null and are useless for WebGPU"
echo "  netlog: $NETLOG    chrome log: $WORK/chrome.log"
