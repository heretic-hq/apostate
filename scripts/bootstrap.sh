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
    # Actually enforce the floor. macOS cannot be containerised, so the SDK is
    # the reproducibility boundary and building against whatever happens to be
    # installed is how a mac build silently stops matching the Linux one.
    floor="$(sed -n 's/^ *mac_sdk_min *= *"\(.*\)".*/\1/p' "$REPO_ROOT/build/args/macos-arm64.gn")"
    [ -n "$floor" ] || die "mac_sdk_min not found in build/args/macos-arm64.gn"
    lowest="$(printf '%s\n%s\n' "$floor" "$sdk" | sort -V | head -1)"
    [ "$lowest" = "$floor" ] || die "macOS SDK $sdk is below the pinned floor $floor"
    say "macOS SDK $sdk  (floor $floor, from build/args/macos-arm64.gn)"
    ;;
  Linux)
    command -v python3 >/dev/null || die "python3 missing"
    ;;
esac

avail_kb="$(df -Pk "$WORKSPACE" | awk 'NR==2{print $4}')"
[ "$avail_kb" -gt 209715200 ] || warn "under 200GB free at $WORKSPACE; a checkout plus build needs roughly that"

say "bootstrap ok  chromium=$CHROMIUM_VERSION  workspace=$WORKSPACE"
