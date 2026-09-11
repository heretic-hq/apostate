#!/usr/bin/env bash
# Compile frozen fast05 and its CXX consumers after the experimental patch batch
# has been applied and scripts/configure.sh has regenerated the target.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/check-audio-fft-candidate.sh "$@"
fi
candidate_manifest="${1:?usage: check-audio-fft-candidate.sh <fast05-candidate-manifest.json> [target]}"
audio_target="${2:-linux-x64}"
audio_out="$SRC/out/$audio_target"
python3 - "$SRC" "$candidate_manifest" <<'PY'
import hashlib, json, pathlib, sys
src, manifest = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2])
data = json.loads(manifest.read_text())
expected = 'c94799b3993654fe2186471b22e73f2af8fb736ad9458d5abb0104cc45d98081'
if data['candidate_patch_sha256'] != expected:
    raise SystemExit('manifest is not the frozen fast05 experiment')
for relative, record in data['files'].items():
    if hashlib.sha256((src / relative).read_bytes()).hexdigest() != record['after_sha256']:
        raise SystemExit('candidate source mismatch: ' + relative)
print('experimental fast05 source hashes verified')
PY
# Explicit crate targets are needed: compiling fft_frame.cc alone can generate
# its CXX header without compiling the changed Rust implementation.
nice -n 10 "$NINJA" -C "$audio_out" -j 2 \
  third_party/rust/rustfft/v6:lib \
  third_party/blink/renderer/platform:rustfft_ffi \
  third_party/blink/renderer/platform:rustfft_ffi_cxx_generated \
  third_party/blink/renderer/platform:rustfft_ffi_unittests \
  obj/third_party/blink/renderer/platform/platform/fft_frame.o
printf 'experimental fast05 V1 targets compiled; runtime conformance remains unverified\n'
