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
  # Fetch only the pinned release tag. A single-branch shallow clone avoids
  # downloading unrelated Chromium refs and history before the build starts.
  say "cloning pinned chromium tag $CHROMIUM_VERSION (shallow single-branch)"
  git clone -q --depth 1 --single-branch --branch "$CHROMIUM_VERSION" \
    https://chromium.googlesource.com/chromium/src.git "$SRC"
fi

if [ "$(git -C "$SRC" rev-parse --is-shallow-repository 2>/dev/null || true)" != true ]; then
  die "Chromium checkout is not shallow; refusing an unbounded source fetch"
fi

say "checking out $CHROMIUM_VERSION"
git -C "$SRC" checkout -q --detach "refs/tags/$CHROMIUM_VERSION"

if [ "$MODE" = "--fetch-only" ]; then
  git -C "$SRC" rev-parse "refs/tags/$CHROMIUM_VERSION^{commit}"
  exit 0
fi
say "gclient sync (pinned by DEPS at the tag; no hooks; 16 jobs)"
gclient sync --with_branch_heads --with_tags --no-history --shallow --nohooks -j16 -D

if [ "$(uname -s)" = "Linux" ] && [ -x "$SRC/build/install-build-deps.sh" ]; then
  say "installing chromium build dependencies"
  "$SRC/build/install-build-deps.sh" --no-prompt --no-chromeos-fonts || \
    warn "install-build-deps reported a problem; check before configuring"
fi

say "chromium at $(git -C "$SRC" describe --tags 2>/dev/null || git -C "$SRC" rev-parse --short HEAD)"
