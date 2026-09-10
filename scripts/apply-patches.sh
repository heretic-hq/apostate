#!/usr/bin/env bash
# Apply patches/series in order.
#
# git apply matches context exactly and never fuzzes, which is the property we
# want: a patch that applied at an offset would have landed somewhere nobody
# verified, and here a patch in the wrong place is a wrong value shipped
# silently.
source "$(dirname "$0")/lib.sh"

MODE="${1:-apply}"
case "$MODE" in apply|--reset-only) ;; *) die "usage: apply-patches.sh [--reset-only]" ;; esac

SERIES="$REPO_ROOT/patches/series"
[ -d "$SRC" ] || die "no checkout; run scripts/fetch-sources.sh"
[ -f "$SERIES" ] || die "no patches/series"

MTIME_SNAPSHOT="$(mktemp /tmp/apostate-patch-times.XXXXXX)"
trap 'rm -f "$MTIME_SNAPSHOT"' EXIT
python3 "$REPO_ROOT/scripts/preserve-patch-mtimes.py" snapshot --src "$SRC" \
  --patches "$REPO_ROOT/patches" --state "$MTIME_SNAPSHOT"

say "resetting checkout to pristine $CHROMIUM_VERSION"
git -C "$SRC" rev-parse --verify "refs/tags/$CHROMIUM_VERSION^{commit}" >/dev/null
git -C "$SRC" reset -q --hard
git -C "$SRC" checkout -q --detach "refs/tags/$CHROMIUM_VERSION"
git -C "$SRC" clean -qfd
git -C "$SRC" reset -q --hard

# DEPS-managed directories are their own git checkouts, so the reset above
# does not touch them. A patch that edits one — third_party/swiftshader, for
# instance — would survive a reset and then be applied a second time, leaving
# the tree in a state no series describes. Reset every sub-repo any patch in
# the series writes to.
while read -r sub; do
  [ -d "$SRC/$sub/.git" ] || continue
  git -C "$SRC/$sub" reset -q --hard
  git -C "$SRC/$sub" clean -qfd
done < <(grep -h "^+++ b/" "$REPO_ROOT"/patches/*.patch 2>/dev/null \
         | sed "s|^+++ b/||" | cut -d/ -f1-2 | sort -u)

if [ "$MODE" = "--reset-only" ]; then
  say "checkout reset; patches not applied"
  exit 0
fi

count=0
while IFS= read -r line; do
  line="${line%%#*}"; line="$(printf '%s' "$line" | xargs || true)"
  [ -n "$line" ] || continue
  patch_file="$REPO_ROOT/patches/$line"
  [ -f "$patch_file" ] || die "missing patch: $line"
  printf '  %s\n' "$line"
  # git apply requires exact context and has no fuzz mode at all, unlike
  # patch(1) — so strictness is the default rather than something to request.
  # An earlier version passed --no-fuzz, which git apply does not accept.
  # Some pinned vendor sources use CRLF. Treat its CR as a line ending while
  # retaining errors for actual trailing spaces/tabs and blank EOF lines.
  git -C "$SRC" -c core.whitespace=blank-at-eol,blank-at-eof,space-before-tab,cr-at-eol \
    apply --whitespace=error "$patch_file" \
    || die "failed to apply $line — rebase the patch against $CHROMIUM_VERSION"
  count=$((count + 1))
done < "$SERIES"

say "applied $count patch(es)"
python3 "$REPO_ROOT/scripts/preserve-patch-mtimes.py" restore --src "$SRC" \
  --state "$MTIME_SNAPSHOT"
