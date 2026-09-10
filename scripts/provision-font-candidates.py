#!/usr/bin/env python3
"""Copy hash-pinned fonts into a new isolated directory.

Example:
  python3 scripts/provision-font-candidates.py \
    --manifest build/font-candidates-windows.json \
    --root windows=/path/to/operator/windows \
    --root macos=/path/to/operator/macos --out /srv/fonts/windows-candidate

Local fonts remain operator-provided inputs. This script does not establish
licensing or reference-device equivalence. Its receipt records the bytes used;
conformance requires a browser measurement against the named reference.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import tempfile
import urllib.request


def digest(data):
    return hashlib.sha256(data).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--root', action='append', default=[], metavar='NAME=PATH')
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    roots = {}
    for item in args.root:
        name, sep, path = item.partition('=')
        if not sep or not name or not path or name in roots:
            parser.error('roots require unique NAME=PATH values')
        roots[name] = Path(path).resolve()
    manifest_bytes = args.manifest.read_bytes()
    manifest = json.loads(manifest_bytes)
    if manifest.get('format') != 1 or not manifest.get('files'):
        parser.error('expected format 1 with a nonempty files list')
    targets = set()
    for entry in manifest['files']:
        target = entry['target']
        if target != Path(target).name or target in ('.', '..', 'receipt.json') or target in targets:
            parser.error('invalid or duplicate target: ' + target)
        targets.add(target)
        if not re.fullmatch('[0-9a-f]{64}', entry['sha256']):
            parser.error('invalid SHA-256 for ' + target)
        if 'url' in entry:
            if 'root' in entry or not re.match(
                    r'^https://raw\.githubusercontent\.com/google/fonts/[0-9a-f]{40}/', entry['url']):
                parser.error('download requires an immutable Google Fonts commit URL')
        elif entry.get('root') not in roots:
            parser.error('missing root: ' + str(entry.get('root')))
    receipt = {'format': 1, 'manifest_sha256': digest(manifest_bytes),
               'files': manifest['files']}
    out = args.out.absolute()
    if out.is_symlink():
        parser.error('output must not be a symlink')
    if out.exists():
        if not out.is_dir() or {p.name for p in out.iterdir()} != targets | {'receipt.json'}:
            parser.error('existing output has unexpected files; choose a new directory')
        if json.loads((out / 'receipt.json').read_text()) != receipt:
            parser.error('existing output has a different receipt; choose a new directory')
        for entry in manifest['files']:
            path = out / entry['target']
            if path.is_symlink() or digest(path.read_bytes()) != entry['sha256']:
                parser.error('existing output checksum mismatch: ' + entry['target'])
        print('verified existing font directory:', out)
        return
    out.parent.mkdir(parents=True, exist_ok=True)
    stage = Path(tempfile.mkdtemp(prefix='.' + out.name + '-', dir=out.parent))
    try:
        for entry in manifest['files']:
            if 'url' in entry:
                with urllib.request.urlopen(entry['url'], timeout=60) as response:
                    data = response.read()
            else:
                root = roots[entry['root']]
                source = (root / entry['path']).resolve()
                if not source.is_relative_to(root):
                    parser.error('source escapes root: ' + entry['path'])
                data = source.read_bytes()
            if digest(data) != entry['sha256']:
                parser.error('input checksum mismatch: ' + entry['target'])
            (stage / entry['target']).write_bytes(data)
        (stage / 'receipt.json').write_text(json.dumps(receipt, indent=2) + '\n')
        stage.rename(out)
    finally:
        if stage.exists():
            shutil.rmtree(stage)
    print('provisioned', len(targets), 'pinned files in', out)


if __name__ == '__main__':
    main()
