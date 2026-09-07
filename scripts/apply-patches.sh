#!/usr/bin/env bash
# Apply patches/series in order. Refuses on fuzz: a patch that applies at an
# offset has landed somewhere we did not verify, and in this project a patch in
# the wrong place is a wrong value shipped silently.
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
  git -C "$SRC" apply --whitespace=error --no-fuzz "$patch_file" \
    || die "failed to apply $line (no fuzz allowed — rebase the patch instead)"
  count=$((count + 1))
done < "$SERIES"

say "applied $count patch(es)"
