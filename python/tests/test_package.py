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

import apostate.resolver as resolver_module  # noqa: E402
from apostate import (  # noqa: E402
    CATALOGUE_VERSION,
    CHROMIUM_VERSION,
    PROFILE_SCHEMA_VERSION,
    BinaryManager,
    ConfigurationError,
    LaunchError,
    ProfileError,
    UnpublishedArtifactError,
    UnsupportedArchiveError,
    launch,
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

    def launch(self, **options: Any) -> dict[str, Any]:
        self.options = options
        return options

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
from apostate.profile_validation import validate_profile
from apostate.resolver import load_catalogue, resolve_profile
catalogue = load_catalogue()
assert catalogue['model'] == 'anchors+dispersion', catalogue['model']
assert catalogue['catalogue_version'] == 2, catalogue['catalogue_version']
validate_profile({'id': 'staged', 'platform': {'name': 'macOS'}})
# A seed and persona are handed to the browser process, which is the compositor;
# the package still composes nothing of its own, so the profile stays empty.
resolution = resolve_profile(fingerprint='staged-seed', fingerprint_platform='macos')
assert resolution.profile_id == 'native-composed', resolution.profile_id
assert resolution.profile == {}, resolution.profile
assert resolve_profile(fingerprint='host').profile_id == 'host-inherited'
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
        # The exact axis list is not restated here: load_catalogue() already
        # refuses a catalogue whose axes are not the contract's, in order, so an
        # equality assertion would only duplicate the loader. What is worth
        # pinning is that the PACKAGED catalogue satisfies it -- the packages
        # ship a copy of resources/profiles/catalogue.json, and a copy that went
        # stale against the resolver is the regression that actually happens.
        axes = [axis["axis"] for axis in catalogue["axes"]]
        self.assertEqual(axes, list(resolver_module._DISPERSION_AXES))
        self.assertEqual(len(axes), len(set(axes)))
        anchor_axis = next(axis for axis in catalogue["axes"] if axis["axis"] == "gpu_identity")
        self.assertEqual(anchor_axis["conditioned_on"], ["anchor"])
        self.assertEqual(anchor_axis["servability"], "anchor-member")
        self.assertEqual(sorted(catalogue["policies"]), ["locale", "theme"])
        self.assertIn("en-us", catalogue["policies"]["locale"])

    def test_catalogue_axes_must_be_complete_and_ordered(self) -> None:
        # A dropped or reordered axis means the package and the browser disagree
        # about what was composed, which is worse than refusing to launch.
        contract = list(resolver_module._DISPERSION_AXES)
        reordered = [contract[1], contract[0], *contract[2:]]
        for label, axes in (("reordered", reordered), ("truncated", contract[:-1]),
                            ("unknown-axis", [*contract[:-1], "not_an_axis"])):
            with self.subTest(rejected=label), self.assertRaises(ProfileError):
                load_catalogue(self._catalogue(axes=[
                    {"axis": axis, "selection": "single", "servability": "none", "conditioned_on": []}
                    for axis in axes
                ]))

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
                for axis in resolver_module._DISPERSION_AXES
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

    def test_seed_and_persona_reach_the_native_composition_switches(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        # The browser process is the compositor and the switches exist in the
        # shipped binary, so a seed is delivered rather than refused. Measured
        # on macos-arm64 152.0.7977.83: --fingerprint=42 yields en-GB /
        # Europe/London and repeats across launches.
        resolution = resolve_profile(fingerprint=12345, fingerprint_platform="windows")
        self.assertEqual(resolution.profile_id, "native-composed")
        self.assertEqual(resolution.profile, {})
        plan = launch_module._resolve_plan(
            translate_options(fingerprint=12345, fingerprint_platform="windows", geoip=False)
        )
        args = launch_module._native_args(plan)
        self.assertIn("--fingerprint=12345", args)
        self.assertIn("--fingerprint-platform=windows", args)
        # An envelope outranks the seed, so none may be sent alongside it.
        self.assertFalse([item for item in args if item.startswith("--apostate-profile=")])

        # A bare launch composes too, and says the identity will not persist.
        bare = resolve_profile()
        self.assertEqual(bare.profile_id, "native-composed")
        self.assertIn("does not persist", " ".join(bare.warnings))
        self.assertFalse([item for item in launch_module._native_args(
            launch_module._resolve_plan(translate_options(geoip=False))
        ) if item.startswith("--fingerprint=")])

        # Retired catalogue ids stay refused: there is no such thing to resolve.
        with self.assertRaisesRegex(ProfileError, "retired with catalogue version 1"):
            resolve_profile(profile="apple-metal-m2", fingerprint_platform="macos")

    def test_unknown_fingerprint_switch_is_refused_before_launch(self) -> None:
        # Chromium ignores an unknown switch silently, which would leave the
        # surface host-inherited while the caller believed it was set.
        launch_module = importlib.import_module("apostate.launch")
        plan = launch_module._resolve_plan(
            translate_options(fingerprint=7, args=["--fingerprint-gpu-vendr=Apple"], geoip=False)
        )
        with self.assertRaisesRegex(ConfigurationError, "not a switch this browser reads"):
            launch_module._native_args(plan)

    def test_host_inheritance_accepts_every_spelling_the_binary_accepts(self) -> None:
        for token in ("host", "off", "false", "0", "disable", "DISABLED"):
            with self.subTest(token=token):
                self.assertEqual(resolve_profile(fingerprint=token).profile_id, "host-inherited")
        self.assertEqual(resolve_profile(fingerprint=0).profile_id, "host-inherited")

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
            root, install, marker = manager._paths(
                "linux-x64",
                {"chromium_version": CHROMIUM_VERSION},
                {"artifact": "apostate-linux-x64.tar.zst"},
            )
            self.assertEqual(root, cache / CHROMIUM_VERSION / "linux-x64")
            self.assertEqual(install, root / "install")
            self.assertEqual(marker, root / "install.json")
            (cache / "stale").mkdir(parents=True)
            manager.clear()
            self.assertFalse(cache.exists())
        self.assertEqual(target_platform("darwin-arm64"), "macos-arm64")

    def test_install_keeps_the_whole_distribution_not_just_the_executable(self) -> None:
        # Chromium cannot start from a lone copied executable: it needs its
        # framework, ICU data and .pak resources. A cache that holds only the
        # executable is a cache that cannot launch.
        archive_bytes = self._zip_archive({
            "apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary",
            "apostate-test/Chromium.app/Contents/Resources/icudtl.dat": b"icu",
            "apostate-test/resources/en-US.pak": b"pak",
        })
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary,
                                    manifest=self._zip_manifest(archive_bytes),
                                    downloader=lambda source: archive_bytes)
            executable = manager.ensure(target="macos-arm64")
            install = Path(temporary) / CHROMIUM_VERSION / "macos-arm64" / "install"
            # The wrapper directory is stripped; everything inside it survives.
            self.assertEqual(executable, install / "Chromium.app/Contents/MacOS/Chromium")
            self.assertEqual((install / "Chromium.app/Contents/Resources/icudtl.dat").read_bytes(), b"icu")
            self.assertEqual((install / "resources/en-US.pak").read_bytes(), b"pak")
            self.assertTrue(os.access(executable, os.X_OK))

    def test_tampered_cached_binary_is_rebuilt(self) -> None:
        archive_bytes = self._zip_archive({
            "apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary",
        })
        with tempfile.TemporaryDirectory() as temporary:
            calls: list[str] = []
            manager = BinaryManager(
                cache_dir=temporary,
                manifest=self._zip_manifest(archive_bytes),
                downloader=lambda source: calls.append(source) or archive_bytes,
            )
            executable = manager.ensure(target="macos-arm64")
            executable.write_bytes(b"tampered")
            rebuilt = manager.ensure(target="macos-arm64")
            self.assertEqual(rebuilt.read_bytes(), b"native binary")
            self.assertEqual(len(calls), 2)

    def test_extraction_refuses_an_escaping_symlink_but_keeps_a_contained_one(self) -> None:
        # The macOS bundle reaches its framework through five relative symlinks,
        # so prohibition is not an option; containment is what is enforced.
        contained = self._zip_archive(
            {"apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary"},
            links={"apostate-test/Chromium.app/Contents/Frameworks/Current": "../MacOS"},
        )
        escaping = self._zip_archive(
            {"apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary"},
            links={"apostate-test/escape": "../../../../etc"},
        )
        absolute = self._zip_archive(
            {"apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary"},
            links={"apostate-test/absolute": "/etc/passwd"},
        )
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=Path(temporary) / "ok",
                                    manifest=self._zip_manifest(contained),
                                    downloader=lambda source: contained)
            executable = manager.ensure(target="macos-arm64")
            link = executable.parent.parent / "Frameworks" / "Current"
            self.assertTrue(link.is_symlink())
            self.assertEqual(os.readlink(link), "../MacOS")
            for label, payload in (("escaping", escaping), ("absolute", absolute)):
                with self.subTest(rejected=label):
                    rejecting = BinaryManager(cache_dir=Path(temporary) / label,
                                              manifest=self._zip_manifest(payload),
                                              downloader=lambda source, data=payload: data)
                    with self.assertRaises(UnsupportedArchiveError):
                        rejecting.ensure(target="macos-arm64")

    def _zip_archive(self, files: dict[str, bytes],
                     links: dict[str, str] | None = None) -> bytes:
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w") as archive:
            for name, payload in files.items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o100755 << 16)
                archive.writestr(info, payload)
            for name, target in (links or {}).items():
                info = zipfile.ZipInfo(name)
                info.create_system = 3
                info.external_attr = (0o120777 << 16)
                archive.writestr(info, target)
        return buffer.getvalue()

    def _zip_manifest(self, archive_bytes: bytes) -> dict[str, Any]:
        return {
            "package_version": "0.1.0",
            "chromium_version": CHROMIUM_VERSION,
            "catalogue_version": CATALOGUE_VERSION,
            "platform": "macos-arm64",
            "artifact": "apostate-test.zip",
            "sha256": hashlib.sha256(archive_bytes).hexdigest(),
        }

    def test_provisioned_widevine_survives_a_forced_reinstall(self) -> None:
        # The CDM is stored outside the install tree precisely so that
        # `install --force` and a Chromium upgrade, which both replace that
        # tree, do not silently remove DRM and turn a working launch into a
        # NotSupportedError a site can read in one call.
        from apostate import widevine
        archive_bytes = self._zip_archive({
            "apostate-test/Chromium.app/Contents/MacOS/Chromium": b"native binary",
        })
        with tempfile.TemporaryDirectory() as temporary:
            manager = BinaryManager(cache_dir=temporary,
                                    manifest=self._zip_manifest(archive_bytes),
                                    downloader=lambda source: archive_bytes)
            manager.ensure(target="macos-arm64")
            store = widevine.store_path(temporary)
            platform_dir = store / "_platform_specific" / "mac_arm64"
            platform_dir.mkdir(parents=True)
            (platform_dir / "libwidevinecdm.dylib").write_bytes(b"cdm")
            (store / "manifest.json").write_text('{"version": "4.10.3050.0"}', encoding="utf-8")

            install = Path(temporary) / CHROMIUM_VERSION / "macos-arm64" / "install"
            installed = widevine.apply_to_install(
                install, "macos-arm64", cache_dir=temporary,
                chromium_version=CHROMIUM_VERSION,
            )
            self.assertIsNotNone(installed)
            library = installed / "_platform_specific" / "mac_arm64" / "libwidevinecdm.dylib"
            self.assertEqual(library.read_bytes(), b"cdm")
            # No version directory: the browser reads the version from
            # manifest.json, and Google Chrome's own bundled copy has none.
            self.assertEqual(sorted(p.name for p in installed.iterdir()),
                             ["_platform_specific", "manifest.json"])

            manager.ensure(target="macos-arm64", force=True)
            self.assertEqual(library.read_bytes(), b"cdm")

    def test_widevine_provisioning_refuses_a_directory_without_a_library(self) -> None:
        from apostate.widevine import WidevineError, provision
        with tempfile.TemporaryDirectory() as temporary:
            empty = Path(temporary) / "WidevineCdm"
            empty.mkdir()
            with self.assertRaises(WidevineError):
                provision(target="macos-arm64", source=empty, cache_dir=temporary,
                          chromium_version=CHROMIUM_VERSION, install=Path(temporary) / "install")

    def test_component_update_switch_is_dropped_from_driver_defaults(self) -> None:
        # Playwright passes --disable-component-update by default, and it blocks
        # ComponentInstaller::Register outright, so a provisioned Widevine CDM is
        # silently inert. Measured: same install and same code, Patchright
        # resolved and Playwright rejected NotSupportedError. Patchright does not
        # pass the switch, which is why the first version of this feature passed
        # its own test and would still have failed for a plain-Playwright user.
        launch_module = importlib.import_module("apostate.launch")
        fake = _FakeSyncPlaywright()
        with tempfile.NamedTemporaryFile() as executable:
            os.chmod(executable.name, 0o755)
            with mock.patch.object(launch_module, "_load_sync_backend",
                                      return_value=launch_module.DriverSelection("patchright", lambda: fake)):
                launch(geoip=False, binary_path=executable.name, fingerprint="host")
        assert fake.chromium.options is not None
        self.assertIn("--disable-component-update", fake.chromium.options["ignore_default_args"])

    def test_an_explicitly_requested_component_update_switch_is_honoured(self) -> None:
        # Suppressing a switch the caller asked for would be the package
        # overriding an explicit decision; only the driver's default is removed.
        launch_module = importlib.import_module("apostate.launch")
        fake = _FakeSyncPlaywright()
        with tempfile.NamedTemporaryFile() as executable:
            os.chmod(executable.name, 0o755)
            with mock.patch.object(launch_module, "_load_sync_backend",
                                      return_value=launch_module.DriverSelection("patchright", lambda: fake)):
                launch(geoip=False, binary_path=executable.name, fingerprint="host",
                       args=["--disable-component-update"])
        assert fake.chromium.options is not None
        self.assertIsNone(fake.chromium.options["ignore_default_args"])
        self.assertIn("--disable-component-update", fake.chromium.options["args"])

    def test_patchright_is_the_default_driver_and_the_choice_is_inspectable(self) -> None:
        # The driver default is the package's to choose and is most of the point
        # of shipping a package rather than a bare binary: the browser patches
        # close the protocol-side tells, but nothing in the browser can remove
        # what a driver CREATES -- main-world bindings, Runtime.addBinding,
        # evaluation-script names in stack traces, its automation argv.
        launch_module = importlib.import_module("apostate.launch")
        self.assertEqual(launch_module.DRIVERS[0], "patchright")
        info = launch_module.driver_info()
        self.assertEqual(info["recommended"], "patchright")
        self.assertEqual(info["preference_order"], list(launch_module.DRIVERS))
        # Whatever is installed, the selection must be the first preference
        # present, never an arbitrary one.
        if info["installed"]:
            self.assertEqual(info["selected"], info["installed"][0])
            self.assertEqual(
                info["installed"],
                [name for name in launch_module.DRIVERS if name in info["installed"]],
            )
        else:
            self.assertIsNone(info["selected"])

    def test_an_unknown_driver_is_refused_by_name(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        with self.assertRaisesRegex(ConfigurationError, "unknown driver"):
            launch_module._load_sync_backend("selenium")

    def test_driver_default_viewport_is_not_allowed_to_overwrite_the_geometry(self) -> None:
        # Playwright's default context reports screen == inner == avail with
        # devicePixelRatio flattened to 1, and Puppeteer's reports an inner
        # viewport LARGER than its own window. No real machine does either.
        # Measured on macos-arm64 with --fingerprint=42: letting the real window
        # size through restored avail 1710x1079 against screen 1710x1112 and a
        # dpr of 2, on both drivers.
        launch_module = importlib.import_module("apostate.launch")
        recorded: dict[str, Any] = {}

        class _Browser:
            def new_page(self, **kwargs: Any) -> dict[str, Any]:
                recorded.update(kwargs)
                return kwargs

            def close(self) -> None:
                return None

        wrapped = launch_module._coherent_viewport(_Browser())
        wrapped.new_page()
        self.assertTrue(recorded["no_viewport"])
        # An explicit request is the caller's decision and must survive.
        recorded.clear()
        wrapped.new_page(viewport={"width": 1024, "height": 768})
        self.assertNotIn("no_viewport", recorded)
        self.assertEqual(recorded["viewport"], {"width": 1024, "height": 768})

    def test_a_context_closes_the_browser_it_was_created_from(self) -> None:
        # launch_context hands back a context, and closing a non-persistent
        # context does not close its browser. Without this the browser and its
        # driver outlive the context, and the driver's installed event loop
        # makes the next sync launch fail with "Sync API inside the asyncio
        # loop" -- found by exercising this path for the first time.
        launch_module = importlib.import_module("apostate.launch")
        closed = []

        class _Browser:
            def close(self) -> None:
                closed.append("browser")

        class _Context:
            def close(self) -> None:
                closed.append("context")

        context = launch_module._context_owns_browser(_Context(), _Browser())
        context.close()
        self.assertEqual(closed, ["context", "browser"])

    def test_async_launch_delegates_to_async_backend(self) -> None:
        launch_module = importlib.import_module("apostate.launch")
        fake = _FakeAsyncPlaywright()
        with tempfile.NamedTemporaryFile() as executable:
            os.chmod(executable.name, 0o755)
            with mock.patch.object(launch_module, "_load_async_backend",
                                      return_value=launch_module.DriverSelection("patchright", lambda: fake)):
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
                with mock.patch.object(launch_module, "_load_sync_backend",
                                      return_value=launch_module.DriverSelection("patchright", lambda: fake)):
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
