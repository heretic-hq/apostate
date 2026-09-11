#!/usr/bin/env bash
# Run the actual Fontations factory's seven precision and fallback controls.
source "$(dirname "$0")/lib.sh"
if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/run-fontations-design-tests.sh "$@"
fi
fontations_test_out="${1:?usage: run-fontations-design-tests.sh <fresh-directory> [target]}"
fontations_test_target="${2:-linux-x64}"
python3 - "$REPO_ROOT" "$SRC" "$SRC/out/$fontations_test_target" "$fontations_test_out" <<'PY'
import hashlib, importlib.util, json, pathlib, subprocess, sys
repo, source, build, out = map(pathlib.Path, sys.argv[1:])
out = out.absolute()
out.mkdir(parents=True, exist_ok=False)
spec = importlib.util.spec_from_file_location('native_gates', repo/'scripts/run-native-provider-tests.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
pin = '0873ec164a06966b90ae0d43ef783cfb180084ae'
assert subprocess.check_output(['git', '-C', str(source/'third_party/skia'), 'rev-parse', 'HEAD'], text=True).strip() == pin
fonts = {}
for name in ('Distortable.ttf', 'SampleSVG.ttf', 'cbdt.ttf', 'colr.ttf', 'sbix.ttf'):
    expected = subprocess.check_output(['git', '-C', str(source/'third_party/skia'), 'show', pin+':resources/fonts/'+name])
    actual = (build/'test_fonts'/('Fontations-'+name)).read_bytes()
    assert actual == expected, 'test font differs from pinned source: '+name
    fonts[name] = hashlib.sha256(actual).hexdigest()
roboto = (build/'test_fonts/DesignSpaceRoboto-Regular.ttf').read_bytes()
assert hashlib.sha256(roboto).hexdigest() == '466989fd178ca6ed13641893b7003e5d6ec36e42c2a816dee71f87b775ea097f'
fonts['Roboto-Regular.ttf'] = hashlib.sha256(roboto).hexdigest()
expected = {'FontationsDesignSpaceTest.'+name for name in (
    'StaticAdvanceUsesDesignUnitsAndSeparateCache', 'TruncatedMetricsRetainNativeFallback',
    'StaticOutlineScalesBeforeFloatStorage', 'PostMatrixRotationPreservesEachBranch',
    'HintingAndSynthesisRetainBaseline', 'VariableFacesRetainActualVariation',
    'ColorSvgAndBitmapFacesRetainBaseline')}
def validate(data):
    iterations = data.get('per_iteration_data')
    runner.require(isinstance(iterations, list) and len(iterations) == 1, 'expected one iteration')
    runner.require(set(iterations[0]) == expected, 'missing or extra Fontations tests')
    for name, attempts in iterations[0].items():
        runner.require(len(attempts) == 1 and attempts[0].get('status') == 'SUCCESS', name+' did not pass once')
    return {'executed': 7, 'passed': 7, 'cases': sorted(expected)}
environment, _ = runner.clean_environment(build)
command = [str(build/'skia_unittests'), '--gtest_filter=FontationsDesignSpaceTest.*',
    '--test-launcher-jobs=1', '--test-launcher-retry-limit=0', '--test-launcher-batch-limit=1',
    '--test-launcher-summary-output='+str(out/'fontations/results.json')]
receipt = runner.run_gate('fontations', command, 180, validate, source, out, environment)
receipt['font_sha256'] = fonts
receipt['binary_sha256'] = runner.sha256(build/'skia_unittests')
(out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
print(json.dumps({'out': str(out), 'passed': receipt['passed'], 'expected_tests': 7}))
raise SystemExit(0 if receipt['passed'] else 1)
PY
