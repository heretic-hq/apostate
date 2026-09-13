#!/usr/bin/env python3
"""Decomposer accepts captures without optional hardware probes."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "decompose-capture.py"
REFERENCE = ROOT / "resources" / "fingerprints" / "raw" / "m4-max-chrome-20260908T163229Z.json"


class DecomposeCaptureTests(unittest.TestCase):
    def test_optional_memory_and_audio_probes_can_be_absent(self) -> None:
        capture = copy.deepcopy(json.loads(REFERENCE.read_text(encoding="utf-8")))
        capture["probes"]["navigator.scalars"]["value"].pop("deviceMemory", None)
        capture["repeat"]["navigator.scalars"]["value"].pop("deviceMemory", None)
        capture["probes"].pop("memory.heap", None)
        capture["repeat"].pop("memory.heap", None)
        capture["probes"].pop("audio.properties", None)
        capture["repeat"].pop("audio.properties", None)

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture_path = root / "partial.json"
            output = root / "blocks"
            capture_path.write_text(json.dumps(capture), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(capture_path), "--out", str(output)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            hardware = next((output / "hardware").glob("*.json"))
            block = json.loads(hardware.read_text(encoding="utf-8"))
            self.assertNotIn("memory_total_bytes", block)
            self.assertNotIn("audio_buffer_frames", block)
            self.assertEqual(block["logical_cores"], 14)

    def test_present_audio_probe_derives_buffer_frames(self) -> None:
        capture = copy.deepcopy(json.loads(REFERENCE.read_text(encoding="utf-8")))

        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            capture_path = root / "capture.json"
            output = root / "blocks"
            capture_path.write_text(json.dumps(capture), encoding="utf-8")
            result = subprocess.run(
                [sys.executable, str(SCRIPT), str(capture_path), "--out", str(output)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            hardware = next((output / "hardware").glob("*.json"))
            block = json.loads(hardware.read_text(encoding="utf-8"))
            self.assertEqual(block["audio_buffer_frames"], 256)
            self.assertEqual(block["audio_sample_rate"], 48000)
            self.assertNotIn("memory_total_bytes", block)


if __name__ == "__main__":
    unittest.main()
