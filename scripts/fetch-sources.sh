#!/usr/bin/env bash
# Sync Chromium to the pinned version. Safe to re-run; resumes a partial sync.
source "$(dirname "$0")/lib.sh"

MODE="${1:-sync}"
case "$MODE" in sync|--fetch-only) ;; *) die "usage: fetch-sources.sh [--fetch-only]" ;; esac

[ -x "$DEPOT_TOOLS/gclient" ] || die "run scripts/bootstrap.sh first"
mkdir -p "$WORKSPACE"
cd "$WORKSPACE"

# Written directly rather than via `gclient config`, because the PGO profile is
# an opt-in custom_var and an official build cannot configure without it. The
# profile is pinned by the Chromium revision — its filename embeds the commit —
# so fetching it keeps the build deterministic rather than compromising it.
say "writing .gclient for chromium $CHROMIUM_VERSION"
cat > .gclient <<'GCLIENT'
solutions = [
  {
    "name": "src",
    "url": "https://chromium.googlesource.com/chromium/src.git",
    "managed": False,
    "custom_deps": {},
    "custom_vars": {
      "checkout_pgo_profiles": True,
    },
  },
]
GCLIENT

if [ ! -d "$SRC/.git" ]; then
  # Partial clone: skip blob history and fetch file contents on demand. A full
  # Chromium clone downloads decades of every file's history that no build ever
  # reads. Tags still resolve, so the pinned version checks out normally.
  say "cloning chromium (partial clone; large but far smaller than full history)"
  git clone -q --filter=blob:none \
    https://chromium.googlesource.com/chromium/src.git "$SRC"
fi

say "fetching pinned tag $CHROMIUM_VERSION"
git -C "$SRC" fetch -q --no-tags origin "refs/tags/$CHROMIUM_VERSION:refs/tags/$CHROMIUM_VERSION"
if [ "$MODE" = "--fetch-only" ]; then
  git -C "$SRC" rev-parse "refs/tags/$CHROMIUM_VERSION^{commit}"
  exit 0
fi
say "checking out $CHROMIUM_VERSION"
git -C "$SRC" checkout -q --detach "refs/tags/$CHROMIUM_VERSION"

# --with_branch_heads and --with_tags are required for a release tag to resolve.
say "gclient sync (pinned by DEPS at the tag; this is the slow step)"
gclient sync --with_branch_heads --with_tags --no-history --shallow -D

if [ "$(uname -s)" = "Linux" ] && [ -x "$SRC/build/install-build-deps.sh" ]; then
  say "installing chromium build dependencies"
  "$SRC/build/install-build-deps.sh" --no-prompt --no-chromeos-fonts || \
    warn "install-build-deps reported a problem; check before configuring"
fi

say "chromium at $(git -C "$SRC" describe --tags 2>/dev/null || git -C "$SRC" rev-parse --short HEAD)"
