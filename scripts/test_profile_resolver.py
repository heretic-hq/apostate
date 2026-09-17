#!/usr/bin/env python3
"""Tests for the anchor-and-dispersion compositor at catalogue version 2."""

from __future__ import annotations

import copy
import hashlib
import json
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections import Counter
from pathlib import Path

try:
    from . import profile_resolver as resolver
except ImportError:
    import profile_resolver as resolver


BASE_CONFIG = {
    "fingerprint": 12345,
    "fingerprint_platform": "windows",
    "browser_build": "152.0.7977.83",
    "host_platform": "windows",
    "host_backend": "ANGLE/D3D11",
    "host_logical_cores": 32,
    "host_total_bytes": 64 * 1024 ** 3,
}


def _mirror_catalogue(destination: Path) -> Path:
    """Copy the catalogue and its dispersion directory so a test may mutate them."""
    source = resolver.DEFAULT_CATALOGUE.parent
    shutil.copytree(source, destination / "profiles")
    return destination / "profiles" / "catalogue.json"


class CatalogueIntegrityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogue = resolver.load_catalogue()
        cls.tables = resolver.load_dispersion()

    def test_catalogue_axes_anchors_and_option_values_all_validate(self) -> None:
        self.assertTrue(resolver.validate_catalogue())
        self.assertEqual(2, self.catalogue["catalogue_version"])
        self.assertEqual("anchors+dispersion", self.catalogue["model"])
        self.assertEqual(list(resolver.AXES), [entry["axis"] for entry in self.catalogue["axes"]])
        for axis, table in self.tables.items():
            for option_set in table["option_sets"]:
                for option in option_set["options"]:
                    self.assertTrue(resolver.validate_profile(option["value"]),
                                    f"{axis}/{option['id']} value is not schema-valid")
                    self.assertIn(option["evidence"], resolver.SUPPORTED_EVIDENCE)

    def test_retired_family_model_is_gone(self) -> None:
        root = resolver.DEFAULT_CATALOGUE.parent
        self.assertFalse((root / "families").exists())
        self.assertFalse((root / "distributions").exists())
        self.assertFalse((root / "compatibility-acceptance.json").exists())
        for key in ("families", "family_count", "distributions", "compatibility_acceptance"):
            self.assertNotIn(key, self.catalogue)
        broken = copy.deepcopy(self.catalogue)
        broken["families"] = []
        with self.assertRaises(resolver.ResolverError):
            resolver._validate_catalogue_index(broken)

    def test_conditioned_option_sets_are_total_over_their_parents(self) -> None:
        platforms = sorted(resolver.PLATFORMS)
        releases: dict[str, list[str]] = {}
        for option_set in self.tables["os_release"]["option_sets"]:
            releases[option_set["key"]["platform"]] = [o["id"] for o in option_set["options"]]
        self.assertEqual(platforms, sorted(releases))
        for axis in ("furniture", "font_packs"):
            keys = {(s["key"]["platform"], s["key"]["os_release"])
                    for s in self.tables[axis]["option_sets"]}
            expected = {(platform, release)
                        for platform, ids in releases.items() for release in ids}
            self.assertEqual(expected, keys, f"{axis} option sets are not total")
        language_sets = set(self.catalogue["language_sets"])
        voice_keys = {(s["key"]["platform"], s["key"]["os_release"], s["key"]["languages"])
                      for s in self.tables["voices"]["option_sets"]}
        expected_voices = {(platform, release, languages)
                           for platform, ids in releases.items()
                           for release in ids for languages in language_sets}
        self.assertEqual(expected_voices, voice_keys)
        anchor_ids = {anchor["id"] for anchor in self.catalogue["anchors"]}
        self.assertEqual(anchor_ids,
                         {s["key"]["anchor"] for s in self.tables["gpu_identity"]["option_sets"]})

    def test_gpu_identity_offers_only_measured_members_of_its_anchor(self) -> None:
        members = {anchor["id"]: set(anchor["members"]) for anchor in self.catalogue["anchors"]}
        for option_set in self.tables["gpu_identity"]["option_sets"]:
            anchor_id = option_set["key"]["anchor"]
            self.assertEqual(len(members[anchor_id]), len(option_set["options"]))
            for option in option_set["options"]:
                self.assertEqual(anchor_id, option["requires"]["anchor"])
                renderer = option["value"]["gpu"]["unmasked_renderer"]
                self.assertTrue(any(device in renderer for device in members[anchor_id]),
                                f"{option['id']} renderer is not a member of {anchor_id}")
                self.assertEqual("physical-ground-truth", option["evidence"])

    def test_font_packs_carry_a_core_set_and_whole_bundles(self) -> None:
        for option_set in self.tables["font_packs"]["option_sets"]:
            kinds = Counter(option["pack_kind"] for option in option_set["options"])
            self.assertEqual(1, kinds["core"])
            self.assertGreater(kinds["optional"], 0)
            for option in option_set["options"]:
                families = option["value"]["fonts"]["enumeration_allowlist"]
                self.assertEqual(option["requires"]["families"], families)
                if option["pack_kind"] == "optional":
                    self.assertTrue(1 <= option["weight"] <= 100)

    def test_media_labels_are_platform_correct(self) -> None:
        for option_set in self.tables["media_topology"]["option_sets"]:
            platform = option_set["key"]["platform"]
            for option in option_set["options"]:
                for device in option["value"]["media"]["devices"]:
                    label = device["label"]
                    if "Realtek" in label:
                        self.assertEqual("windows", platform, f"{label} on {platform}")
                    if "MacBook" in label or "AirPods" in label or "Mac mini" in label:
                        self.assertEqual("macos", platform, f"{label} on {platform}")
                    if "Built-in Audio Analog Stereo" in label:
                        self.assertEqual("linux", platform, f"{label} on {platform}")

    def test_only_local_voices_are_offered(self) -> None:
        for option_set in self.tables["voices"]["option_sets"]:
            for option in option_set["options"]:
                for voice in option["value"].get("speech", {}).get("voices", []):
                    self.assertTrue(voice["local_service"],
                                    "network voices are a build capability, not a profile value")


class CompositionTests(unittest.TestCase):
    def test_resolution_is_byte_stable_and_the_payload_is_native_only(self) -> None:
        first = resolver.resolve_with_diagnostics(BASE_CONFIG)
        second = resolver.resolve_with_diagnostics(BASE_CONFIG)
        self.assertEqual(resolver._json_output(first).encode("utf-8"),
                         resolver._json_output(second).encode("utf-8"))
        self.assertTrue(resolver.validate_profile(first["profile"]))
        self.assertLessEqual(set(first["profile"]), resolver._PROFILE_TOP_LEVEL)
        self.assertEqual([], first["native_loader_support"]["unsupported_fields"])

    def test_the_profile_carries_the_loader_consumed_id_derived_from_the_root(self) -> None:
        """The loader reads `id` into source_id_, so the runtime payload needs it.

        It is derived from the composition root, not the profile digest: a field
        inside the profile cannot depend on a hash of the profile.
        """
        resolved = resolver.resolve_with_diagnostics(BASE_CONFIG)
        root = resolver.seed_root(str(BASE_CONFIG["fingerprint"]),
                                  BASE_CONFIG["fingerprint_platform"],
                                  BASE_CONFIG["browser_build"])
        self.assertEqual(f"fp-{root.hex()[:24]}", resolved["profile"]["id"])
        changed = resolver.resolve_with_diagnostics(dict(BASE_CONFIG, fingerprint=12346))
        self.assertNotEqual(resolved["profile"]["id"], changed["profile"]["id"])

    def test_identity_changes_with_every_component_of_the_seed_root(self) -> None:
        base = resolver.resolve_with_diagnostics(BASE_CONFIG)["diagnostics"]["identity"]
        for override in ({"fingerprint": 12346},
                         {"browser_build": "152.0.7977.82"},
                         {"fingerprint_platform": "macos"}):
            changed = resolver.resolve_with_diagnostics(dict(BASE_CONFIG, **override))
            self.assertNotEqual(base, changed["diagnostics"]["identity"], override)
        root = resolver.seed_root("1", "windows", "152.0.7977.83", 2, 3)
        self.assertNotEqual(root, resolver.seed_root("1", "windows", "152.0.7977.83", 3, 3))
        self.assertNotEqual(root, resolver.seed_root("1", "windows", "152.0.7977.83", 2, 4))

    def test_adding_a_pack_at_the_end_shifts_nothing_else(self) -> None:
        """The seed-stability claim: axis substreams are independent."""
        before = resolver.resolve_with_diagnostics(BASE_CONFIG)["diagnostics"]["axes"]
        with tempfile.TemporaryDirectory() as tmp:
            catalogue_path = _mirror_catalogue(Path(tmp))
            table_path = catalogue_path.parent / "dispersion" / "font_packs.json"
            table = json.loads(table_path.read_text(encoding="utf-8"))
            for option_set in table["option_sets"]:
                option_set["options"].append({
                    "id": "test-extra-pack", "weight": 50, "evidence": "catalogue-value",
                    "pack_kind": "optional", "requires": {"families": ["Test Family"]},
                    "value": {"fonts": {"enumeration_allowlist": ["Test Family"]}},
                })
            table_path.write_text(json.dumps(table, sort_keys=True, separators=(",", ":")) + "\n",
                                  encoding="utf-8")
            catalogue = json.loads(catalogue_path.read_text(encoding="utf-8"))
            for entry in catalogue["axes"]:
                if entry["axis"] == "font_packs":
                    entry["options"] += len(table["option_sets"])
            catalogue_path.write_text(
                json.dumps(catalogue, sort_keys=True, separators=(",", ":")) + "\n",
                encoding="utf-8")
            after = resolver.resolve_with_diagnostics(
                dict(BASE_CONFIG, catalogue_path=catalogue_path))["diagnostics"]["axes"]
        for axis in before:
            if axis == "font_packs":
                continue
            self.assertEqual(before[axis], after[axis], f"{axis} shifted")
        self.assertEqual(before["font_packs"]["options"],
                         [i for i in after["font_packs"]["options"] if i != "test-extra-pack"])

    def test_capacity_is_only_ever_reduced(self) -> None:
        small = resolver.resolve_with_diagnostics(dict(
            BASE_CONFIG, host_logical_cores=4, host_total_bytes=8 * 1024 ** 3))
        self.assertLessEqual(small["profile"]["cpu"]["logical_cores"], 4)
        self.assertLessEqual(small["profile"]["memory"]["total_bytes"], 8 * 1024 ** 3)
        for seed in range(40):
            resolved = resolver.resolve_profile(dict(
                BASE_CONFIG, fingerprint=seed, host_logical_cores=6,
                host_total_bytes=16 * 1024 ** 3))
            self.assertLessEqual(resolved["cpu"]["logical_cores"], 6)
            self.assertLessEqual(resolved["memory"]["total_bytes"], 16 * 1024 ** 3)

    def test_an_axis_with_no_servable_option_falls_back_to_host_inheritance(self) -> None:
        """A host below every bucket must inherit, not receive the lowest bucket.

        The lowest macOS core bucket is 8, so on a two-core host "keep the
        lowest" would ship a claim above host capability.
        """
        tiny = resolver.resolve_with_diagnostics({
            "fingerprint": 3, "fingerprint_platform": "macos",
            "host_platform": "macos", "host_backend": "ANGLE/Metal",
            "host_logical_cores": 2, "host_total_bytes": 4 * 1024 ** 3,
            "window_width": 320, "window_height": 240,
        })
        self.assertNotIn("cpu", tiny["profile"])
        self.assertNotIn("memory", tiny["profile"])
        self.assertEqual([], tiny["diagnostics"]["axes"]["cpu"]["options"])
        self.assertEqual(["host-inherited"], tiny["diagnostics"]["axes"]["cpu"]["evidence"])
        self.assertTrue(any("stays host-inherited" in warning
                            for warning in tiny["diagnostics"]["warnings"]))
        self.assertNotIn("avail_inset_left", tiny["profile"].get("screen", {}))
        self.assertNotEqual({}, tiny["profile"].get("screen", None))

    def test_no_persona_host_or_seed_combination_leaves_a_table_gap(self) -> None:
        hosts = (("macos", "ANGLE/Metal"), ("windows", "ANGLE/D3D11"),
                 ("linux", "ANGLE/Vulkan"))
        for persona in sorted(resolver.PLATFORMS):
            for host_platform, backend in hosts:
                for seed in range(6):
                    profile = resolver.resolve_profile({
                        "fingerprint": seed, "fingerprint_platform": persona,
                        "host_platform": host_platform, "host_backend": backend,
                        "host_logical_cores": 16, "host_total_bytes": 32 * 1024 ** 3,
                        "window_width": 1024, "window_height": 700,
                    })
                    self.assertGreaterEqual(profile["screen"]["width"], 1024)
                    self.assertLessEqual(profile["cpu"]["logical_cores"], 16)

    def test_work_area_is_derived_from_the_panel_and_the_furniture_insets(self) -> None:
        for seed in range(25):
            screen = resolver.resolve_profile(dict(BASE_CONFIG, fingerprint=seed))["screen"]
            self.assertNotIn("avail_inset_left", screen)
            self.assertLessEqual(screen["avail_width"], screen["width"])
            self.assertLessEqual(screen["avail_height"], screen["height"])
            self.assertLessEqual(screen["avail_top"], screen["height"] - screen["avail_height"])

    def test_measured_furniture_reproduces_the_captured_work_area(self) -> None:
        resolved = resolver.resolve_with_diagnostics(dict(
            BASE_CONFIG, fingerprint="win11-measured", host_logical_cores=8,
            host_total_bytes=16 * 1024 ** 3))
        tables = resolver.load_dispersion()
        panel = next(option for option_set in tables["panel"]["option_sets"]
                     if option_set["key"]["platform"] == "windows"
                     for option in option_set["options"] if option["id"] == "fhd-1080p")
        furniture = next(option for option_set in tables["furniture"]["option_sets"]
                         if option_set["key"] == {"platform": "windows", "os_release": "windows-11"}
                         for option in option_set["options"] if option["id"] == "taskbar-bottom")
        screen, insets = panel["value"]["screen"], furniture["value"]["screen"]
        self.assertEqual(1920, screen["width"])
        self.assertEqual(1080, screen["height"])
        self.assertEqual(1032, screen["height"] - insets["avail_inset_top"]
                         - insets["avail_inset_bottom"])
        self.assertEqual(1920, screen["width"] - insets["avail_inset_left"]
                         - insets["avail_inset_right"])
        self.assertIsNotNone(resolved["profile"]["window"]["outer_inner_delta_height"])

    def test_the_anchor_capability_cluster_reaches_the_profile(self) -> None:
        """An identity string with the host's own tables under it is the retired
        catalogue's failure, so selecting an anchor must take the whole cluster."""
        resolved = resolver.resolve_with_diagnostics(BASE_CONFIG)
        profile, diagnostics = resolved["profile"], resolved["diagnostics"]
        for section in ("gl_extensions", "gl_limits", "gl_precisions"):
            self.assertIn(section, profile, f"{section} never reached the profile")
        self.assertGreater(len(profile["gl_extensions"]), 30)
        self.assertIn("MAX_TEXTURE_SIZE", profile["gl_limits"])
        self.assertEqual(12, len(profile["gl_precisions"]))
        self.assertEqual([diagnostics["anchor"]["id"]], diagnostics["axes"]["anchor"]["options"])
        self.assertTrue(resolver.validate_profile(profile))

    def test_every_anchor_member_yields_the_same_gl_cluster(self) -> None:
        catalogue, tables, records = resolver._load_catalogue()
        for option_set in tables["gpu_identity"]["option_sets"]:
            record = records[option_set["key"]["anchor"]]["record"]
            layers = [
                resolver._anchor_capability_layer(
                    record, option["value"]["gpu"]["unmasked_renderer"])
                for option in option_set["options"]
            ]
            reference = {key: layers[0][key] for key in
                         ("gl_extensions", "gl_limits", "gl_precisions") if key in layers[0]}
            for layer in layers[1:]:
                self.assertEqual(reference, {key: layer[key] for key in reference})

    def test_a_member_with_no_measured_adapter_leaves_webgpu_inherited(self) -> None:
        """WebGPU is not uniform inside the Linux/Vulkan anchor: two members
        reported an adapter and two returned none."""
        catalogue, tables, records = resolver._load_catalogue()
        anchor_id = next(a["id"] for a in catalogue["anchors"]
                         if a["backend"] == "ANGLE/Vulkan")
        record = records[anchor_id]["record"]
        options = next(s["options"] for s in tables["gpu_identity"]["option_sets"]
                       if s["key"]["anchor"] == anchor_id)
        present = absent = 0
        for option in options:
            layer = resolver._anchor_capability_layer(
                record, option["value"]["gpu"]["unmasked_renderer"])
            if "webgpu" in layer:
                present += 1
                self.assertIn(layer["webgpu"]["info"]["architecture"], {"lovelace"})
            else:
                absent += 1
        self.assertEqual((2, 2), (present, absent))
        with self.assertRaises(resolver.ResolverError):
            resolver._anchor_capability_layer(record, "ANGLE (NVIDIA, fabricated RTX 5090)")

    def test_a_nonintegral_point_size_endpoint_stays_unrepresented(self) -> None:
        catalogue, tables, records = resolver._load_catalogue()
        for anchor in catalogue["anchors"]:
            record = records[anchor["id"]]["record"]
            renderer = ((record["members"][0]["identity"])["webgl1"])["unmaskedRenderer"]
            limits = resolver._anchor_capability_layer(record, renderer)["gl_limits"]
            raw = record["capability_cluster"]["webgl1"]["parameters"]["ALIASED_POINT_SIZE_RANGE"]
            integral = all(float(endpoint).is_integer() for endpoint in raw)
            self.assertEqual(integral, "ALIASED_POINT_SIZE_RANGE_MAX" in limits,
                             f"{anchor['id']} point range {raw} handled wrongly")
            for value in limits.values():
                self.assertIsInstance(value, int)

    def test_cross_backend_clusters_are_not_interchangeable(self) -> None:
        catalogue, _, records = resolver._load_catalogue()
        signatures = {}
        for anchor in catalogue["anchors"]:
            record = records[anchor["id"]]["record"]
            renderer = ((record["members"][0]["identity"])["webgl1"])["unmaskedRenderer"]
            layer = resolver._anchor_capability_layer(record, renderer)
            signatures[anchor["id"]] = resolver._canonical_json(
                [layer["gl_extensions"], layer["gl_limits"]])
        self.assertEqual(len(signatures), len(set(signatures.values())),
                         "two anchors produced the same capability signature")

    def test_the_persona_never_moves_the_gpu_cluster(self) -> None:
        resolved = resolver.resolve_with_diagnostics(dict(
            BASE_CONFIG, fingerprint_platform="windows", host_platform="macos",
            host_backend="ANGLE/Metal"))
        anchor = resolved["diagnostics"]["anchor"]
        self.assertEqual("ANGLE/Metal", anchor["backend"])
        self.assertEqual("macos", anchor["platform"])
        self.assertEqual("Windows", resolved["profile"]["platform"]["name"])
        self.assertTrue(any("does not move the GPU cluster" in warning
                            for warning in resolved["diagnostics"]["warnings"]))
        with self.assertRaises(resolver.ResolverError):
            resolver.resolve_profile(dict(BASE_CONFIG, host_backend="ANGLE/OpenGL",
                                          host_platform="linux"))

    def test_realised_distribution_follows_the_table_weights(self) -> None:
        counts: Counter[str] = Counter()
        for seed in range(600):
            resolved = resolver.resolve_with_diagnostics(dict(BASE_CONFIG, fingerprint=seed))
            counts[resolved["diagnostics"]["axes"]["os_release"]["options"][0]] += 1
        table = next(option_set for option_set in resolver.load_dispersion()["os_release"]["option_sets"]
                     if option_set["key"]["platform"] == "windows")
        weights = {option["id"]: option["weight"] for option in table["options"]}
        self.assertEqual(set(weights), set(counts), "an option was never drawn")
        total_weight = sum(weights.values())
        for option_id, weight in weights.items():
            expected = 600 * weight / total_weight
            self.assertLess(abs(counts[option_id] - expected), 0.35 * expected + 12,
                            f"{option_id} realised {counts[option_id]}, expected ~{expected:.0f}")

    def test_weighted_pick_uses_cumulative_weight_without_modulo(self) -> None:
        options = [{"id": "a", "weight": 1}, {"id": "b", "weight": 999}]
        picks = Counter(options[resolver.weighted_pick(
            resolver.seed_root(str(seed), "windows", "152.0.7977.83"), "cpu", options)]["id"]
            for seed in range(400))
        self.assertGreater(picks["b"], picks["a"] * 20)
        self.assertEqual(0, resolver.weighted_pick(b"\x00" * 32, "cpu",
                                                   [{"id": "only", "weight": 3}]))

    def test_a_missing_option_set_is_a_table_defect_and_fails_the_launch(self) -> None:
        tables = resolver.load_dispersion()
        with self.assertRaises(resolver.ResolverError):
            resolver._option_set("furniture", tables["furniture"],
                                 {"platform": "windows", "os_release": "windows-7"})

    def test_language_key_is_a_total_projection_computed_before_the_draw(self) -> None:
        table = resolver.load_dispersion()["voices"]
        self.assertEqual("en-US,en", resolver._language_key("en-US,en", table))
        self.assertEqual("", resolver._language_key("fr-FR,fr", table))
        self.assertEqual("", resolver._language_key(None, table))
        resolved = resolver.resolve_profile(dict(BASE_CONFIG, locale_policy="en-au"))
        self.assertNotIn("speech", resolved)

    def test_explicit_profile_file_is_validated_and_marked_as_a_bypass(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            good = Path(tmp) / "good.json"
            good.write_text(json.dumps({"id": "explicit", "cpu": {"logical_cores": 4}}),
                            encoding="utf-8")
            resolved = resolver.resolve_with_diagnostics({"profile_file": good})
            self.assertEqual("explicit-profile-file", resolved["diagnostics"]["source"])
            bad = Path(tmp) / "bad.json"
            bad.write_text(json.dumps({"id": "bad", "not_a_profile_field": True}),
                           encoding="utf-8")
            with self.assertRaises(resolver.ResolverError):
                resolver.resolve_with_diagnostics({"profile_file": bad})

    def test_unrecognised_requires_key_is_rejected(self) -> None:
        with self.assertRaises(resolver.ResolverError):
            resolver._validate_requires("cpu", "cores-8", {"min_threads": 8})

    # Chromium's JSON writer emits raw UTF-8 for non-ASCII
    # (base/json/string_escape.cc WriteUnicodeCharacter), so the byte-comparable
    # encoding is ensure_ascii=False.
    GOLDEN_HOST = {"host_platform": "macos", "host_backend": "ANGLE/Metal",
                   "host_logical_cores": 14, "host_total_bytes": 38654705664}
    GOLDEN_PROFILES = {
        "windows": ("fp-b0b97b3a3531b65ee50f45fc", 17,
                    "5f9b72f3d7d243bee90353008e839937ea4d0b253a3299a3f060efed96e5b042"),
        "macos": ("fp-60eab51485a4a8465ce3c24a", 17,
                  "7757d9370957f5a9bde47258d540f2772da0134d86d7e62c014dddcdc7580683"),
        "linux": ("fp-8c5f63da9ef88ea749549a91", 16,
                  "bc61c1eb8e3ba852222f5955896c20d4f0d7d8bc80e71713051e7260b169f342"),
    }

    def test_every_persona_matches_the_native_compositor_byte_for_byte(self) -> None:
        """Determinism gate (FINGERPRINTS section 9.1), all three personas.

        These digests were produced independently by the C++ compositor in the
        browser process. macOS is the load-bearing one: its speech.voices table
        carries non-ASCII names, so that digest is what proves the two writers
        agree on escaping rather than merely on field values.
        """
        for persona, (profile_id, sections, digest) in self.GOLDEN_PROFILES.items():
            profile = resolver.resolve_profile(dict(
                self.GOLDEN_HOST, fingerprint=12345, fingerprint_platform=persona,
                browser_build="152.0.7977.83"))
            encoded = json.dumps(profile, sort_keys=True, separators=(",", ":"),
                                 ensure_ascii=False).encode("utf-8")
            self.assertEqual(profile_id, profile["id"], persona)
            self.assertEqual(sections, len(profile), persona)
            self.assertEqual(digest, hashlib.sha256(encoded).hexdigest(), persona)
        macos = resolver.resolve_profile(dict(
            self.GOLDEN_HOST, fingerprint=12345, fingerprint_platform="macos",
            browser_build="152.0.7977.83"))
        self.assertTrue(any(ord(char) > 127 for voice in macos["speech"]["voices"]
                            for char in voice["name"]),
                        "the macOS digest only proves escaping agreement if it has non-ASCII")

    def test_no_option_value_contains_a_character_the_two_writers_escape_differently(self) -> None:
        """Keeps the cross-implementation digest comparison valid as tables grow.

        Chromium escapes `<` as \\u003C (a deliberate script-execution guard),
        U+2028 and U+2029; Python's json escapes none of them. A profile value
        containing one would diverge byte-wise between the two writers without
        being non-ASCII, so it would not show up as a mojibake-style failure.
        """
        divergent = ("<", "\u2028", "\u2029")

        def scan(value: object, where: str) -> list[str]:
            if isinstance(value, dict):
                return [hit for key, item in value.items()
                        for hit in scan(key, where) + scan(item, f"{where}.{key}")]
            if isinstance(value, list):
                return [hit for index, item in enumerate(value)
                        for hit in scan(item, f"{where}[{index}]")]
            if isinstance(value, str):
                return [f"{where}: {char!r}" for char in divergent if char in value]
            return []

        hits: list[str] = []
        for axis, table in resolver.load_dispersion().items():
            for option_set in table["option_sets"]:
                for option in option_set["options"]:
                    hits.extend(scan(option["value"], f"{axis}/{option['id']}"))
        for kind in resolver._POLICY_KINDS:
            for entry in resolver.load_catalogue()["policies"][kind]:
                hits.extend(scan(entry["value"], f"policy.{kind}/{entry['id']}"))
        self.assertEqual([], hits)

    def test_golden_vectors_agreed_with_the_native_compositor(self) -> None:
        """Determinism gate (FINGERPRINTS section 9.1).

        These three values were produced independently by the C++ compositor in
        the browser process and by this module. Pinning them here means a drift
        in either implementation, or in the option tables the profile is drawn
        from, fails loudly on both sides instead of silently diverging.
        """
        root = resolver.seed_root("12345", "windows", "152.0.7977.83", 2, 3)
        self.assertEqual(
            "b0b97b3a3531b65ee50f45fc56ea165e625bc8e6a8248f1afccb25d6308db8c8",
            root.hex())
        self.assertEqual(0x19d1abcfc04392c7, resolver.draw(root, "cpu", 0))
        profile = resolver.resolve_profile({
            "fingerprint": 12345, "fingerprint_platform": "windows",
            "browser_build": "152.0.7977.83", "host_platform": "macos",
            "host_backend": "ANGLE/Metal", "host_logical_cores": 14,
            "host_total_bytes": 38654705664,
        })
        self.assertEqual("fp-b0b97b3a3531b65ee50f45fc", profile["id"])
        self.assertEqual(17, len(profile))
        self.assertEqual(
            "5f9b72f3d7d243bee90353008e839937ea4d0b253a3299a3f060efed96e5b042",
            hashlib.sha256(
                resolver._canonical_json(profile).encode("utf-8")).hexdigest())

    def test_cli_operations_emit_json(self) -> None:
        script = Path(__file__).with_name("profile_resolver.py")
        for args in (["--catalogue"], ["--list"],
                     ["--resolve", "--fingerprint", "7", "--fingerprint-platform", "macos"],
                     ["--resolve", "--fingerprint", "7", "--runtime-only"]):
            completed = subprocess.run([sys.executable, str(script), *args],
                                       check=True, capture_output=True, text=True)
            self.assertIsInstance(json.loads(completed.stdout), dict)


if __name__ == "__main__":
    unittest.main()
