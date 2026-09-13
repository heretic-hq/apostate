#!/usr/bin/env python3
"""Focused tests for the Apostate-owned catalogue resolver."""

from __future__ import annotations

import copy
import json
import subprocess
import sys
import unittest
from pathlib import Path

try:
    from . import profile_resolver as resolver
except ImportError:
    import profile_resolver as resolver


class CatalogueResolverTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.catalogue = resolver.load_catalogue()
        cls.catalogue_path = resolver.DEFAULT_CATALOGUE

    def test_catalogue_integrity_loads_every_family_reference(self) -> None:
        self.assertTrue(resolver.validate_catalogue())
        families = resolver._as_families(self.catalogue, self.catalogue_path.parent)
        self.assertEqual(14, len(families))
        self.assertEqual(
            {entry["id"] for entry in self.catalogue["families"]},
            {family["id"] for family in families},
        )
        for family in families:
            self.assertEqual("compatibility-capture", family["evidence_class"])
            self.assertTrue(resolver.validate_profile(family["profile"]))

    def test_family_reference_cannot_escape_family_directory(self) -> None:
        broken = copy.deepcopy(self.catalogue)
        broken["families"][0]["file"] = "../catalogue.json"
        with self.assertRaises(resolver.ResolverError):
            resolver._as_families(broken, self.catalogue_path.parent)

    def test_resolution_is_byte_stable_and_native_payload_is_flat(self) -> None:
        config = {
            "fingerprint": 12345,
            "fingerprint_platform": "windows",
            "browser_build": "152.0.7977.83",
        }
        first = resolver.resolve_with_diagnostics(config)
        second = resolver.resolve_with_diagnostics(config)
        encoded_first = resolver._json_output(first).encode("utf-8")
        encoded_second = resolver._json_output(second).encode("utf-8")
        self.assertEqual(encoded_first, encoded_second)
        self.assertTrue(resolver.validate_profile(first["profile"]))
        self.assertNotIn("diagnostics", first["profile"])
        self.assertNotIn("provenance", first["profile"])
        self.assertEqual([], first["native_loader_support"]["unsupported_fields"])

    def test_build_identity_changes_without_runtime_hash(self) -> None:
        base = resolver.resolve_with_diagnostics(
            {"fingerprint": 12345, "fingerprint_platform": "windows", "browser_build": "152.0.7977.83"}
        )
        changed = resolver.resolve_with_diagnostics(
            {"fingerprint": 12345, "fingerprint_platform": "windows", "browser_build": "152.0.7977.82"}
        )
        self.assertNotEqual(base["diagnostics"]["identity"], changed["diagnostics"]["identity"])
        self.assertNotEqual(base["diagnostics"]["profile_id"], changed["diagnostics"]["profile_id"])
        self.assertTrue(base["diagnostics"]["identity"].startswith("sha256:"))

    def test_catalogue_identity_is_part_of_profile_identity(self) -> None:
        profile = {"id": "identity-test"}
        resolver.validate_profile(profile)
        catalogue = {"catalogue_version": 1}
        first, _ = resolver._identity(profile, "1", "windows", "152.0.7977.83", catalogue, "x", {})
        changed_catalogue = {"catalogue_version": 2}
        second, _ = resolver._identity(profile, "1", "windows", "152.0.7977.83", changed_catalogue, "x", {})
        self.assertNotEqual(first, second)

    def test_inline_profile_is_validated_before_seed_selection(self) -> None:
        inline = {"id": "inline-profile", "platform": {"name": "Windows"}}
        result = resolver.resolve_with_diagnostics(
            {"profile": inline, "fingerprint": 987654, "fingerprint_platform": "windows"}
        )
        self.assertEqual(inline, result["profile"])
        self.assertEqual("explicit", result["diagnostics"]["gpu_family"])
        with self.assertRaises(resolver.ResolverError):
            resolver.resolve_with_diagnostics(
                {"profile": {"id": "bad", "not_a_profile_field": True}, "fingerprint": 987654}
            )

    def test_explicit_family_id_and_platform_are_checked(self) -> None:
        result = resolver.resolve_with_diagnostics(
            {"profile_id": "apple-metal-m4", "fingerprint_platform": "macos"}
        )
        self.assertEqual("apple-metal-m4", result["profile"]["id"])
        with self.assertRaises(resolver.ResolverError):
            resolver.resolve_with_diagnostics(
                {"profile_id": "apple-metal-m4", "fingerprint_platform": "windows"}
            )
        with self.assertRaises(resolver.ResolverError):
            resolver.resolve_with_diagnostics(
                {"fingerprint": 1, "fingerprint_platform": "linux"}
            )

    def test_cli_operations_emit_json(self) -> None:
        script = Path(__file__).with_name("profile_resolver.py")
        for args in (
            ["--catalogue"],
            ["--list"],
            ["--resolve", "--fingerprint", "7", "--fingerprint-platform", "windows"],
        ):
            completed = subprocess.run(
                [sys.executable, str(script), *args],
                check=True,
                capture_output=True,
                text=True,
            )
            self.assertIsInstance(json.loads(completed.stdout), dict)


if __name__ == "__main__":
    unittest.main()
