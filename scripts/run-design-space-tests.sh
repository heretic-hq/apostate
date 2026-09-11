#!/usr/bin/env bash
# Execute the three Linux FreeType tests and reject missing or skipped coverage.
set -euo pipefail
source "$(dirname "$0")/lib.sh"
if [ "$(uname -s)" = Linux ] && [ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]; then
  exec bash "$REPO_ROOT/scripts/in-linux-build-container.sh" scripts/run-design-space-tests.sh "$@"
fi
font_test_out="${1:?usage: run-design-space-tests.sh <fresh-receipt-directory> [target]}"
font_test_target="${2:-linux-x64}"
python3 - "$SRC/out/$font_test_target" "$font_test_out" <<'PY'
import hashlib, json, pathlib, subprocess, sys, xml.etree.ElementTree as ET
build, out = pathlib.Path(sys.argv[1]), pathlib.Path(sys.argv[2]).absolute()
out.mkdir(parents=True, exist_ok=False)
font = build / 'test_fonts/DesignSpaceRoboto-Regular.ttf'
assert hashlib.sha256(font.read_bytes()).hexdigest() == '466989fd178ca6ed13641893b7003e5d6ec36e42c2a816dee71f87b775ea097f'
binary = build / 'skia_unittests'
command = [str(binary), '--gtest_filter=DesignSpacePathsTest.*',
           '--test-launcher-jobs=1', '--test-launcher-retry-limit=0',
           '--gtest_output=xml:' + str(out/'results.xml')]
receipt = {'command': command, 'binary_sha256': hashlib.sha256(binary.read_bytes()).hexdigest(),
           'font_sha256': hashlib.sha256(font.read_bytes()).hexdigest(), 'passed': False}
try:
    with (out/'test.log').open('w') as log:
        result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    receipt['returncode'] = result.returncode
    cases = ET.parse(out/'results.xml').findall('.//testcase')
    expected = {'IdentitySerializationAndCacheDescriptor', 'SyntheticBoldRetainsNativePath',
                'DesignOutlineHasExactUnroundedBounds'}
    assert len(cases) == len(expected) and {c.get('name') for c in cases} == expected
    assert all(c.get('status') == 'run' and c.find('skipped') is None and
               c.find('failure') is None and c.find('error') is None for c in cases)
    assert result.returncode == 0
    receipt['passed'] = True
finally:
    (out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
print(f'all {len(expected)} real FreeType tests ran and passed')
PY
