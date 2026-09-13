#!/usr/bin/env bash
# Install and attest the pinned Chromium sysroot for a configured Linux target.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/prepare-linux-sysroot.sh "$@"
fi
source "$REPO_ROOT/scripts/lib.sh"

target="${1:-${TARGET:-$(target_default)}}"
case "$target" in
  linux-x64) arch=amd64; sysroot_dir=debian_bullseye_amd64-sysroot ;;
  linux-arm64) arch=arm64; sysroot_dir=debian_bullseye_arm64-sysroot ;;
  *) die "unsupported Linux sysroot target: $target" ;;
esac

sysroot_json="$SRC/build/linux/sysroot_scripts/sysroots.json"
sysroot="$SRC/build/linux/$sysroot_dir"
expected="$(python3 - "$sysroot_json" "$arch" <<'PY'
import json, sys
with open(sys.argv[1], encoding='utf-8') as stream:
    data = json.load(stream)
print(data['bullseye_' + sys.argv[2]]['Sha256Sum'])
PY
)"
say "installing pinned Chromium $arch sysroot"
python3 "$SRC/build/linux/sysroot_scripts/install-sysroot.py" --arch="$arch"
test -d "$sysroot" || die "Chromium did not install sysroot at $sysroot"

actual_tree="$(python3 - "$sysroot" <<'PY'
import hashlib, pathlib, sys
root = pathlib.Path(sys.argv[1])
h = hashlib.sha256()
for path in sorted(p for p in root.rglob('*') if p.is_file() and p.name != '.stamp'):
    h.update(str(path.relative_to(root)).encode() + b'\0')
    h.update(hashlib.sha256(path.read_bytes()).digest())
print(h.hexdigest())
PY
)"
receipt_dir="$WORKSPACE/sysroot-receipts"
mkdir -p "$receipt_dir"
python3 - "$receipt_dir/$target.json" "$target" "$arch" "$expected" "$actual_tree" "$sysroot" <<'PY'
import json, pathlib, sys
path, target, arch, expected, actual, sysroot = sys.argv[1:]
data = {'target': target, 'arch': arch, 'expected_tarball_sha256': expected,
        'extracted_tree_sha256': actual, 'sysroot': sysroot}
pathlib.Path(path).write_text(json.dumps(data, sort_keys=True, separators=(',', ':')) + '\n', encoding='utf-8')
PY
say "sysroot ready  target=$target expected_tarball_sha256=$expected extracted_tree_sha256=$actual_tree"
