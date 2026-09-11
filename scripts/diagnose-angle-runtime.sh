#!/usr/bin/env bash
# Collect dynamic-loader diagnostics for one software-only ANGLE test case.
source "$(dirname "$0")/lib.sh"
if [[ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/diagnose-angle-runtime.sh "$@"
fi
diagnostic_out="${1:?usage: diagnose-angle-runtime.sh <fresh-directory>}"
mkdir "$diagnostic_out"
diagnostic_out="$(cd "$diagnostic_out" && pwd)"
OUT="$SRC/out/linux-x64"
[[ ! -e /dev/dri && ! -e /dev/nvidia0 && ! -e /dev/kfd ]] || die 'GPU device exposed'
cd "$SRC/third_party/angle"
env -i PATH=/usr/bin:/bin \
  VK_DRIVER_FILES="$OUT/vk_swiftshader_icd.json" \
  VK_ICD_FILENAMES="$OUT/vk_swiftshader_icd.json" \
  xvfb-run -a -s '-screen 0 1280x720x24 -nolisten tcp' \
  env LD_DEBUG=libs,files "$OUT/angle_end2end_tests" \
  --use-config=ES2_Vulkan_SwiftShader \
  --gtest_filter=ParallelShaderCompileTest.DefaultSupportOnLinuxSwiftShader/ES2_Vulkan_SwiftShader \
  --bot-mode --max-processes=1 --batch-size=1 --test-timeout=30 --batch-timeout=45 \
  --flaky-retries=0 --results-file="$diagnostic_out/results.json" \
  > "$diagnostic_out/loader.log" 2>&1
