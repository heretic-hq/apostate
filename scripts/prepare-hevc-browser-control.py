#!/usr/bin/env python3
"""Package pinned Chromium media fixtures as explicit browser diagnostic inputs."""
import argparse
import base64
import hashlib
import json
from pathlib import Path
import subprocess

REVISION = '79460ebecaa5625e57a5fb679a735659e73dc687'
FILES = {
    'main': ('bear-1280x720-hevc-no-audio.mp4', 'hvc1.1.6.L93.B0'),
    'main10': ('bear-1280x720-hevc-10bit-no-audio.mp4', 'hvc1.2.4.L93.B0'),
    'h264': ('bear.mp4', 'avc1.64001E'),
    'fragmented': ('bear-320x240-v-2frames_frag-hevc.mp4', 'hev1.1.6.L60.90'),
    'main10Frame': ('bear-320x180-10bit-frame-0.hevc', 'hev1.2.4.L93.B0'),
    'h264Frame': ('bear-320x192-high-frame-0.h264', 'avc1.64001E'),
}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--expect-unavailable', action='store_true')
    args = parser.parse_args()
    assert not args.out.exists()
    result = {'expectHEVC': not args.expect_unavailable, 'revision': REVISION, 'assets': {}}
    for key, (name, codec) in FILES.items():
        data = subprocess.check_output(['git', '-C', str(args.source), 'show',
                                        REVISION + ':media/test/data/' + name])
        assert 0 < len(data) < 4 * 1024 * 1024
        result['assets'][key] = {'name': name, 'codec': codec, 'sha256': hashlib.sha256(data).hexdigest(),
                                 'base64': base64.b64encode(data).decode()}
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(result) + '\n')


if __name__ == '__main__':
    main()
