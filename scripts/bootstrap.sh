#!/usr/bin/env bash
# Fetch the pinned depot_tools and assert the host can build. Idempotent.
source "$(dirname "$0")/lib.sh"

mkdir -p "$WORKSPACE"

if [ ! -d "$DEPOT_TOOLS/.git" ]; then
  say "cloning depot_tools"
  git clone -q https://chromium.googlesource.com/chromium/tools/depot_tools.git "$DEPOT_TOOLS"
fi

say "pinning depot_tools to $DEPOT_TOOLS_REVISION"
git -C "$DEPOT_TOOLS" fetch -q origin
git -C "$DEPOT_TOOLS" checkout -q --detach "$DEPOT_TOOLS_REVISION"

actual="$(git -C "$DEPOT_TOOLS" rev-parse HEAD)"
[ "$actual" = "$DEPOT_TOOLS_REVISION" ] || die "depot_tools is at $actual, expected $DEPOT_TOOLS_REVISION"

case "$(uname -s)" in
  Darwin)
    command -v xcodebuild >/dev/null || die "Xcode command line tools not installed"
    sdk="$(xcrun --sdk macosx --show-sdk-version 2>/dev/null || true)"
    [ -n "$sdk" ] || die "no macOS SDK found"
    say "macOS SDK $sdk  (pinned floor in build/args/macos-arm64.gn)"
    ;;
  Linux)
    command -v python3 >/dev/null || die "python3 missing"
    ;;
esac

avail_kb="$(df -Pk "$WORKSPACE" | awk 'NR==2{print $4}')"
[ "$avail_kb" -gt 209715200 ] || warn "under 200GB free at $WORKSPACE; a checkout plus build needs roughly that"

say "bootstrap ok  chromium=$CHROMIUM_VERSION  workspace=$WORKSPACE"
