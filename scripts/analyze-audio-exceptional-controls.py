#!/usr/bin/env python3
"""Validate and compare byte-preserving offline exceptional-input diagnostics."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import struct


def validate(root):
    receipt = json.loads((root / 'receipt.json').read_text())
    assert receipt['status'] == 'completed' and receipt['userdata_removed']
    data = json.loads((root / 'result.json').read_text())
    assert data['diagnosticOnly'] and data['corpusAdmission'] is False
    records = {}
    def walk(value, path):
        if isinstance(value, dict):
            if 'encoding' in value:
                assert value['encoding'] == 'float32-host-bytes' and value['littleEndian']
                raw = base64.b64decode(value['base64'], validate=True)
                assert len(raw) == value['frames'] * 4
                assert hashlib.sha256(raw).hexdigest() == value['sha256']
                counts = {'nan': 0, 'infinity': 0, 'zero': 0}
                payloads = {}
                for (word,) in struct.iter_unpack('<I', raw):
                    mag = word & 0x7fffffff
                    if mag > 0x7f800000:
                        counts['nan'] += 1
                        key = f'{word:08x}'
                        payloads[key] = payloads.get(key, 0) + 1
                    elif mag == 0x7f800000: counts['infinity'] += 1
                    elif mag == 0: counts['zero'] += 1
                assert all(value[k] == v for k, v in counts.items())
                assert value['nanPayloadCounts'] == payloads
                records[path] = raw
            else:
                for key, item in value.items(): walk(item, path + '/' + key)
    for row in data['results']: walk(row, row['config']['id'])
    return data, records


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('left', type=Path)
    parser.add_argument('right', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    left, a = validate(args.left)
    right, b = validate(args.right)
    assert (args.left / 'fixture.html').read_bytes() == (args.right / 'fixture.html').read_bytes()
    assert left['fixture'] == right['fixture']
    outcome_differences = []
    for x, y in zip(left['results'], right['results'], strict=True):
        assert x['config'] == y['config']
        if x['accepted'] != y['accepted'] or x.get('error') != y.get('error'):
            outcome_differences.append({'id': x['config']['id'],
                'left': {'accepted': x['accepted'], 'error': x.get('error')},
                'right': {'accepted': y['accepted'], 'error': y.get('error')}})
    differences = []
    for key in sorted(a.keys() | b.keys()):
        if '/inputs/' in key:
            assert key in a and key in b and a[key] == b[key], 'unmatched input bytes'
        if key not in a or key not in b:
            differences.append({'buffer': key, 'missing': 'left' if key not in a else 'right'})
            continue
        if a[key] != b[key]:
            assert len(a[key]) == len(b[key])
            words = [(x[0], y[0]) for x, y in zip(struct.iter_unpack('<I', a[key]), struct.iter_unpack('<I', b[key]), strict=True)]
            indices = [i for i, (x, y) in enumerate(words) if x != y]
            differences.append({'buffer': key, 'different_samples': len(indices), 'first_index': indices[0]})
    summary = [{'id': x['config']['id'], 'accepted': x['accepted'], 'error': x.get('error'),
                'output': {k: v for k, v in x.get('output', {}).items() if k != 'base64'}} for x in left['results']]
    report = {'diagnostic_only': True, 'not_t0': True, 'fixture_sha256': hashlib.sha256((args.left/'fixture.html').read_bytes()).hexdigest(),
              'cases': len(summary), 'verified_buffers_per_run': len(a), 'different_buffers': differences, 'outcome_differences': outcome_differences, 'summary': summary}
    args.out.write_text(json.dumps(report, indent=2) + '\n')
    print(json.dumps({'cases': len(summary), 'verified_buffers':len(a),'different_buffers':len(differences)}))


if __name__ == '__main__': main()
