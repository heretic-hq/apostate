#!/usr/bin/env python3
"""Runner contract tests only: never starts a container, browser or GPU process."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest import mock
from types import SimpleNamespace
import subprocess
import signal

SPEC = importlib.util.spec_from_file_location("software_runner", Path(__file__).with_name("run-software-webgpu-check.py"))
runner = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(runner)

class RunnerContractTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.bundle = self.root / "bundle"
        self.bundle.mkdir()
        (self.bundle / "apostate_dawn_software_check").write_bytes(b"test executable fixture, never launched")
        (self.bundle / "apostate_dawn_software_check").chmod(0o755)
        (self.bundle / "libvk_swiftshader.so").write_bytes(b"test library fixture, never loaded")
        (self.bundle / "vk_swiftshader_icd.json").write_text(json.dumps({"ICD": {"library_path": "libvk_swiftshader.so"}}))
        self.manifest = dict(runner.PINS, files={name: runner.digest(self.bundle / name) for name in runner.FILES})
        self.path = self.root / "manifest.json"
        self.save()
    def save(self):
        self.path.write_text(json.dumps(self.manifest))
    def verify(self):
        return runner.verify_bundle(self.bundle, self.path)
    def test_manifest_requires_actual_source_pins(self):
        output = self.root / "generated.json"
        with mock.patch.object(runner.subprocess, "check_output", side_effect=list(runner.PINS.values())):
            self.assertEqual(runner.write_manifest(self.bundle, Path("/build/source"), output), 0)
        self.assertEqual(json.loads(output.read_text()), self.manifest)
    def test_manifest_wrong_source_does_not_write(self):
        output = self.root / "wrong.json"
        with mock.patch.object(runner.subprocess, "check_output", return_value="wrong"):
            with self.assertRaisesRegex(ValueError, "checkout pin mismatch"):
                runner.write_manifest(self.bundle, Path("/build/source"), output)
        self.assertFalse(output.exists())
    def test_manifest_identical_rerun_is_idempotent(self):
        before = self.path.read_bytes()
        with mock.patch.object(runner.subprocess, "check_output", side_effect=list(runner.PINS.values())):
            self.assertEqual(runner.write_manifest(self.bundle, Path("/build/source"), self.path), 0)
        self.assertEqual(before, self.path.read_bytes())
    def test_valid_hashes_and_pins(self):
        bundle, manifest = self.verify()
        self.assertEqual(bundle, self.bundle.resolve())
        self.assertEqual(manifest, self.manifest)
    def test_mutated_library_rejected(self):
        (self.bundle / "libvk_swiftshader.so").write_bytes(b"changed")
        with self.assertRaisesRegex(ValueError, "hash mismatch"): self.verify()
    def test_wrong_source_pin_rejected(self):
        self.manifest["dawn_revision"] = "0" * 40
        self.save()
        with self.assertRaisesRegex(ValueError, "source pin mismatch"): self.verify()
    def test_symlink_rejected_even_with_matching_hash(self):
        original = self.bundle / "libvk_swiftshader.so"
        outside = self.root / "outside.so"
        original.rename(outside)
        original.symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "file type"): self.verify()
    def test_unpinned_bundle_member_rejected(self):
        (self.bundle / "libvulkan.so.1").write_bytes(b"another provider")
        with self.assertRaisesRegex(ValueError, "unpinned files"): self.verify()
    def test_escaped_manifest_member_rejected(self):
        self.manifest["files"]["../outside"] = "0" * 64
        self.save()
        with self.assertRaisesRegex(ValueError, "exactly"): self.verify()
    def test_icd_escape_rejected_even_if_hash_matches(self):
        p = self.bundle / "vk_swiftshader_icd.json"
        p.write_text(json.dumps({"ICD": {"library_path": "/usr/lib/libvulkan_radeon.so"}}))
        self.manifest["files"][p.name] = runner.digest(p)
        self.save()
        with self.assertRaisesRegex(ValueError, "escapes"): self.verify()
    def command(self, image="registry.invalid/runtime@sha256:" + "a" * 64, bundle=None):
        return runner.container_command("docker", image, bundle or self.bundle,
            self.root / "out", self.manifest, "owned-test-container")
    def test_mutable_or_option_image_rejected(self):
        for image in ["runtime:latest", "--privileged@sha256:" + "a" * 64, "bad image@sha256:" + "a" * 64]:
            with self.assertRaisesRegex(ValueError, "pinned"): self.command(image)
    def test_mount_delimiter_rejected(self):
        with self.assertRaisesRegex(ValueError, "mount paths"):
            self.command(bundle=Path("/tmp/source,dst=/dev/dri"))
    def test_timeout_cleans_only_owned_container_and_process(self):
        class Process:
            pid = 456789
            calls = 0
            def wait(self, timeout):
                self.calls += 1
                if self.calls == 1:
                    raise subprocess.TimeoutExpired("fixture", timeout)
                return 0
            def poll(self): return None
        args = SimpleNamespace(bundle=self.bundle, manifest=self.path,
            image="registry.invalid/runtime@sha256:" + "a" * 64,
            out=self.root / "timed-out", runtime="docker", timeout=5)
        with mock.patch.object(runner.subprocess, "Popen", return_value=Process()) as start, \
             mock.patch.object(runner.subprocess, "run", return_value=SimpleNamespace(returncode=0)) as cleanup, \
             mock.patch.object(runner.os, "killpg") as kill:
            self.assertEqual(runner.run(args), 1)
        launched = start.call_args.args[0]
        owned_name = launched[launched.index("--name") + 1]
        self.assertEqual(cleanup.call_args.args[0], ["docker", "rm", "--force", owned_name])
        kill.assert_called_once_with(Process.pid, signal.SIGTERM)
        receipt = json.loads((args.out / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "timeout")
        self.assertFalse(receipt["passed"])
    def test_container_has_only_owned_mounts_and_clean_environment(self):
        command = self.command()
        for flag in ["--pull=never", "--network=none", "--read-only", "--cap-drop=ALL",
                     "--security-opt=no-new-privileges", "--user=65534:65534", "--memory=1g"]:
            self.assertIn(flag, command)
        self.assertFalse(any(x.startswith("--device") or x == "--privileged" for x in command))
        self.assertEqual(command.count("--mount"), 2)
        self.assertIn(f"type=bind,src={self.bundle},dst=/provider,readonly", command)
        self.assertIn("--entrypoint=/usr/bin/env", command)
        self.assertIn("-i", command)
        self.assertIn("VK_DRIVER_FILES=/provider/vk_swiftshader_icd.json", command)
        self.assertNotIn("--enable-unsafe-webgpu", command)

if __name__ == "__main__":
    unittest.main()
