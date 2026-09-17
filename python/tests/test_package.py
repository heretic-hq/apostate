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
    UnpublishedArtifactError,
    launch_async,
    launch_persistent_context,
    load_catalogue,
    resolve_profile,
    target_platform,
    translate_options,
)


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
from apostate.errors import ProfileError
from apostate.profile_validation import validate_profile
from apostate.resolver import load_catalogue, resolve_profile
catalogue = load_catalogue()
assert catalogue['model'] == 'anchors+dispersion', catalogue['model']
assert catalogue['catalogue_version'] == 2, catalogue['catalogue_version']
validate_profile({'id': 'staged', 'platform': {'name': 'macOS'}})
try:
    resolve_profile(fingerprint='staged-seed', fingerprint_platform='macos')
except ProfileError as exc:
    assert 'in-binary compositor' in str(exc), str(exc)
else:
    raise AssertionError('a seed and persona must not compose inside the package')
print(catalogue['browser_build'])
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
            self.assertEqual(result.stdout.strip(), CHROMIUM_VERSION)

    def test_catalogue_exposes_the_version_two_anchor_and_dispersion_shape(self) -> None:
        catalogue = load_catalogue()
        self.assertEqual(catalogue["catalogue_version"], CATALOGUE_VERSION)
        self.assertEqual(catalogue["profile_schema_version"], PROFILE_SCHEMA_VERSION)
        self.assertEqual(catalogue["browser_build"], CHROMIUM_VERSION)
        self.assertEqual(catalogue["model"], "anchors+dispersion")
        for anchor in catalogue["anchors"]:
            self.assertEqual(
                set(anchor), {"id", "platform", "backend", "members", "rotation_status"}
            )
            self.assertIn(anchor["platform"], {"windows", "macos", "linux"})
            self.assertIn(anchor["rotation_status"], {"measured-safe", "single-member"})
            self.assertTrue(anchor["members"])
        self.assertIn(
            "macos-metal-apple-850a91233555", {anchor["id"] for anchor in catalogue["anchors"]}
        )
        self.assertEqual(
            [axis["axis"] for axis in catalogue["axes"]],
            ["os_release", "gpu_identity", "cpu", "memory", "panel", "furniture",
             "font_packs", "media_topology", "voices"],
        )
        anchor_axis = next(axis for axis in catalogue["axes"] if axis["axis"] == "gpu_identity")
        self.assertEqual(anchor_axis["conditioned_on"], ["anchor"])
        self.assertEqual(anchor_axis["servability"], "anchor-member")
        self.assertEqual(sorted(catalogue["policies"]), ["locale", "theme"])
        self.assertIn("en-us", catalogue["policies"]["locale"])

    @staticmethod
    def _catalogue(**overrides: Any) -> dict[str, Any]:
        catalogue = {
            "catalogue_id": "apostate",
            "catalogue_version": CATALOGUE_VERSION,
            "version": CATALOGUE_VERSION,
            "profile_schema_version": PROFILE_SCHEMA_VERSION,
            "browser_build": CHROMIUM_VERSION,
            "model": "anchors+dispersion",
            "anchors": [{
                "id": "macos-metal-apple-test", "platform": "macos", "backend": "ANGLE/Metal",
                "members": ["Apple M4 Max"], "member_count": 1, "rotation_status": "single-member",
            }],
            "axes": [
                {"axis": axis, "selection": "single", "servability": "none", "conditioned_on": []}
                for axis in ("os_release", "gpu_identity", "cpu", "memory", "panel",
                             "furniture", "font_packs", "media_topology", "voices")
            ],
            "policies": {
                "locale": [{"id": "en-us"}],
                "theme": [{"id": "light"}],
            },
        }
        catalogue.update(overrides)
        return catalogue

    def test_catalogue_rejects_version_disagreement_and_the_retired_family_model(self) -> None:
        self.assertEqual(load_catalogue(self._catalogue())["model"], "anchors+dispersion")
        for label, catalogue in (
            ("catalogue_version", self._catalogue(catalogue_version=1, version=1)),
            ("profile_schema_version", self._catalogue(profile_schema_version=2)),
            ("families", self._catalogue(families=[{"id": "apple-metal-m2"}])),
            ("family_count", self._catalogue(family_count=14)),
            ("compatibility_acceptance", self._catalogue(compatibility_acceptance={})),
            ("model", self._catalogue(model="families")),
        ):
            with self.subTest(rejected=label), self.assertRaises(ProfileError):
                load_catalogue(catalogue)

    def test_seed_persona_and_catalogue_ids_fail_closed_without_the_compositor(self) -> None:
        with self.assertRaisesRegex(ProfileError, "in-binary compositor"):
            resolve_profile(fingerprint=12345, fingerprint_platform="windows")
        with self.assertRaisesRegex(ProfileError, "in-binary compositor"):
            resolve_profile(fingerprint_platform="windows")
        # The documented default with no arguments is a drawn seed, so the bare
        # call is a composition request and must not fall back to the host.
        with self.assertRaisesRegex(ProfileError, "in-binary compositor"):
            resolve_profile()
        with self.assertRaisesRegex(ProfileError, "retired with catalogue version 1"):
            resolve_profile(profile="apple-metal-m2", fingerprint_platform="macos")

    def test_host_inheritance_is_explicit_and_sends_no_profile_envelope(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        resolution = resolve_profile(fingerprint="host")
        self.assertEqual(resolution.profile, {})
        self.assertEqual(resolution.profile_id, "host-inherited")
        self.assertEqual(resolution.catalogue_version, CATALOGUE_VERSION)
        plan = launch_module._resolve_plan(translate_options(fingerprint="host", geoip=False))
        self.assertFalse(
            [item for item in launch_module._native_args(plan) if item.startswith("--apostate-profile=")]
        )
        localized = launch_module._resolve_plan(
            translate_options(fingerprint="host", locale="en-GB,en", timezone="Europe/London", geoip=False)
        )
        self.assertEqual(
            localized.profile,
            {"locale": {"accept_languages": "en-GB,en", "timezone": "Europe/London"}},
        )
        argument = next(item for item in launch_module._native_args(localized)
                        if item.startswith("--apostate-profile="))
        self.assertEqual(
            json.loads(base64.b64decode(argument.split("=", 1)[1]))["locale"]["timezone"],
            "Europe/London",
        )
        with self.assertRaises(ProfileError):
            resolve_profile(fingerprint="host", fingerprint_platform="windows")

    def test_explicit_inline_profile_validates_and_reaches_the_native_envelope(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        plan = launch_module._resolve_plan(
            translate_options(profile={"id": "explicit", "platform": {"name": "macOS"}},
                              fingerprint_platform="macos", geoip=False)
        )
        argument = next(item for item in launch_module._native_args(plan)
                        if item.startswith("--apostate-profile="))
        self.assertEqual(
            json.loads(base64.b64decode(argument.split("=", 1)[1])),
            {"id": "explicit", "platform": {"name": "macOS"}},
        )
        self.assertEqual(plan.diagnostics["profile_id"], "explicit")
        self.assertEqual(plan.diagnostics["platform"], "macos")
        self.assertIn("bypasses composition", " ".join(plan.diagnostics["warnings"]))
        with self.assertRaises(ProfileError):
            resolve_profile(profile={"id": "explicit", "unsupported_section": {}})

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

    def test_unpublished_package_manifest_fails_before_download(self) -> None:
        calls: list[str] = []
        manifest = {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
            "artifacts": {},
            "status": "unpublished",
        }
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary, manifest=manifest, downloader=lambda source: calls.append(source))
            with self.assertRaisesRegex(UnpublishedArtifactError, "unpublished"):
                manager.ensure(target="macos-arm64")
        self.assertEqual(calls, [])

    def test_unpublished_platform_refuses_other_platform_artifacts(self) -> None:
        records = [{
            "platform": target,
            "artifact": f"apostate-{CHROMIUM_VERSION}-{target}.tar.zst",
            "sha256": "0" * 64,
        } for target in ("linux-x64", "linux-arm64", "macos-arm64")]
        identity = {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
        }
        calls: list[str] = []
        with tempfile.TemporaryDirectory() as temporary:
            for shape in (
                {"artifacts": {record["platform"]: record for record in records}},
                {"artifacts": records},
                records[0],
            ):
                with self.subTest(shape=shape):
                    manifest = {**identity, **shape}
                    manager = BinaryManager(cache_dir=temporary, manifest=manifest,
                                            downloader=lambda source: calls.append(source))
                    with self.assertRaisesRegex(UnpublishedArtifactError, "windows-x64.*not published for this release"):
                        manager.ensure(target="windows-x64")
        self.assertEqual(calls, [])

    def test_packaged_release_manifest_agrees_with_the_package_catalogue_version(self) -> None:
        # A stale catalogue_version here turns "no artifact is published yet"
        # into "this manifest is for another package", which is a lie.
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary)
            with self.assertRaisesRegex(UnpublishedArtifactError, "unpublished"):
                manager.ensure(target="macos-arm64")

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
        }
        with tempfile.TemporaryDirectory() as temporary:
            calls: list[str] = []
            manager = BinaryManager(
                cache_dir=temporary,
                manifest=manifest,
                downloader=lambda source: calls.append(source) or archive_bytes,
            )
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
                        profile={"id": "async-explicit", "platform": {"name": "macOS"}},
                        fingerprint_platform="macos",
                        geoip=False,
                        binary_path=executable.name,
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
