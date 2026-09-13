#!/usr/bin/env python3
"""Validate the compatibility acceptance index and its catalogue bindings.

The acceptance index is metadata only.  It records what may be offered and
which compatibility receipts exist; it never becomes part of the native
profile payload and it never upgrades physical T0 evidence.
"""

from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import re
import sys
from pathlib import Path
from typing import Any, Iterable

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ACCEPTANCE = ROOT / "resources" / "profiles" / "compatibility-acceptance.json"
DEFAULT_CATALOGUE = ROOT / "resources" / "profiles" / "catalogue.json"
DEFAULT_SCHEMA = ROOT / "ledger" / "schema" / "compatibility-acceptance.schema.json"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")

WEBGL_FIELDS = {
    "identity": "gpu",
    "extensions": "gl_extensions",
    "limits": "gl_limits",
    "precision": "gl_precisions",
}
WEBGPU_FIELDS = {
    "identity": "webgpu",
    "features": "webgpu.features",
    "limits": "webgpu.limits",
}
RAW_KEYS = {
    "raw_capture",
    "raw_capture_path",
    "raw_corpus",
    "raw_corpus_path",
    "raw_data",
    "raw_payload",
}


class AcceptanceError(ValueError):
    """Raised for an unreadable or internally inconsistent acceptance index."""


def _validation_status(status: str) -> str:
    return "unvalidated" if status == "offered" else status
def _load_json(path: Path, label: str) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise AcceptanceError(f"cannot read {label}: {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise AcceptanceError(f"{label} is not valid JSON: {path}: {exc}") from exc


def _schema_validate(value: Any, schema_path: Path) -> list[str]:
    validator_path = Path(__file__).with_name("validate-release-contract.py")
    spec = importlib.util.spec_from_file_location("_release_contract_validator", validator_path)
    if spec is None or spec.loader is None:
        raise AcceptanceError(f"cannot load schema validator: {validator_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    schema = module.load_json(schema_path)
    module.validate_schema_document(schema_path)
    errors: list[str] = []
    module.validate(value, schema, schema, "$", schema_path, errors)
    return errors


def _family_path(catalogue_path: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or Path(relative).is_absolute() or "\\" in relative:
        raise AcceptanceError(f"catalogue family file is not a confined relative path: {relative!r}")
    expected_root = (catalogue_path.parent / "families").resolve()
    candidate = (catalogue_path.parent / relative).resolve()
    try:
        candidate.relative_to(expected_root)
    except ValueError as exc:
        raise AcceptanceError(f"catalogue family file escapes families/: {relative!r}") from exc
    if candidate.parent != expected_root or candidate.suffix != ".json":
        raise AcceptanceError(f"catalogue family file is not a direct JSON family: {relative!r}")
    return candidate


def _source_digest(catalogue: dict[str, Any], catalogue_path: Path) -> str:
    entries = catalogue.get("families")
    if not isinstance(entries, list):
        raise AcceptanceError("catalogue families must be an array")
    lines: list[str] = []
    for entry in sorted(entries, key=lambda item: item.get("file", "") if isinstance(item, dict) else ""):
        if not isinstance(entry, dict):
            raise AcceptanceError("catalogue contains a non-object family")
        path = _family_path(catalogue_path, entry.get("file"))
        if not path.is_file() or path.is_symlink():
            raise AcceptanceError(f"catalogue family file is missing or symlinked: {path}")
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        lines.append(f"{digest}  {entry['file']}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()


def _walk_for_raw(value: Any, path: str, errors: list[str]) -> None:
    if isinstance(value, dict):
        for key, child in value.items():
            key_path = f"{path}.{key}"
            if key.lower() in RAW_KEYS:
                errors.append(f"{key_path}: raw compatibility corpus data is forbidden")
            _walk_for_raw(child, key_path, errors)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _walk_for_raw(child, f"{path}[{index}]", errors)
    elif isinstance(value, str):
        normalized = value.replace("\\", "/").lower()
        if "/raw/" in normalized or normalized.startswith("raw/"):
            errors.append(f"{path}: raw capture path is forbidden")


def _nested_present(value: Any, dotted: str) -> bool:
    current = value
    for part in dotted.split("."):
        if not isinstance(current, dict) or part not in current:
            return False
        current = current[part]
    return True


def _check_digest(value: Any, path: str, errors: list[str]) -> None:
    if not isinstance(value, str) or SHA256_RE.fullmatch(value) is None:
        errors.append(f"{path}: expected lowercase SHA-256 digest")


def _check_validated_receipts(
    family: dict[str, Any], requirements: dict[str, Any], errors: list[str]
) -> None:
    family_id = family["family_id"]
    status = family["status"]
    surfaces = family["surfaces"]
    receipts = family["receipts"]
    if status != "validated":
        return

    for area, required_ids in (("webgl", requirements["webgl_surface_ids"]), ("webgpu", requirements["webgpu_surface_ids"])):
        for surface_id in required_ids:
            surface = surfaces[area][surface_id]
            if surface["status"] != "validated":
                errors.append(f"families.{family_id}.{area}.{surface_id}: validated family lacks a validated surface")
            if surface["coverage"] != "present":
                errors.append(f"families.{family_id}.{area}.{surface_id}: validated surface lacks captured coverage")

    webgl = surfaces["webgl"]
    if webgl["cluster_claim"] != "full":
        errors.append(f"families.{family_id}.surfaces.webgl.cluster_claim: validated family must claim the full WebGL cluster")

    repeatability = receipts["repeatability"]
    if repeatability["status"] != "validated":
        errors.append(f"families.{family_id}.receipts.repeatability: validated receipt is missing")
    if repeatability["required_runs"] != requirements["repeatability_runs"]:
        errors.append(f"families.{family_id}.receipts.repeatability.required_runs: does not match probe requirements")
    if repeatability["observed_runs"] < requirements["repeatability_runs"]:
        errors.append(f"families.{family_id}.receipts.repeatability.observed_runs: fewer than required runs")
    if not isinstance(repeatability["same_input_identity"], str) or not repeatability["same_input_identity"]:
        errors.append(f"families.{family_id}.receipts.repeatability.same_input_identity: missing stable identity")
    if not repeatability["stable_fields"] or repeatability["failures"]:
        errors.append(f"families.{family_id}.receipts.repeatability: stable fields or failure list is invalid")

    coherence = receipts["coherence"]
    check_map = {item["id"]: item["status"] for item in coherence["checks"]}
    for check_id in requirements["coherence_check_ids"]:
        if check_map.get(check_id) != "validated":
            errors.append(f"families.{family_id}.receipts.coherence: missing passing check {check_id}")
    if coherence["status"] != "validated":
        errors.append(f"families.{family_id}.receipts.coherence: validated receipt is missing")

    held_out = receipts["held_out"]
    result_map = {item["probe_id"]: item for item in held_out["results"]}
    for probe_id in requirements["held_out_probe_ids"]:
        result = result_map.get(probe_id)
        if result is None or result["status"] != "validated":
            errors.append(f"families.{family_id}.receipts.held_out: missing passing probe {probe_id}")
        elif any(result[field] is None for field in ("input_sha256", "expected_sha256", "observed_sha256")):
            errors.append(f"families.{family_id}.receipts.held_out.{probe_id}: digest is missing")
        else:
            for field in ("input_sha256", "expected_sha256", "observed_sha256"):
                _check_digest(result[field], f"families.{family_id}.receipts.held_out.{probe_id}.{field}", errors)
    if held_out["status"] != "validated":
        errors.append(f"families.{family_id}.receipts.held_out: validated receipt is missing")

    detector = receipts["detector"]
    if detector["status"] != "validated" or detector["result"] != "pass":
        errors.append(f"families.{family_id}.receipts.detector: validated detector result is missing")
    if not detector["raw_receipt_retained"] or detector["receipt_sha256"] is None:
        errors.append(f"families.{family_id}.receipts.detector: retained receipt digest is required")
    else:
        _check_digest(detector["receipt_sha256"], f"families.{family_id}.receipts.detector.receipt_sha256", errors)

    references = {item["kind"]: item for item in family["receipt_references"]}
    for kind in ("repeatability", "coherence", "held-out", "webgl", "webgpu", "detector"):
        reference = references.get(kind)
        if reference is None or not reference["retained"] or reference["sha256"] is None:
            errors.append(f"families.{family_id}.receipt_references: retained {kind} receipt is required")


def validate(
    root: Path = ROOT,
    *,
    acceptance_path: Path | None = None,
    catalogue_path: Path | None = None,
    schema_path: Path | None = None,
) -> list[str]:
    """Return errors for the acceptance index and its catalogue bindings."""
    root = root.resolve()
    acceptance_path = (acceptance_path or root / DEFAULT_ACCEPTANCE.relative_to(ROOT)).resolve()
    catalogue_path = (catalogue_path or root / DEFAULT_CATALOGUE.relative_to(ROOT)).resolve()
    schema_path = (schema_path or root / DEFAULT_SCHEMA.relative_to(ROOT)).resolve()
    errors: list[str] = []

    try:
        acceptance = _load_json(acceptance_path, "compatibility acceptance index")
        catalogue = _load_json(catalogue_path, "profile catalogue")
        schema_errors = _schema_validate(acceptance, schema_path)
    except (AcceptanceError, OSError, TypeError, ValueError) as exc:
        return [str(exc)]
    errors.extend(schema_errors)
    if errors:
        return errors
    if not isinstance(acceptance, dict) or not isinstance(catalogue, dict):
        return ["acceptance index and catalogue must be JSON objects"]

    _walk_for_raw(acceptance, "$", errors)
    if acceptance["catalogue_id"] != catalogue.get("catalogue_id"):
        errors.append("acceptance catalogue_id does not match profile catalogue")
    if acceptance["catalogue_version"] != catalogue.get("catalogue_version"):
        errors.append("acceptance catalogue_version does not match profile catalogue")
    if acceptance["profile_schema_version"] != catalogue.get("profile_schema_version"):
        errors.append("acceptance profile_schema_version does not match profile catalogue")
    if acceptance["browser_build"] != catalogue.get("browser_build"):
        errors.append("acceptance browser_build does not match profile catalogue")
    binding = catalogue.get("compatibility_acceptance")
    if not isinstance(binding, dict):
        errors.append("profile catalogue is missing compatibility_acceptance binding")
    else:
        expected_binding = {
            "file": acceptance_path.name,
            "schema": "ledger/schema/compatibility-acceptance.schema.json",
            "track": acceptance["acceptance_track"],
            "initial_status": acceptance["offering_policy"]["initial_catalogue_status"],
        }
        for field, expected in expected_binding.items():
            if binding.get(field) != expected:
                errors.append(f"catalogue.compatibility_acceptance.{field} does not match acceptance index")

    inventory = acceptance["capture_inventory"]
    if inventory["reported_compatibility_captures"] != catalogue.get("capture_count"):
        errors.append("acceptance reported capture count does not match profile catalogue")
    if inventory["normalized_named_families"] != catalogue.get("family_count"):
        errors.append("acceptance normalized family count does not match profile catalogue")
    if inventory["collector_sha256"] != catalogue.get("capture_inventory", {}).get("collector_sha256"):
        errors.append("acceptance collector digest does not match profile catalogue")

    entries = {entry["id"]: entry for entry in catalogue.get("families", []) if isinstance(entry, dict)}
    records = {record["family_id"]: record for record in acceptance["families"]}
    if set(entries) != set(records):
        errors.append("acceptance family IDs do not exactly match profile catalogue")
    if len(records) != catalogue.get("family_count"):
        errors.append("acceptance family record count does not match profile catalogue")
    for family_id, entry in entries.items():
        record = records.get(family_id)
        if record is None:
            continue
        entry_acceptance = entry.get("acceptance")
        if not isinstance(entry_acceptance, dict):
            errors.append(f"catalogue family {family_id} is missing acceptance binding")
            continue
        expected_entry_acceptance = {
            "status": record["status"],
            "validation_status": _validation_status(record["status"]),
            "record_id": family_id,
        }
        if entry_acceptance != expected_entry_acceptance:
            errors.append(f"catalogue family {family_id} acceptance binding disagrees with acceptance record")

    try:
        source_digest = _source_digest(catalogue, catalogue_path)
    except AcceptanceError as exc:
        errors.append(str(exc))
        source_digest = None
    requirements = acceptance["probe_requirements"]
    for family_id, family in records.items():
        entry = entries.get(family_id)
        if entry is None:
            continue
        prefix = f"families.{family_id}"
        if entry.get("platform") != family["platform"]:
            errors.append(f"{prefix}.platform does not match profile catalogue")
        if entry.get("evidence_class") != "compatibility-capture" or family["evidence_class"] != "compatibility-capture":
            errors.append(f"{prefix}.evidence_class must remain compatibility-capture")
        source = family["source"]
        if source["browser_build"] != acceptance["browser_build"]:
            errors.append(f"{prefix}.source.browser_build does not match acceptance build")
        if source["collector_sha256"] != inventory["collector_sha256"]:
            errors.append(f"{prefix}.source.collector_sha256 does not match capture inventory")
        if source_digest is not None and source["normalized_source_sha256"] != source_digest:
            errors.append(f"{prefix}.source.normalized_source_sha256 does not match family files")
        family_path = _family_path(catalogue_path, entry.get("file"))
        try:
            envelope = _load_json(family_path, f"profile family {family_id}")
        except AcceptanceError as exc:
            errors.append(str(exc))
            continue
        profile = envelope.get("profile") if isinstance(envelope, dict) else None
        if not isinstance(profile, dict) or profile.get("id") != family_id:
            errors.append(f"{prefix}: bound family profile is missing or has the wrong id")
        elif envelope.get("evidence_class") != family["evidence_class"]:
            errors.append(f"{prefix}: bound family evidence_class does not match acceptance record")
        for area, field_map in (("webgl", WEBGL_FIELDS), ("webgpu", WEBGPU_FIELDS)):
            for surface_id, field in field_map.items():
                surface = family["surfaces"][area][surface_id]
                if surface["coverage"] == "present" and not _nested_present(profile, field):
                    errors.append(f"{prefix}.surfaces.{area}.{surface_id}: claims present coverage absent from profile")
        webgl = family["surfaces"]["webgl"]
        if webgl["cluster_claim"] == "full":
            for surface_id in requirements["webgl_surface_ids"]:
                surface = webgl[surface_id]
                if surface["coverage"] != "present" or surface["status"] != "validated":
                    errors.append(f"{prefix}.surfaces.webgl: full cluster claim requires every WebGL surface to be captured and validated")
        for area in ("webgl", "webgpu"):
            for surface_id, surface in family["surfaces"][area].items():
                if surface_id == "cluster_claim":
                    continue
                if surface["status"] == "validated" and surface["coverage"] != "present":
                    errors.append(f"{prefix}.surfaces.{area}.{surface_id}: validated surface must have present coverage")
        repeatability = family["receipts"]["repeatability"]
        if repeatability["required_runs"] != requirements["repeatability_runs"]:
            errors.append(f"{prefix}.receipts.repeatability.required_runs does not match probe requirements")
        detector = family["receipts"]["detector"]
        if detector["status"] == "unvalidated" and detector["result"] == "pass":
            errors.append(f"{prefix}.receipts.detector: unvalidated receipt cannot claim an unqualified pass")
        _check_validated_receipts(family, requirements, errors)
        for reference in family["receipt_references"]:
            if reference["retained"]:
                _check_digest(reference["sha256"], f"{prefix}.receipt_references.{reference['kind']}", errors)
            elif reference["sha256"] is not None:
                errors.append(f"{prefix}.receipt_references.{reference['kind']}: unretained receipt cannot have a digest")

    return errors


def _summary(acceptance_path: Path) -> dict[str, int]:
    acceptance = _load_json(acceptance_path, "compatibility acceptance index")
    summary = {status: 0 for status in ("offered", "validated", "provisional", "limited")}
    for family in acceptance.get("families", []):
        status = family.get("status")
        if status in summary:
            summary[status] += 1
    return summary


def main(argv: Iterable[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--acceptance", type=Path)
    parser.add_argument("--catalogue", type=Path)
    parser.add_argument("--schema", type=Path)
    args = parser.parse_args(argv)
    acceptance = (args.acceptance or args.root / "resources/profiles/compatibility-acceptance.json").resolve()
    errors = validate(args.root, acceptance_path=acceptance, catalogue_path=args.catalogue, schema_path=args.schema)
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        return 1
    print(json.dumps({"acceptance_track": "V3-C/V4-C", "families": _summary(acceptance)}, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
