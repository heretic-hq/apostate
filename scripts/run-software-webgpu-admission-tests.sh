#!/usr/bin/env bash
# Run the six native policy tests after parent-coordinated target compilation.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
[[ "$(uname -s)" == Linux ]] || die 'software admission tests require Linux'
if [[ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/run-software-webgpu-admission-tests.sh "$@"
fi
admission_test_out="${1:?usage: run-software-webgpu-admission-tests.sh <fresh-receipt-directory> [target]}"
admission_test_target="${2:-linux-x64}"
[[ "$admission_test_target" =~ ^[A-Za-z0-9_-]+$ ]] || die 'invalid target'
python3 - "$REPO_ROOT" "$SRC" "$SRC/out/$admission_test_target" "$admission_test_out" <<'PY'
import importlib.util, json, os, pathlib, sys
repo, source, build, out = map(pathlib.Path, sys.argv[1:])
out = out.absolute()
out.mkdir(parents=True, exist_ok=False)
for name in ('/dev/dri', '/dev/kfd', '/dev/nvidiactl', '/dev/nvidia0', '/dev/dxg', '/dev/vfio'):
    assert not pathlib.Path(name).exists(), 'physical GPU exposed: ' + name
spec = importlib.util.spec_from_file_location('native_gates', repo/'scripts/run-native-provider-tests.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
expected = {'ProfileSoftwareWebGPUPolicyTest.' + name for name in (
    'RequiresProfileAndActualSoftwareContext', 'RetainsSafetyAndExplicitAdapterChoices',
    'OnlyApprovedCPUReasonIsIgnored', 'ExclusiveDescriptorParticipatesInIdentity',
    'InvalidExclusivePathsNeverDiscoverAdapters', 'ExclusiveInstanceCannotProbeOtherBackends')}
def validate(data):
    iterations = data.get('per_iteration_data')
    runner.require(isinstance(iterations, list) and len(iterations) == 1, 'expected one iteration')
    runner.require(set(iterations[0]) == expected, 'missing or extra policy tests')
    for name, attempts in iterations[0].items():
        runner.require(len(attempts) == 1 and attempts[0].get('status') == 'SUCCESS',
                       'policy test skipped, retried or failed: ' + name)
    return {'executed': 6, 'passed': 6, 'cases': sorted(expected)}
env, environment_receipt = runner.clean_environment(build)
command = [str(build/'gpu_unittests'), '--gtest_filter=ProfileSoftwareWebGPUPolicyTest.*',
           '--test-launcher-jobs=1', '--test-launcher-retry-limit=0', '--test-launcher-batch-limit=1',
           '--test-launcher-summary-output=' + str(out/'policy/results.json')]
receipt = runner.run_gate('policy', command, 180, validate, source, out, env)
receipt['image_id'] = os.environ['APOSTATE_BUILD_IMAGE_ID']
receipt['environment'] = environment_receipt
receipt['binary_sha256'] = runner.sha256(build/'gpu_unittests')
(out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
raise SystemExit(0 if receipt['passed'] else 1)
PY
