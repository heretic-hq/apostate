"""Focused checks for scripts/validate-release-baseline.py."""

from __future__ import annotations

import hashlib
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("validate-release-baseline.py")


def _contents_hash(root: Path, names: list[str]) -> str:
    digest = hashlib.sha256()
    for name in names:
        digest.update(name.encode() + b"\0")
        digest.update((root / "patches" / name).read_bytes())
        digest.update(b"\0")
    return digest.hexdigest()


def _make_fixture() -> tuple[tempfile.TemporaryDirectory[str], Path]:
    temporary = tempfile.TemporaryDirectory()
    root = Path(temporary.name)
    (root / "build").mkdir()
    (root / "patches").mkdir()
    names = ["0001-first.patch", "0002-second.patch"]
    for name, body in zip(names, [b"first\n", b"second\n"]):
        (root / "patches" / name).write_bytes(body)
    (root / "patches" / "series").write_text("\n".join(names) + "\n")
    (root / "build" / "CHROMIUM_VERSION").write_text("152.0.7977.83\n")
    series_hash = hashlib.sha256((root / "patches" / "series").read_bytes()).hexdigest()
    contents_hash = _contents_hash(root, names)
    (root / "build" / "MANIFEST.lock").write_text(
        '# Generated fixture\n'
        'chromium_version     = "152.0.7977.83"\n'
        f'patch_series_sha256  = "{series_hash}"\n'
        f'patch_contents_sha256 = "{contents_hash}"\n'
        "\n[outputs]\n"
    )
    return temporary, root


class ValidateReleaseBaselineTests(unittest.TestCase):
    def run_validator(self, root: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            check=False,
            capture_output=True,
            text=True,
        )

    def test_valid_fixture_passes(self):
        temporary, root = _make_fixture()
        with temporary:
            result = self.run_validator(root)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("release baseline valid", result.stdout)

    def test_stale_manifest_is_reported(self):
        temporary, root = _make_fixture()
        with temporary:
            (root / "patches" / "0001-first.patch").write_bytes(b"changed\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("stale MANIFEST.lock", result.stderr)

    def test_missing_patch_fails(self):
        temporary, root = _make_fixture()
        with temporary:
            (root / "patches" / "0002-second.patch").unlink()
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("missing patch 0002-second.patch", result.stderr)

    def test_duplicate_patch_fails(self):
        temporary, root = _make_fixture()
        with temporary:
            series = root / "patches" / "series"
            series.write_text(series.read_text() + "0001-first.patch\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("references 0001-first.patch 2 times", result.stderr)

    def test_version_drift_fails(self):
        temporary, root = _make_fixture()
        with temporary:
            (root / "build" / "CHROMIUM_VERSION").write_text("152.0.7977.84\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Chromium version drift", result.stderr)

    def test_out_of_order_series_fails_before_manifest_hashes(self):
        temporary, root = _make_fixture()
        with temporary:
            series = root / "patches" / "series"
            series.write_text("0002-second.patch\n0001-first.patch\n")
            result = self.run_validator(root)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("out of numeric apply order", result.stderr)

    def test_current_tail_keeps_dependency_order(self):
        names = [line.strip() for line in (Path(__file__).parents[1] / "patches" / "series").read_text().splitlines() if line.strip()]
        positions = {name: names.index(name) for name in names}
        self.assertLess(positions["0060-mac-arm-fft-multiply.patch"], positions["0063-validated-software-element-indices.patch"])
        self.assertLess(positions["0063-validated-software-element-indices.patch"], positions["0071-validated-index-under-robustness.patch"])
        self.assertLess(positions["0070-gl-limits-int64-parse.patch"], positions["0071-validated-index-under-robustness.patch"])

if __name__ == "__main__":
    unittest.main()
