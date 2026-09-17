#!/usr/bin/env bash
# Fetch the pinned depot_tools and assert the host can build. Idempotent.
source "$(dirname "$0")/lib.sh"

case "$(uname -s)" in
  Darwin)
    command -v xcodebuild >/dev/null || die "Xcode command line tools not installed"
    command -v plutil >/dev/null || die "plutil is required to verify an SDK build version"
    # Resolve the exact SDK version/build before creating the workspace or
    # fetching depot_tools. --resolve-only reports installed SDKs on failure
    # and never writes the checkout symlink.
    mac_sdk_version="$(tr -d '[:space:]' < "$REPO_ROOT/build/MAC_SDK_VERSION")"
    mac_sdk_build="$(tr -d '[:space:]' < "$REPO_ROOT/build/MAC_SDK_BUILD")"
    mac_sdk="$(bash "$REPO_ROOT/scripts/prepare-mac-sdk.sh" --resolve-only)"
    active="$(xcrun --sdk macosx --show-sdk-version 2>/dev/null || true)"
    say "macOS SDK $mac_sdk_version ($mac_sdk_build) at $mac_sdk"
    if [ "$active" != "$mac_sdk_version" ]; then
      # Not a failure. The active Xcode's own SDK is irrelevant once
      # mac_sdk_path is pinned, and on a host upgraded past the pin the
      # pinned SDK legitimately lives only in the Command Line Tools copy.
      say "active Xcode ships SDK $active; the build uses the pinned $mac_sdk_version regardless"
    fi
    ;;
  Linux)
    command -v python3 >/dev/null || die "python3 missing"
    ;;
esac

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

# Measured on a complete macos-arm64 build: 66GB total -- 49GB checkout, 16GB
# output, 0.7GB depot_tools. The floor is 100GB so a target that needs more
# than macOS still has room; the old 200GB figure was roughly three times
# actual consumption, so it fired on the one runner with the least headroom
# and nowhere else, which is how a warning gets ignored. The number is
# reported unconditionally so each target's real consumption is in its log.
avail_kb="$(df -Pk "$WORKSPACE" | awk 'NR==2{print $4}')"
say "workspace has $((avail_kb / 1048576))GB free at $WORKSPACE"
[ "$avail_kb" -gt 104857600 ] || warn "under 100GB free; a measured checkout plus build needs 66GB on macOS"

say "bootstrap ok  chromium=$CHROMIUM_VERSION  workspace=$WORKSPACE"
