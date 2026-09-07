#!/usr/bin/env bash
# Apply patches/series in order.
#
# git apply matches context exactly and never fuzzes, which is the property we
# want: a patch that applied at an offset would have landed somewhere nobody
# verified, and here a patch in the wrong place is a wrong value shipped
# silently.
source "$(dirname "$0")/lib.sh"

SERIES="$REPO_ROOT/patches/series"
[ -d "$SRC" ] || die "no checkout; run scripts/fetch-sources.sh"
[ -f "$SERIES" ] || die "no patches/series"

say "resetting checkout to pristine $CHROMIUM_VERSION"
git -C "$SRC" checkout -q --detach "refs/tags/$CHROMIUM_VERSION"
git -C "$SRC" clean -qfd
git -C "$SRC" reset -q --hard

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
  git -C "$SRC" apply --whitespace=error "$patch_file" \
    || die "failed to apply $line — rebase the patch against $CHROMIUM_VERSION"
  count=$((count + 1))
done < "$SERIES"

say "applied $count patch(es)"
