"""Filesystem and input-validation tests. Fake bytes never stand in for a CDM."""

import copy
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location(
    "provision_widevine", Path(__file__).with_name("provision-widevine.py"))
widevine = importlib.util.module_from_spec(spec)
spec.loader.exec_module(widevine)


class ProvisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.source = self.root / "installed-cdm"
        self.library = self.source / "_platform_specific/linux_x64/libwidevinecdm.so"
        self.library.parent.mkdir(parents=True)
        self.library.write_bytes(b"test bytes, not an executable CDM")
        self.manifest = {
            "version": "4.10.3050.0",
            "x-cdm-module-versions": "4",
            "x-cdm-interface-versions": "10",
            "x-cdm-host-versions": "10",
            "x-cdm-codecs": "vp8,vp09,avc1,av01",
            "x-cdm-supported-encryption-schemes": ["cenc", "cbcs"],
        }
        self.manifest_path = self.source / "manifest.json"
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.pin = copy.deepcopy(json.loads(widevine.PIN_FILE.read_text()))
        self.pin["manifest_sha256"] = hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()
        self.pin["library_sha256"] = hashlib.sha256(self.library.read_bytes()).hexdigest()
        self.pin["library_size"] = self.library.stat().st_size
        self.user_dir = self.root / "fresh-profile"
        self.hint = self.user_dir / "WidevineCdm" / widevine.HINT_NAME

    def run_provision(self):
        return widevine.provision(self.source, self.user_dir, self.pin)

    def test_publishes_standard_hint_without_copying_and_is_idempotent(self):
        installed_before = {p: p.read_bytes() for p in self.source.rglob("*") if p.is_file()}
        self.assertEqual(self.run_provision()["status"], "created")
        self.assertEqual(json.loads(self.hint.read_text()), {"Path": str(self.source)})
        stat_before = self.hint.stat()
        self.assertEqual(self.run_provision()["status"], "unchanged")
        self.assertEqual(self.hint.stat().st_ino, stat_before.st_ino)
        self.assertEqual(self.hint.stat().st_mtime_ns, stat_before.st_mtime_ns)
        self.assertEqual([p for p in self.user_dir.rglob("*") if p.is_file()], [self.hint])
        self.assertEqual({p: p.read_bytes() for p in installed_before}, installed_before)

    def test_permits_runner_seeded_preferences(self):
        prefs = self.user_dir / "Default/Preferences"
        prefs.parent.mkdir(parents=True)
        prefs.write_text('{"unrelated": true}')
        self.run_provision()
        self.assertEqual(prefs.read_text(), '{"unrelated": true}')

    def test_bad_inputs_make_no_user_directory(self):
        cases = (self.manifest_path, self.library)
        for path in cases:
            with self.subTest(path=path):
                original = path.read_bytes()
                path.write_bytes(original + b"changed")
                with self.assertRaises(widevine.ProvisionError):
                    self.run_provision()
                self.assertFalse(self.user_dir.exists())
                path.write_bytes(original)

    def test_rejects_unsupported_interface_even_with_updated_hash_pin(self):
        self.manifest["x-cdm-interface-versions"] = "12"
        self.manifest_path.write_text(json.dumps(self.manifest))
        self.pin["manifest_sha256"] = hashlib.sha256(self.manifest_path.read_bytes()).hexdigest()
        self.pin["interface_versions"] = [12]
        with self.assertRaisesRegex(widevine.ProvisionError, "interface"):
            self.run_provision()
        self.assertFalse(self.user_dir.exists())

    def test_rejects_manifest_version_mismatch(self):
        self.pin["version"] = "4.10.9999.0"
        with self.assertRaisesRegex(widevine.ProvisionError, "version"):
            self.run_provision()
        self.assertFalse(self.user_dir.exists())

    def test_rejects_conflicting_or_invalid_hint_unchanged(self):
        self.hint.parent.mkdir(parents=True)
        for content in ('{"Path":"/another/installation"}', "invalid json"):
            with self.subTest(content=content):
                self.hint.write_text(content)
                with self.assertRaises(widevine.ProvisionError):
                    self.run_provision()
                self.assertEqual(self.hint.read_text(), content)

    def test_rejects_concurrent_conflicting_hint_without_overwrite(self):
        actual_link = os.link

        def competing_link(source, destination):
            Path(destination).write_text('{"Path":"/concurrent/installation"}')
            return actual_link(source, destination)

        with patch.object(widevine.os, "link", side_effect=competing_link):
            with self.assertRaisesRegex(widevine.ProvisionError, "conflicts"):
                self.run_provision()
        self.assertEqual(self.hint.read_text(), '{"Path":"/concurrent/installation"}')
        self.assertEqual(list(self.hint.parent.iterdir()), [self.hint])

    def test_rejects_active_or_stale_singleton_symlink(self):
        self.user_dir.mkdir()
        (self.user_dir / "SingletonLock").symlink_to("nonexistent-host-999")
        with self.assertRaisesRegex(widevine.ProvisionError, "SingletonLock"):
            self.run_provision()
        self.assertFalse(self.hint.exists())

    def test_rejects_symlink_hint_and_component_directory(self):
        outside = self.root / "outside"
        outside.mkdir()
        self.user_dir.mkdir()
        self.hint.parent.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(widevine.ProvisionError, "symlink"):
            self.run_provision()
        self.hint.parent.unlink()
        self.hint.parent.mkdir()
        target = outside / "hint"
        target.write_text(json.dumps({"Path": str(self.source)}))
        self.hint.symlink_to(target)
        with self.assertRaisesRegex(widevine.ProvisionError, "symlink"):
            self.run_provision()

    def test_rejects_source_escape_and_source_as_user_directory(self):
        original = self.library.read_bytes()
        outside = self.root / "outside.so"
        outside.write_bytes(original)
        self.library.unlink()
        self.library.symlink_to(outside)
        with self.assertRaisesRegex(widevine.ProvisionError, "inside"):
            self.run_provision()
        self.library.unlink()
        self.library.write_bytes(original)
        self.user_dir = self.source
        with self.assertRaisesRegex(widevine.ProvisionError, "separate"):
            self.run_provision()


if __name__ == "__main__":
    unittest.main()
