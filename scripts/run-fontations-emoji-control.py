#!/usr/bin/env python3
"""Run the bounded emoji diagnostic in the prepared CPU-only build container."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import subprocess

ROOT = Path(__file__).resolve().parents[1]


def sha256(path):
    result = hashlib.sha256()
    with path.open('rb') as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b''):
            result.update(chunk)
    return result.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--build', required=True, type=Path)
    parser.add_argument('--font', required=True, type=Path)
    parser.add_argument('--font-index', type=int, default=0, choices=range(9))
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args()
    build, font, out = args.build.resolve(), args.font.resolve(), args.out.resolve()
    binary = build / 'fontations_emoji_control'
    image = json.loads((ROOT/'.workspace/build-container/current.json').read_text())['image_id']
    assert re.fullmatch(r'sha256:[0-9a-f]{64}', image)
    assert binary.is_file() and font.is_file()
    out.mkdir(parents=True, exist_ok=False)
    container = 'apostate-emoji-' + secrets.token_hex(8)
    command = ['docker', 'run', '--name', container, '--rm', '--init',
               '--user', f'{os.getuid()}:{os.getgid()}', '--network', 'none', '--read-only',
               '--memory', '2g', '--cpus', '2', '--pids-limit', '128',
               '--tmpfs', '/tmp:rw,nosuid,nodev,size=256m',
               '--mount', f'type=bind,source={build},target=/build,readonly',
               '--mount', f'type=bind,source={font},target=/input/font.ttc,readonly',
               '--mount', f'type=bind,source={out},target=/output',
               '--workdir', '/build', '--entrypoint', '/build/fontations_emoji_control', image,
               '--font-file=/input/font.ttc', f'--font-index={args.font_index}',
               '--output-dir=/output/render']
    receipt = {'diagnostic_only': True, 'corpus_admission': False,
               'command': command, 'image_id': image, 'binary_sha256': sha256(binary),
               'font_sha256': sha256(font), 'font_index': args.font_index, 'passed': False}
    try:
        with (out/'test.log').open('w') as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=240)
        receipt['returncode'] = result.returncode
        assert result.returncode == 0, 'native diagnostic failed'
        data = json.loads((out/'render/result.json').read_text())
        assert data['diagnosticOnly'] and data['corpusAdmission'] is False
        assert len(data['rows']) == 320
        receipt.update(passed=True, rows=320, result_sha256=sha256(out/'render/result.json'))
    except Exception as error:
        receipt['error'] = str(error)
    finally:
        subprocess.run(['docker', 'rm', '-f', container], stdout=subprocess.DEVNULL,
                       stderr=subprocess.DEVNULL, timeout=30)
        (out/'receipt.json').write_text(json.dumps(receipt, indent=2)+'\n')
    print(json.dumps(receipt))
    return 0 if receipt['passed'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
