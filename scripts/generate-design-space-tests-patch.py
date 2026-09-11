#!/usr/bin/env python3
"""Generate test-only Chromium coverage for the design-space FreeType flag."""
import argparse
import base64
import difflib
import hashlib
import json
from pathlib import Path
import subprocess
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = '79460ebecaa5625e57a5fb679a735659e73dc687'
SKIA = '0873ec164a06966b90ae0d43ef783cfb180084ae'
FONT_SHA = '466989fd178ca6ed13641893b7003e5d6ec36e42c2a816dee71f87b775ea097f'

def fetch(repo, revision, path):
    url = f'https://chromium.googlesource.com/{repo}/+/{revision}/{path}?format=TEXT'
    data = base64.b64decode(urllib.request.urlopen(url, timeout=60).read())
    return data, url

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--scratch', type=Path, required=True)
    args = parser.parse_args()
    scratch = args.scratch.absolute()
    scratch.mkdir(parents=True, exist_ok=False)
    # Own Git root is required when scratch is nested below the repository.
    subprocess.run(['git', 'init', '--quiet', str(scratch)], check=True)
    source, url = fetch('chromium/src', CHROMIUM, 'skia/BUILD.gn')
    font, font_url = fetch('skia', SKIA, 'resources/fonts/Roboto-Regular.ttf')
    assert hashlib.sha256(font).hexdigest() == FONT_SHA
    old = source.decode()
    anchor = 'test("skia_unittests") {\n'
    assert old.count(anchor) == 1
    new = old.replace(anchor, '''if (is_linux) {
  copy("design_space_test_font") {
    sources = [ "../third_party/skia/resources/fonts/Roboto-Regular.ttf" ]
    outputs = [ "$root_out_dir/test_fonts/DesignSpaceRoboto-Regular.ttf" ]
  }
}

''' + anchor)
    anchor = '  data_deps = [ "//testing/buildbot/filters:skia_unittests_filters" ]\n'
    assert new.count(anchor) == 1
    new = new.replace(anchor, anchor + '''
  if (is_linux) {
    assert(enable_freetype, "Design-space path tests require real FreeType")
    sources += [ "ext/design_space_paths_unittest.cc" ]
    data_deps += [ ":design_space_test_font" ]
  }
''')
    test = (ROOT / 'scripts/fixtures/design_space_paths_unittest.cc').read_text()
    changes = [('skia/BUILD.gn', old, new), ('skia/ext/design_space_paths_unittest.cc', '', test)]
    patch = 'Subject: [PATCH] tests: exercise design-space FreeType paths in skia_unittests\n\n'
    for path, before, after in changes:
        target = scratch / path
        target.parent.mkdir(parents=True, exist_ok=True)
        if before: target.write_text(before)
        patch += f'diff --git a/{path} b/{path}\n'
        if not before: patch += 'new file mode 100644\n'
        patch += ''.join(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                         fromfile='a/' + path if before else '/dev/null', tofile='b/' + path))
    result = ROOT / 'patches/0053-design-space-paths-gtests.patch'
    result.write_text(patch)
    (scratch / '0053.patch').write_text(patch)
    subprocess.run(['git', '-C', str(scratch), 'apply', '--check', '--whitespace=error', '0053.patch'], check=True)
    subprocess.run(['git', '-C', str(scratch), 'apply', '--whitespace=error', '0053.patch'], check=True)
    for path, _, expected in changes:
        assert (scratch / path).read_text() == expected
    manifest = {'chromium_revision': CHROMIUM, 'skia_revision': SKIA,
                'gn_url': url, 'gn_before_sha256': hashlib.sha256(source).hexdigest(),
                'font_url': font_url, 'font_sha256': FONT_SHA,
                'patch_sha256': hashlib.sha256(patch.encode()).hexdigest(),
                'files': {path: hashlib.sha256(after.encode()).hexdigest() for path, _, after in changes},
                'v0': 'exact pinned GN source; strict patch application and full byte readback pass',
                'v1_v2': 'not run; parent compiles and executes skia_unittests'}
    (scratch / 'candidate-manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
    print(result)

if __name__ == '__main__': main()
