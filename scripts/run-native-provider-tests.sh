#!/usr/bin/env bash
# Run exact native Ozone/SwiftShader gates after their targets have been built.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
[[ "$(uname -s)" == Linux ]] || die 'native provider gates require the pinned Linux container'
if [[ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/run-native-provider-tests.sh "$@"
fi
provider_test_out="${1:?usage: run-native-provider-tests.sh <fresh-receipt-directory> [target]}"
provider_test_target="${2:-linux-x64}"
[[ "$provider_test_target" =~ ^[A-Za-z0-9_-]+$ ]] || die 'invalid build target'
exec python3 "$REPO_ROOT/scripts/run-native-provider-tests.py" \
  --source "$SRC" --build "$SRC/out/$provider_test_target" --out "$provider_test_out" \
  --image-id "$APOSTATE_BUILD_IMAGE_ID"
