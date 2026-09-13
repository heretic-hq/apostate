"""Focused tests for deterministic release manifest inputs."""
from pathlib import Path
import json
import tempfile
import unittest

try:
    from .manifest import ManifestError, build_manifest, canonical_json, sha256_bytes, write_manifest
except ImportError:
    from manifest import ManifestError, build_manifest, canonical_json, sha256_bytes, write_manifest


class ManifestTests(unittest.TestCase):
    def _inputs(self, root: Path) -> dict[str, object]:
        patches = root / "patches"
        patches.mkdir()
        (patches / "series").write_text("0001-first.patch\n", encoding="utf-8")
        (patches / "0001-first.patch").write_bytes(b"patch\n")
        (root / "args.gn").write_text('target_os = "linux"\n', encoding="utf-8")
        output = root / "out"
        output.mkdir()
        (output / "chrome").write_bytes(b"browser\n")
        return {
            "target": "linux-x64",
            "chromium_version": "152.0.7977.83",
            "chromium_revision": "source-revision",
            "depot_tools_revision": "depot-revision",
            "args_file": root / "args.gn",
            "toolchain": "clang-19",
            "image": "sha256:image",
            "patch_series": patches / "series",
            "output_dir": output,
            "outputs": ["chrome"],
        }

    def test_same_inputs_have_identical_canonical_manifest_bytes(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = self._inputs(Path(temporary))
            first = build_manifest(**inputs)
            second = build_manifest(**inputs)
            self.assertEqual(first, second)
            self.assertEqual(canonical_json(first), canonical_json(second))
            self.assertEqual(sha256_bytes(canonical_json(first)), sha256_bytes(canonical_json(second)))
            self.assertNotIn(b"built_at", canonical_json(first))

    def test_missing_output_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = self._inputs(Path(temporary))
            inputs["outputs"] = ["chrome", "missing-helper"]
            with self.assertRaisesRegex(ManifestError, "missing output"):
                build_manifest(**inputs)

    def test_legacy_lock_is_never_overwritten(self):
        with tempfile.TemporaryDirectory() as temporary:
            inputs = self._inputs(Path(temporary))
            manifest = build_manifest(**inputs)
            destination = Path(temporary) / "MANIFEST.lock"
            destination.write_text("stale\n", encoding="utf-8")
            with self.assertRaisesRegex(ManifestError, "legacy build/MANIFEST.lock"):
                write_manifest(manifest, destination, overwrite=True)
            self.assertEqual(destination.read_text(encoding="utf-8"), "stale\n")


if __name__ == "__main__":
    unittest.main()
