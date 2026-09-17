#!/usr/bin/env bash
# Apply the patch series to the pinned Chromium checkout, or verify that it
# already is. Works against the persistent workspace resolved by lib.sh.
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/lib.sh"

check_only=0
if [ "${1:-}" = "--check" ]; then
  check_only=1
fi

SERIES_FILE="$REPO_ROOT/patches/series"
[ -f "$SERIES_FILE" ] || die "missing patch series at $SERIES_FILE"

# The checkout must be a real git worktree before anything else touches it.
[ -d "$SRC" ] || die "no checkout at $SRC; run scripts/fetch-sources.sh"
git -C "$SRC" rev-parse --is-inside-work-tree >/dev/null 2>&1 \
  || die "$SRC is not a git worktree; run scripts/fetch-sources.sh"

# Patch paths are absolute (not ../../patches) because the workspace may live
# outside this repo, where a relative path from the checkout would not resolve.
patches=()
while read -r patch_line; do
  # Strip comments and whitespace
  patch_file="$(echo "$patch_line" | sed -e 's/[[:space:]]*#.*//' -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//')"
  [[ -z "$patch_file" ]] && continue
  patch_path="$REPO_ROOT/patches/$patch_file"
  [ -f "$patch_path" ] || die "missing patch file: $patch_path"
  patches+=("$patch_path")
done < "$SERIES_FILE"

[ "${#patches[@]}" -gt 0 ] || die "patch series is empty"

# Identity of the applied series: every patch name and its bytes. A reverse
# --check cannot answer "is this applied?" — reversing patch N is tested against
# a tree that patches N+1.. have since edited, so any two patches touching one
# file make it report "not applied" forever. That turned every run into a full
# reset and reapply, which rewrote mtimes and forced ninja to rebuild the world
# on an otherwise unchanged tree.
STAMP="$SRC/.apostate-patches"
series_digest() {
  python3 - "$REPO_ROOT" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
digest = hashlib.sha256()
for line in (root / "patches/series").read_text(encoding="utf-8").splitlines():
    name = line.partition("#")[0].strip()
    if name:
        digest.update(name.encode() + b"\0" + (root / "patches" / name).read_bytes() + b"\0")
print(digest.hexdigest())
PY
}
digest="$(series_digest)"

if [ -f "$STAMP" ] && [ "$(cat "$STAMP")" = "$digest" ]; then
  echo "patch series already applied"
  exit 0
fi

if [ "$check_only" = 1 ]; then
  # Verify-only: report the current tree's state without modifying it.
  if [ -e "$STAMP" ]; then
    echo "stale stamp — a real run will reset and reapply"
    exit 3
  fi
  if [ -z "$(git -C "$SRC" status --porcelain)" ]; then
    echo "pristine tree; a real run will apply the full series"
    exit 0
  fi
  echo "mixed/partial state — a real run will reset and reapply"
  exit 3
fi

# Every repository the series touches, not just the main one. DEPS checkouts
# such as third_party/swiftshader, angle, dawn and webrtc are independent git
# repositories nested inside the Chromium worktree: `git apply` writes into
# them by path, but the parent repository's checkout and clean cannot revert
# them. Resetting only $SRC therefore left sub-repository patches applied, the
# reverse-apply fast path disagreed with reality, and the series stopped at the
# first sub-repository patch with "patch does not apply" against its own
# already-applied output.
repo_roots() {
  local paths=() path dir root
  while IFS= read -r path; do
    paths+=("$path")
  done < <(sed -n 's|^+++ b/||p' "${patches[@]}" | sort -u)
  {
    printf '%s\n' "$SRC"
    for path in "${paths[@]}"; do
      dir="$SRC/$(dirname "$path")"
      [ -d "$dir" ] || continue
      root="$(git -C "$dir" rev-parse --show-toplevel 2>/dev/null || true)"
      [ -n "$root" ] && printf '%s\n' "$root"
    done
  } | sort -u
}

# Paths the series creates, so the reset can remove exactly those instead of
# every untracked file. `git clean -fd` deleted gclient-provisioned build
# tooling — buildtools/mac_arm64/gn and third_party/node's node binary are
# fetched by hooks, are untracked, and are not gitignored — which left the
# checkout unable to configure until the hooks were re-run.
created_paths() {
  python3 - "$@" <<'PY'
import pathlib, sys
paths = []
for patch in sys.argv[1:]:
    lines = pathlib.Path(patch).read_text(encoding="utf-8", errors="replace").splitlines()
    for i, line in enumerate(lines):
        if line.startswith("--- /dev/null") and i + 1 < len(lines) and lines[i + 1].startswith("+++ b/"):
            paths.append(lines[i + 1][6:])
for path in sorted(set(paths)):
    print(path)
PY
}

# Not fully applied: reset to pristine, then apply the whole series exactly.
rm -f "$STAMP"
say "resetting checkout to pristine"
while IFS= read -r root; do
  say "  reset $(basename "$root")"
  git -C "$root" checkout -- .
done < <(repo_roots)
while IFS= read -r path; do
  [ -n "$path" ] || continue
  rm -f "$SRC/$path"
  # Remove the directory only when the series created it and nothing else is
  # left in it; rmdir refuses otherwise, which is the check we want.
  rmdir "$SRC/$(dirname "$path")" 2>/dev/null || true
done < <(created_paths "${patches[@]}")

# The tag is pinned, so every patch must apply without fuzz. Each is applied in
# series order and checked immediately before it is applied, because a patch may
# legitimately depend on a file an earlier patch created.

say "applying patch series"
for patch_path in "${patches[@]}"; do
  echo "==> $(basename "$patch_path")"
  if ! git -C "$SRC" apply "$patch_path"; then
    die "failed to apply $(basename "$patch_path")"
  fi
done

printf '%s\n' "$digest" > "$STAMP"

say "applied ${#patches[@]} patches"
