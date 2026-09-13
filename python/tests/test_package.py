from __future__ import annotations

import asyncio
import base64
import hashlib
import importlib
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from typing import Any
from unittest import mock

# ``python -m unittest discover python/tests`` starts at the repository root;
# make the checkout package importable without requiring an editable install.
PACKAGE_ROOT = Path(__file__).resolve().parents[1]
if str(PACKAGE_ROOT) not in sys.path:
    sys.path.insert(0, str(PACKAGE_ROOT))

from apostate import (  # noqa: E402
    CATALOGUE_VERSION,
    CHROMIUM_VERSION,
    PROFILE_SCHEMA_VERSION,
    BinaryManager,
    ConfigurationError,
    LaunchError,
    ProfileError,
    SignatureVerificationError,
    UnpublishedArtifactError,
    launch_async,
    launch_persistent_context,
    resolve_profile,
    target_platform,
    translate_options,
)
from apostate import binary as binary_module  # noqa: E402
from apostate.binary import canonical_manifest_bytes  # noqa: E402


class _FakeAsyncChromium:
    def __init__(self) -> None:
        self.options: dict[str, Any] | None = None

    async def launch(self, **options: Any) -> object:
        self.options = options
        return object()


class _FakeAsyncPlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeAsyncChromium()

    async def start(self) -> "_FakeAsyncPlaywright":
        return self

    async def stop(self) -> None:
        return None


class _FakeSyncChromium:
    def __init__(self) -> None:
        self.options: dict[str, Any] | None = None

    def launch_persistent_context(self, **options: Any) -> dict[str, Any]:
        self.options = options
        return options


class _FakeSyncPlaywright:
    def __init__(self) -> None:
        self.chromium = _FakeSyncChromium()

    def start(self) -> "_FakeSyncPlaywright":
        return self

    def stop(self) -> None:
        return None


class PackageContractTests(unittest.TestCase):
    def test_translate_options_uses_canonical_snake_case_and_stable_seed(self) -> None:
        config = translate_options(
            fingerprint="stable-seed:1",
            fingerprint_platform="win32",
            locale="en-US",
            timezone="America/New_York",
            geoip=False,
            proxy={"server": "http://proxy.example:8080", "bypass": "localhost"},
            headless=False,
            user_data_dir=Path("~/apostate-profile"),
            args=("--one", "--two"),
        )
        self.assertEqual(config.fingerprint, "stable-seed:1")
        self.assertEqual(config.fingerprint_platform, "windows")
        self.assertEqual(config.user_data_dir, str(Path("~/apostate-profile").expanduser()))
        self.assertEqual(config.to_dict()["fingerprint_platform"], "windows")
        self.assertEqual(config.args, ("--one", "--two"))
        with self.assertRaises(ConfigurationError):
            translate_options(fingerprint=-1)
        with self.assertRaises(ConfigurationError):
            translate_options(fingerprint="unstable seed")
        with self.assertRaises(ConfigurationError):
            translate_options(fingerprint=True)

    def test_packaged_profile_schema_matches_authoritative_schema(self) -> None:
        packaged = PACKAGE_ROOT / "apostate" / "assets" / "profile.schema.json"
        authoritative = PACKAGE_ROOT.parent / "config" / "profile.schema.json"
        self.assertEqual(packaged.read_bytes(), authoritative.read_bytes())

    def test_installed_package_assets_resolve_without_source_resources(self) -> None:
        package_dir = PACKAGE_ROOT / "apostate"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            staged_site = root / "site"
            shutil.copytree(package_dir, staged_site / "apostate")
            script = """
from apostate.config import translate_options
from apostate.profile_validation import validate_profile
from apostate.resolver import DeterministicResolver
resolution = DeterministicResolver().resolve(
    translate_options(fingerprint='staged-seed', fingerprint_platform='macos')
)
validate_profile(resolution.profile)
assert resolution.profile['platform']['navigator_platform'] == 'MacIntel'
assert resolution.profile['gpu']['unmasked_vendor'] == 'Google Inc. (Apple)'
print(resolution.profile_id)
"""
            environment = os.environ.copy()
            environment["PYTHONPATH"] = str(staged_site)
            result = subprocess.run(
                [sys.executable, "-c", script],
                cwd=root,
                env=environment,
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertTrue(result.stdout.strip().startswith("apple-metal-"), result.stdout)

    def test_package_resolve_profile_selects_catalogue_family(self) -> None:
        resolution = resolve_profile(fingerprint="package-selection", fingerprint_platform="macos")
        self.assertTrue(resolution.profile_id.startswith("apple-metal-"))
        self.assertEqual(resolution.profile["platform"]["navigator_platform"], "MacIntel")
        self.assertEqual(resolution.browser_version, CHROMIUM_VERSION)

    def test_acceptance_rejects_forged_digest_family_tamper_and_raw_paths(self) -> None:
        source = PACKAGE_ROOT.parent / "resources" / "profiles"
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            shutil.copy2(source / "catalogue.json", root / "catalogue.json")
            shutil.copy2(source / "compatibility-acceptance.json", root / "compatibility-acceptance.json")
            shutil.copytree(source / "families", root / "families")

            acceptance_path = root / "compatibility-acceptance.json"
            acceptance = json.loads(acceptance_path.read_text(encoding="utf-8"))
            acceptance["families"][0]["source"]["normalized_source_sha256"] = "0" * 64
            acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
            with self.assertRaises(ProfileError):
                resolve_profile(profile="apple-metal-m2", fingerprint_platform="macos", catalogue=root / "catalogue.json")

            acceptance["families"][0]["source"]["normalized_source_sha256"] = "3f58ac378cf5559e509023a976b47d06f513eeaae687dc5312f8299cd08ccd3d"
            acceptance["families"][0]["known_limitations"][0] = "captures/raw/apple-metal-m2.json"
            acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
            with self.assertRaises(ProfileError):
                resolve_profile(profile="apple-metal-m2", fingerprint_platform="macos", catalogue=root / "catalogue.json")

            acceptance["families"][0]["known_limitations"][0] = "WebGL limits remain host-clamped."
            acceptance_path.write_text(json.dumps(acceptance), encoding="utf-8")
            family_path = root / "families" / "apple-metal-m2.json"
            family = json.loads(family_path.read_text(encoding="utf-8"))
            family["profile"]["gpu"]["unmasked_renderer"] = "tampered"
            family_path.write_text(json.dumps(family), encoding="utf-8")
            with self.assertRaises(ProfileError):
                resolve_profile(profile="apple-metal-m2", fingerprint_platform="macos", catalogue=root / "catalogue.json")

    @staticmethod
    def _catalogue(family: dict[str, Any]) -> dict[str, Any]:
        return {
            "catalogue_version": CATALOGUE_VERSION,
            "profile_schema_version": PROFILE_SCHEMA_VERSION,
            "browser_build": CHROMIUM_VERSION,
            "families": [family],
        }

    @staticmethod
    def _family(family_id: str = "test-family", platform: str = "macos") -> dict[str, Any]:
        profile_name = "macOS" if platform == "macos" else "Windows" if platform == "windows" else "Linux"
        return {
            "id": family_id,
            "file": f"families/{family_id}.json",
            "platform": platform,
            "gpu_family": "test-gpu",
            "evidence_class": "compatibility-capture",
            "provenance": "focused package test",
            "anchor": {"gpu_vendor_renderer": "compatibility-capture"},
            "profile": {"id": family_id, "platform": {"name": profile_name}},
        }

    def test_family_file_path_is_direct_confined_json_and_not_symlinked(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            families = root / "families"
            families.mkdir()
            family = self._family()
            (families / "test-family.json").write_text(json.dumps(family), encoding="utf-8")
            catalogue_path = root / "catalogue.json"
            for unsafe in (
                "../test-family.json",
                str((families / "test-family.json").resolve()),
                "families/nested/test-family.json",
                "families/test-family.txt",
            ):
                indexed = dict(family, file=unsafe)
                catalogue_path.write_text(json.dumps(self._catalogue(indexed)), encoding="utf-8")
                with self.subTest(path=unsafe), self.assertRaises(ProfileError):
                    resolve_profile(profile="test-family", fingerprint_platform="macos", catalogue=catalogue_path)

            outside = root / "outside.json"
            outside.write_text(json.dumps(family), encoding="utf-8")
            direct = families / "test-family.json"
            direct.unlink()
            try:
                direct.symlink_to(outside)
            except (OSError, NotImplementedError):
                self.skipTest("symbolic links are unavailable")
            catalogue_path.write_text(json.dumps(self._catalogue(family)), encoding="utf-8")
            with self.assertRaises(ProfileError):
                resolve_profile(profile="test-family", fingerprint_platform="macos", catalogue=catalogue_path)

    def test_family_metadata_is_bound_and_exposed_only_in_resolution(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / "families").mkdir()
            family = self._family()
            family_file = dict(family, gpu_family="other-gpu")
            (root / "families" / "test-family.json").write_text(json.dumps(family_file), encoding="utf-8")
            catalogue_path = root / "catalogue.json"
            catalogue_path.write_text(json.dumps(self._catalogue(family)), encoding="utf-8")
            with self.assertRaises(ProfileError):
                resolve_profile(profile="test-family", fingerprint_platform="macos", catalogue=catalogue_path)

            (root / "families" / "test-family.json").write_text(json.dumps(family), encoding="utf-8")
            resolution = resolve_profile(profile="test-family", fingerprint_platform="macos", catalogue=catalogue_path)
            diagnostics = resolution.to_dict()
            self.assertEqual(diagnostics["gpu_family"], "test-gpu")
            self.assertEqual(diagnostics["profile_schema_version"], PROFILE_SCHEMA_VERSION)
            self.assertEqual(diagnostics["anchor"], family["anchor"])
            self.assertNotIn("gpu_family", resolution.profile)
            self.assertNotIn("provenance", resolution.profile)

    def test_explicit_mapping_and_file_platform_must_match_request(self) -> None:
        profile = {"id": "explicit", "platform": {"name": "Windows"}}
        with self.assertRaises(ProfileError):
            resolve_profile(profile=profile, fingerprint_platform="macos")
        with tempfile.NamedTemporaryFile("w", suffix=".json", encoding="utf-8") as profile_file:
            json.dump(profile, profile_file)
            profile_file.flush()
            with self.assertRaises(ProfileError):
                resolve_profile(profile=profile_file.name, fingerprint_platform="macos")

    def test_native_payload_strips_source_capture_without_mutating_diagnostics(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        profile = {"id": "explicit", "source_capture": "capture.json", "platform": {"name": "macOS"}}
        plan = launch_module._resolve_plan(
            translate_options(fingerprint_platform="macos", geoip=False),
            resolver=lambda config: profile,
        )
        argument = next(item for item in launch_module._native_args(plan) if item.startswith("--apostate-profile="))
        native = json.loads(base64.b64decode(argument.split("=", 1)[1]))
        self.assertEqual(native["id"], "explicit")
        self.assertNotIn("source_capture", native)
        self.assertEqual(plan.profile["source_capture"], "capture.json")
        self.assertEqual(plan.diagnostics["profile_id"], "explicit")

    def test_unpublished_package_manifest_fails_before_signature_or_download(self) -> None:
        calls: list[str] = []
        manifest = {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
            "artifacts": {},
            "signature": None,
            "status": "unpublished",
        }
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary, manifest=manifest, downloader=lambda source: calls.append(source))
            with mock.patch.object(binary_module, "verify_manifest") as verify:
                with self.assertRaisesRegex(UnpublishedArtifactError, "unpublished"):
                    manager.ensure(target="macos-arm64")
            verify.assert_not_called()
        self.assertEqual(calls, [])
    def test_canonical_manifest_vector_has_sorted_compact_utf8_and_one_lf(self) -> None:
        manifest = {"z": "café", "signature": "ignored", "a": 1}
        expected = b'{"a":1,"signature":null,"z":"caf\\u00e9"}\n'
        self.assertEqual(canonical_manifest_bytes(manifest), expected)
        self.assertEqual(canonical_manifest_bytes(manifest).count(b"\n"), 1)

    def test_invalid_signature_is_rejected_before_download(self) -> None:
        calls: list[str] = []
        manifest = {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
            "platform": "macos-arm64",
            "artifact": "apostate-test.zip",
            "sha256": hashlib.sha256(b"archive").hexdigest(),
            "signature": base64.b64encode(b"\0" * 64).decode("ascii"),
        }
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(
                cache_dir=temporary,
                manifest=manifest,
                downloader=lambda source: calls.append(source),
            )
            with self.assertRaises(SignatureVerificationError):
                manager.ensure(target="macos-arm64")
        self.assertEqual(calls, [])

    def test_cache_paths_and_clear_cache_are_deterministic(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            cache = Path(temporary) / "cache"
            manager = BinaryManager(cache_dir=cache)
            root, archive, binary = manager._paths(
                "linux-x64",
                {"chromium_version": CHROMIUM_VERSION},
                {"artifact": "https://downloads.example/apostate-linux-x64.tar.zst"},
            )
            self.assertEqual(root, cache / CHROMIUM_VERSION / "linux-x64")
            self.assertEqual(archive.name, "apostate-linux-x64.tar.zst")
            self.assertEqual(binary, root / "binary")
            (cache / "stale").mkdir(parents=True)
            manager.clear()
            self.assertFalse(cache.exists())
        self.assertEqual(target_platform("darwin-arm64"), "macos-arm64")

    def test_tampered_cached_binary_is_rebuilt_from_verified_archive(self) -> None:
        archive_buffer = io.BytesIO()
        with zipfile.ZipFile(archive_buffer, "w") as archive:
            archive.writestr("Chromium.app/Contents/MacOS/Chromium", b"native binary")
        archive_bytes = archive_buffer.getvalue()
        manifest = {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
            "platform": "macos-arm64",
            "artifact": "apostate-test.zip",
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
            "signature": "bypass-in-test",
        }
        with tempfile.TemporaryDirectory() as temporary:
            calls: list[str] = []
            manager = BinaryManager(
                cache_dir=temporary,
                manifest=manifest,
                downloader=lambda source: calls.append(source) or archive_bytes,
            )
            with mock.patch.object(binary_module, "verify_manifest"):
                executable = manager.ensure(target="macos-arm64")
                executable.write_bytes(b"tampered")
                marker = executable.parent / "verified.json"
                marker_data = json.loads(marker.read_text(encoding="utf-8"))
                marker_data["binary_sha256"] = hashlib.sha256(b"tampered").hexdigest()
                marker.write_text(json.dumps(marker_data), encoding="utf-8")
                rebuilt = manager.ensure(target="macos-arm64")
            self.assertEqual(rebuilt.read_bytes(), b"native binary")
            self.assertEqual(calls, ["apostate-test.zip"])

    def test_async_launch_delegates_to_async_backend(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        fake = _FakeAsyncPlaywright()
        with tempfile.NamedTemporaryFile() as executable:
            os.chmod(executable.name, 0o755)
            with mock.patch.object(launch_module, "_load_async_backend", return_value=lambda: fake):
                result = asyncio.run(
                    launch_async(
                        fingerprint="async-seed",
                        fingerprint_platform="macos",
                        geoip=False,
                        binary_path=executable.name,
                        resolver=lambda config: {},
                    )
                )
        self.assertIsNotNone(result)
        assert fake.chromium.options is not None
        self.assertEqual(fake.chromium.options["executable_path"], executable.name)
        self.assertTrue(any(argument.startswith("--apostate-profile=") for argument in fake.chromium.options["args"]))

    def test_missing_binary_fails_honestly(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            missing = Path(temporary) / "does-not-exist"
            with self.assertRaises(LaunchError):
                asyncio.run(
                    launch_async(
                        geoip=False,
                        binary_path=missing,
                        resolver=lambda config: {},
                    )
                )

    def test_persistent_context_creates_user_data_dir(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        fake = _FakeSyncPlaywright()
        with tempfile.TemporaryDirectory() as temporary:
            profile_path = Path(temporary) / "profile"
            with tempfile.NamedTemporaryFile() as executable:
                os.chmod(executable.name, 0o755)
                with mock.patch.object(launch_module, "_load_sync_backend", return_value=lambda: fake):
                    context = launch_persistent_context(
                        profile_path,
                        geoip=False,
                        binary_path=executable.name,
                        resolver=lambda config: {},
                    )
            self.assertTrue(profile_path.is_dir())
            self.assertEqual(context["user_data_dir"], str(profile_path))
            assert fake.chromium.options is not None
            self.assertEqual(fake.chromium.options["user_data_dir"], str(profile_path))


if __name__ == "__main__":
    unittest.main()
