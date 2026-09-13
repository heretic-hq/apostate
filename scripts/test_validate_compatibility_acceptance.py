#!/usr/bin/env python3
"""Focused tests for the compatibility acceptance validator."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "validate-compatibility-acceptance.py"
ACCEPTANCE = ROOT / "resources" / "profiles" / "compatibility-acceptance.json"
CATALOGUE = ROOT / "resources" / "profiles" / "catalogue.json"
FAMILIES = ROOT / "resources" / "profiles" / "families"
SCHEMA = ROOT / "ledger" / "schema" / "compatibility-acceptance.schema.json"


@contextlib.contextmanager
def copied_fixture() -> Iterator[Path]:
    """Copy the real acceptance inputs into an isolated temporary root."""
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        profiles = root / "resources" / "profiles"
        profiles.mkdir(parents=True)
        shutil.copy2(ACCEPTANCE, profiles / ACCEPTANCE.name)
        shutil.copy2(CATALOGUE, profiles / CATALOGUE.name)
        shutil.copytree(FAMILIES, profiles / "families")
        schema_directory = root / "ledger" / "schema"
        schema_directory.mkdir(parents=True)
        shutil.copy2(SCHEMA, schema_directory / SCHEMA.name)
        yield root


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def acceptance_path(root: Path) -> Path:
    return root / "resources" / "profiles" / ACCEPTANCE.name


def catalogue_path(root: Path) -> Path:
    return root / "resources" / "profiles" / CATALOGUE.name


def find_family(acceptance: dict[str, Any], family_id: str) -> dict[str, Any]:
    for family in acceptance["families"]:
        if family["family_id"] == family_id:
            return family
    raise AssertionError(f"fixture family not found: {family_id}")


def find_catalogue_entry(catalogue: dict[str, Any], family_id: str) -> dict[str, Any]:
    for family in catalogue["families"]:
        if family["id"] == family_id:
            return family
    raise AssertionError(f"fixture catalogue family not found: {family_id}")


class CompatibilityAcceptanceValidatorTests(unittest.TestCase):
    def run_validator(self, root: Path) -> subprocess.CompletedProcess[str]:
        environment = dict(os.environ)
        environment["PYTHONDONTWRITEBYTECODE"] = "1"
        return subprocess.run(
            [sys.executable, str(SCRIPT), "--root", str(root)],
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=False,
            env=environment,
        )

    def assert_rejected(self, result: subprocess.CompletedProcess[str], message: str) -> None:
        output = result.stdout + result.stderr
        self.assertNotEqual(result.returncode, 0, output)
        self.assertIn(message, output)

    def test_current_fourteen_family_index_passes(self) -> None:
        with copied_fixture() as root:
            result = self.run_validator(root)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        summary = json.loads(result.stdout)
        self.assertEqual(summary["families"], {"offered": 14, "validated": 0, "provisional": 0, "limited": 0})

    def test_sixteen_reported_captures_normalize_to_fourteen_families(self) -> None:
        with copied_fixture() as root:
            acceptance = load_json(acceptance_path(root))
            catalogue = load_json(catalogue_path(root))
            result = self.run_validator(root)

        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(acceptance["capture_inventory"]["reported_compatibility_captures"], 16)
        self.assertEqual(acceptance["capture_inventory"]["normalized_named_families"], 14)
        self.assertEqual(catalogue["capture_count"], 16)
        self.assertEqual(catalogue["family_count"], 14)

    def test_missing_family_record_is_rejected(self) -> None:
        with copied_fixture() as root:
            path = acceptance_path(root)
            acceptance = load_json(path)
            acceptance["families"].pop()
            write_json(path, acceptance)
            result = self.run_validator(root)

        self.assert_rejected(result, "acceptance family IDs do not exactly match profile catalogue")

    def test_platform_mismatch_is_rejected(self) -> None:
        with copied_fixture() as root:
            path = acceptance_path(root)
            acceptance = load_json(path)
            find_family(acceptance, "apple-metal-m2")["platform"] = "windows"
            write_json(path, acceptance)
            result = self.run_validator(root)

        self.assert_rejected(result, "families.apple-metal-m2.platform does not match profile catalogue")

    def test_evidence_class_mismatch_is_rejected(self) -> None:
        with copied_fixture() as root:
            path = catalogue_path(root)
            catalogue = load_json(path)
            find_catalogue_entry(catalogue, "apple-metal-m2")["evidence_class"] = "catalogue-value"
            write_json(path, catalogue)
            result = self.run_validator(root)

        self.assert_rejected(result, "families.apple-metal-m2.evidence_class must remain compatibility-capture")

    def test_full_webgl_claim_requires_validated_cluster_surfaces(self) -> None:
        with copied_fixture() as root:
            path = acceptance_path(root)
            acceptance = load_json(path)
            family = find_family(acceptance, "apple-metal-m2")
            family["surfaces"]["webgl"]["cluster_claim"] = "full"
            write_json(path, acceptance)
            result = self.run_validator(root)

        self.assert_rejected(
            result,
            "families.apple-metal-m2.surfaces.webgl: full cluster claim requires every WebGL surface to be captured and validated",
        )

    def test_validated_family_requires_retained_receipts(self) -> None:
        with copied_fixture() as root:
            path = acceptance_path(root)
            acceptance = load_json(path)
            find_family(acceptance, "apple-metal-m2")["status"] = "validated"
            write_json(path, acceptance)
            result = self.run_validator(root)

        self.assert_rejected(result, "families.apple-metal-m2.receipt_references: retained repeatability receipt is required")

    def test_raw_corpus_path_is_rejected(self) -> None:
        with copied_fixture() as root:
            path = acceptance_path(root)
            acceptance = load_json(path)
            family = find_family(acceptance, "apple-metal-m2")
            family["known_limitations"][0] = "captures/raw/apple-metal-m2.json"
            write_json(path, acceptance)
            result = self.run_validator(root)

        self.assert_rejected(result, "raw capture path is forbidden")

    def test_embedded_raw_capture_is_rejected(self) -> None:
        with copied_fixture() as root:
            path = acceptance_path(root)
            acceptance = load_json(path)
            find_family(acceptance, "apple-metal-m2")["source"]["raw_capture"] = {"gpu": "captured"}
            write_json(path, acceptance)
            result = self.run_validator(root)

        self.assert_rejected(result, "raw_capture")

    def test_physical_reference_is_rejected(self) -> None:
        with copied_fixture() as root:
            path = acceptance_path(root)
            acceptance = load_json(path)
            find_family(acceptance, "apple-metal-m2")["source"]["physical_reference"] = True
            write_json(path, acceptance)
            result = self.run_validator(root)

        self.assert_rejected(result, "physical_reference")

    def test_t0_physical_closure_claim_is_rejected(self) -> None:
        with copied_fixture() as root:
            path = acceptance_path(root)
            acceptance = load_json(path)
            acceptance["offering_policy"]["physical_t0_closure"] = "captured"
            write_json(path, acceptance)
            result = self.run_validator(root)

        self.assert_rejected(result, "physical_t0_closure")


if __name__ == "__main__":
    unittest.main()
