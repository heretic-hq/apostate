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
target="${APOSTATE_TARGET:-$(target_default)}"
case "$target" in
  linux-arm64)
    printf 'target_os = ["linux"]\ntarget_cpu = ["x64", "arm64"]\n' >> .gclient
    ;;
  linux-x64)
    printf 'target_os = ["linux"]\ntarget_cpu = ["x64"]\n' >> .gclient
    ;;
  macos-arm64)
    printf 'target_os = ["mac"]\ntarget_cpu = ["arm64"]\n' >> .gclient
    ;;
  windows-x64)
    printf 'target_os = ["win"]\ntarget_cpu = ["x64"]\n' >> .gclient
    ;;
  *)
    die "unsupported target: $target"
    ;;
esac

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
bash "$REPO_ROOT/scripts/run-chromium-hooks.sh" "$target"

if [ "$(uname -s)" = "Linux" ] && [ -x "$SRC/build/install-build-deps.sh" ]; then
  say "installing chromium build dependencies"
  # Hard failure, not a warning. A missing host dependency does not go away:
  # it reappears an hour into the compile as an unresolved header or a link
  # error with no connection to its cause. Failing here costs a minute.
  "$SRC/build/install-build-deps.sh" --no-prompt --no-chromeos-fonts ||
    die "install-build-deps.sh failed; the build cannot succeed without it"
fi

say "chromium at $(git -C "$SRC" describe --tags 2>/dev/null || git -C "$SRC" rev-parse --short HEAD)"

# The checkout exists now, so the real remaining space is knowable, which it
# was not at bootstrap. A complete macos-arm64 build's output measured 16GB
# with symbol_level=0; 20GB is that plus margin. Below it, ninja will run out
# of disk somewhere in 57128 actions, hours in and with an error naming a
# random object file rather than the cause.
#
# df only. Measuring the checkout with du costs 23 seconds on a warm APFS tree
# and considerably more on NTFS, on every build of every target, to print a
# number the workflow's post-build disk report already covers.
avail_kb="$(df -Pk "$WORKSPACE" | awk 'NR==2{print $4}')"
say "$((avail_kb / 1048576))GB free for build output"
[ "$avail_kb" -gt 20971520 ] ||
  die "only $((avail_kb / 1048576))GB free after the checkout; the build output measures 16GB. This build cannot finish, so it is not started."
