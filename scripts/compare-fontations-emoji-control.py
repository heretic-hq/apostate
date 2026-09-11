#!/usr/bin/env python3
"""Compare bounded Fontations diagnostics with the native CoreText control.

Pixel differences are measurements, not test failure or conformance closure.
The unmodified design-space flag must preserve the raw color-font branch.
"""
import argparse
import hashlib
import json
from pathlib import Path


def digest(data):
    return hashlib.sha256(data).hexdigest()


def read_pixels(directory, name, size):
    path = (directory / name).resolve()
    if path.parent != directory.resolve() or path.suffix != '.rgba':
        raise ValueError('Raw image must be a direct .rgba child')
    width, height = map(int, size)
    if (width, height) not in [(256, 192), (512, 384)]:
        raise ValueError('Unexpected bounded image size')
    if path.stat().st_size != width * height * 4:
        raise ValueError('Unexpected raw image length')
    return path.read_bytes()


def compare(a, b):
    equal = a == b
    return {
        'sameRGBA': equal,
        'leftSHA256': digest(a),
        'rightSHA256': digest(b),
        'differingBytes': 0 if equal else sum(x != y for x, y in zip(a, b)),
        'differingAlphaPixels': 0 if equal else sum(x != y for x, y in zip(a[3::4], b[3::4])),
        'maxChannelDelta': 0 if equal else max(abs(x-y) for x, y in zip(a, b)),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--native', required=True, type=Path)
    parser.add_argument('--linux', required=True, type=Path)
    parser.add_argument('--output', required=True, type=Path)
    args = parser.parse_args()
    native_data = (args.native / 'result.json').read_bytes()
    linux_data = (args.linux / 'result.json').read_bytes()
    native, linux = json.loads(native_data), json.loads(linux_data)
    for document in [native, linux]:
        if document.get('diagnosticOnly') is not True or document.get('corpusAdmission') is not False:
            raise ValueError('Only diagnostic documents are accepted')
    if len(linux['rows']) != 320:
        raise ValueError('Expected exactly320 Fontations rows')
    native_rows = {}
    for row in native['rows']:
        for render in row.get('renders', []):
            native_rows[row['mode'], row['size'], row['scalar'], render['scale']] = row, render
        for render in row.get('deviceSizeRenders', []):
            native_rows['named_device_exact', row['size'], row['scalar'], render['scale']] = row, render
    linux_rows = {(row['mode'], row['size'], row['scalar'], row['scale']): row for row in linux['rows']}
    if len(linux_rows) != 320:
        raise ValueError('Duplicate Fontations rows')
    expected_keys = {(m, s, c, d) for m in ['file_exact', 'file_flagged', 'file_reconstruction', 'file_device_reconstruction']
                     for s in [8, 12, 14, 16, 20, 24, 32, 48]
                     for c in [0x1f512, 0x1f600, 0x1f680, 0x2600, 0x2764] for d in [1, 2]}
    if set(linux_rows) != expected_keys:
        raise ValueError('Unexpected diagnostic matrix')
    comparisons, preservation = [], []
    for key, row in linux_rows.items():
        mode, size, scalar, scale = key
        expected_mode = {'file_exact': 'file_exact', 'file_flagged': 'file_exact',
                         'file_reconstruction': 'named_exact',
                         'file_device_reconstruction': 'named_device_exact'}[mode]
        nr, render = native_rows[expected_mode, size, scalar, scale]
        actual = read_pixels(args.linux, row['file'], row['pixelSize'])
        expected = read_pixels(args.native, render['rawFile'], render['pixelSize'])
        if digest(expected) != render['rgbaSHA256']:
            raise ValueError('Native raw image hash differs from its measurement')
        comparisons.append({
            'mode': mode, 'size': size, 'scalar': scalar, 'scale': scale,
            'nativeMode': expected_mode, 'nativePostscript': nr['postscript'],
            'nativeAdvanceSpace': 'device' if expected_mode == 'named_device_exact' else 'logical',
            'linuxAdvanceSpace': 'logical font request',
            'nativeAdvance': render.get('drawFontAdvance', nr['advance']), 'linuxAdvance': row['advance'],
            'nativeBoundsYUp': render.get('drawFontBounds', nr['bounds']), 'linuxBoundsYDown': row['bounds'],
            'sameAlphaBounds': render['alphaBoundsInMemoryRows'] == row['alphaBoundsInMemoryRows'],
            **compare(actual, expected),
        })
        if mode == 'file_flagged':
            before = linux_rows['file_exact', size, scalar, scale]
            baseline = read_pixels(args.linux, before['file'], before['pixelSize'])
            fields = ['advance', 'bounds', 'publicPathPresent', 'publicPathBounds', 'fontMetrics', 'scaler']
            preservation.append({'size': size, 'scalar': scalar, 'scale': scale,
                'samePixels': actual == baseline,
                'sameMetrics': all(row.get(field) == before.get(field) for field in fields)})
    summary = {
        'diagnosticOnly': True, 'corpusAdmission': False,
        'nativeResultSHA256': digest(native_data), 'linuxResultSHA256': digest(linux_data),
        'comparisons': comparisons, 'flagPreservation': preservation,
        'counts': {mode: {'total': sum(c['mode'] == mode for c in comparisons),
                         'sameRGBA': sum(c['mode'] == mode and c['sameRGBA'] for c in comparisons),
                         'sameAlphaBounds': sum(c['mode'] == mode and c['sameAlphaBounds'] for c in comparisons)}
                   for mode in ['file_exact', 'file_flagged', 'file_reconstruction', 'file_device_reconstruction']},
        'flagPreserved': all(c['samePixels'] and c['sameMetrics'] for c in preservation),
        'limitations': 'Different platform renderers. Reconstruction changes the diagnostic drawing request only; no production font provider or browser canvas is tested.'}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(summary, indent=2) + '\n')
    print(json.dumps({key: summary[key] for key in ['counts', 'flagPreserved']}, indent=2))
    return 0 if summary['flagPreserved'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
