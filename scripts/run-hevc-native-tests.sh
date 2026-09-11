#!/usr/bin/env bash
# Run the decoder suite and require the real HEVC decode branch to execute.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [[ "$(uname -s)" == Linux && -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/run-hevc-native-tests.sh "$@"
fi
hevc_test_out="${1:?usage: run-hevc-native-tests.sh <fresh-receipt-directory> [target]}"
hevc_test_target="${2:-linux-x64}"
python3 - "$REPO_ROOT" "$SRC" "$SRC/out/$hevc_test_target" "$hevc_test_out" <<'PY'
import hashlib, importlib.util, json, os, pathlib, subprocess, sys, xml.etree.ElementTree as ET
repo, source, build, out = map(pathlib.Path, sys.argv[1:])
out = out.absolute()
out.mkdir(parents=True, exist_ok=False)
for device in ('/dev/dri', '/dev/kfd', '/dev/nvidiactl', '/dev/nvidia0', '/dev/dxg', '/dev/vfio'):
    assert not pathlib.Path(device).exists(), 'physical GPU exposed: ' + device
spec = importlib.util.spec_from_file_location('native_gates', repo/'scripts/run-native-provider-tests.py')
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)
env, environment_receipt = runner.clean_environment(build)
expected = set('''HevcSupportRequiresRegisteredDecoder HevcUnsupportedProfileRemainsRejected
HevcEncryptedInputRemainsRejected HevcMain10DecodeAndReset Initialize_Normal
Initialize_OpenDecoderFails Reinitialize_Normal Reinitialize_AfterDecodeFrame
Reinitialize_AfterReset DecodeFrame_Normal DecodeFrame_OOM DecodeFrame_DecodeError
DecodeFrame_DecodeErrorAtEndOfStream DecodeFrame_Smaller DecodeFrame_Larger
Reset_Initialized Reset_Decoding Reset_EndOfStream Destroy_Initialized
Destroy_Decoding Destroy_EndOfStream'''.split())
command = [str(build/'media_unittests'), '--gtest_filter=FFmpegVideoDecoderTest.*',
           '--single-process-tests', '--gtest_output=xml:' + str(out/'results.xml')]
receipt = {'diagnostic_only': True, 'corpus_admission': False, 'passed': False,
           'command': command, 'image_id': os.environ['APOSTATE_BUILD_IMAGE_ID'],
           'binary_sha256': runner.sha256(build/'media_unittests'),
           'environment': environment_receipt}
try:
    with (out/'test.log').open('w') as log:
        result = subprocess.run(command, cwd=source, env=env, stdout=log,
                                stderr=subprocess.STDOUT, timeout=300)
    receipt['returncode'] = result.returncode
    cases = ET.parse(out/'results.xml').findall('.//testcase')
    assert len(cases) == len(expected) and {c.get('name') for c in cases} == expected
    assert all(c.get('status') == 'run' and c.find('skipped') is None and
               c.find('failure') is None and c.find('error') is None for c in cases)
    main10 = next(c for c in cases if c.get('name') == 'HevcMain10DecodeAndReset')
    properties = {p.get('name'): p.get('value') for p in main10.findall('./properties/property')}
    assert properties.get('hevc_decoder_registered') in {'1', 'true'}, properties
    assert result.returncode == 0
    receipt.update(passed=True, executed=len(cases), hevc_decoder_registered=True)
except Exception as error:
    receipt['error'] = str(error)
finally:
    (out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
print(json.dumps(receipt))
raise SystemExit(0 if receipt['passed'] else 1)
PY
