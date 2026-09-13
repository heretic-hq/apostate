"""Deterministic, catalogue-backed profile resolution for Python launches."""

from __future__ import annotations

import copy
import hashlib
import importlib.resources
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping

from .config import (
    CATALOGUE_VERSION,
    CHROMIUM_VERSION,
    PROFILE_SCHEMA_VERSION,
    LaunchConfig,
    host_persona,
    normalize_platform,
    translate_options,
)
from .errors import ConfigurationError, ProfileError
from .profile_validation import validate_profile

_CANONICAL_PROFILE_KEYS = frozenset({
    "id", "source_capture", "cpu", "memory", "platform", "browser", "speech", "screen",
    "gl_limits", "gl_extensions", "gl_precisions", "webgpu", "gpu", "locale", "theme",
    "input", "audio", "media", "keyboard", "fonts",
})
_SUPPORTED_EVIDENCE = frozenset({
    "physical-ground-truth", "compatibility-capture", "catalogue-value",
    "native-derived", "proxy-derived", "host-inherited",
})

def _load_json(path: Path, description: str) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError(f"{description} is unavailable or invalid: {path}") from exc
    if not isinstance(value, Mapping):
        raise ProfileError(f"{description} must be a JSON object: {path}")
    return value


_ACCEPTANCE_FILE = "compatibility-acceptance.json"
_ACCEPTANCE_SCHEMA_FILE = "compatibility-acceptance.schema.json"
_ACCEPTANCE_SCHEMA_DESCRIPTOR = "ledger/schema/compatibility-acceptance.schema.json"
_ACCEPTANCE_TRACK = "V3-C/V4-C"
_ACCEPTANCE_STATUSES = frozenset({"offered", "validated", "provisional", "limited"})
_RAW_ACCEPTANCE_KEYS = frozenset({
    "raw_capture", "raw_capture_path", "raw_corpus", "raw_corpus_path", "raw_data", "raw_payload",
})


def _reject_raw_acceptance(value: Any, path: str = "compatibility_acceptance") -> None:
    if isinstance(value, Mapping):
        for key, child in value.items():
            child_path = f"{path}.{key}"
            if str(key).lower() in _RAW_ACCEPTANCE_KEYS:
                raise ProfileError(f"{child_path} contains forbidden raw compatibility data")
            _reject_raw_acceptance(child, child_path)
    elif isinstance(value, list):
        for index, child in enumerate(value):
            _reject_raw_acceptance(child, f"{path}[{index}]")
    elif isinstance(value, str):
        normalized = value.replace("\\", "/").lower()
        if normalized == "raw" or normalized.startswith("raw/") or "/raw/" in normalized:
            raise ProfileError(f"{path} contains a forbidden raw capture path")

def _family_source_digest(catalogue: Mapping[str, Any], base: Path | None, package_root: Any) -> str:
    entries = catalogue.get("families")
    if not isinstance(entries, list):
        raise ProfileError("profile catalogue families must be a list")
    lines: list[str] = []
    for entry in sorted(entries, key=lambda item: item.get("file", "") if isinstance(item, Mapping) else ""):
        if not isinstance(entry, Mapping):
            raise ProfileError("profile catalogue contains a non-object family")
        family_id = entry.get("id")
        relative = entry.get("file")
        expected = f"families/{family_id}.json"
        if not isinstance(family_id, str) or relative != expected:
            raise ProfileError(f"catalogue family {family_id!r} file must be {expected}")
        try:
            if base is not None:
                family_dir = base / "families"
                if family_dir.is_symlink():
                    raise ProfileError("catalogue family directory must not be a symlink")
                path = base / relative
                if path.is_symlink() or not path.is_file():
                    raise ProfileError(f"catalogue family file is missing or symlinked: {relative}")
                content = path.read_bytes()
            elif package_root is not None:
                resource = package_root.joinpath("assets", "families", f"{family_id}.json")
                if resource.is_symlink() or not resource.is_file():
                    raise ProfileError(f"package catalogue family file is missing or symlinked: {relative}")
                content = resource.read_bytes()
            else:
                raise ProfileError("compatibility acceptance family assets have no resolvable location")
        except OSError as exc:
            raise ProfileError(f"catalogue family file is unavailable: {relative}") from exc
        digest = hashlib.sha256(content).hexdigest()
        lines.append(f"{digest}  {relative}\n")
    return hashlib.sha256("".join(lines).encode("utf-8")).hexdigest()

def _schema_type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "null":
        return value is None
    return False


def _schema_reference(root: Mapping[str, Any], reference: Any) -> Mapping[str, Any] | None:
    if not isinstance(reference, str) or not reference.startswith("#/"):
        return None
    current: Any = root
    for raw_part in reference[2:].split("/"):
        part = raw_part.replace("~1", "/").replace("~0", "~")
        if not isinstance(current, Mapping) or part not in current:
            return None
        current = current[part]
    return current if isinstance(current, Mapping) else None


def _schema_errors(value: Any, schema: Mapping[str, Any], root: Mapping[str, Any],
                   path: str = "$") -> list[str]:
    """Validate the JSON-Schema subset used by the acceptance contract."""
    errors: list[str] = []
    if "$ref" in schema:
        referenced = _schema_reference(root, schema["$ref"])
        if referenced is None:
            return [f"{path}: acceptance schema contains an invalid reference"]
        errors.extend(_schema_errors(value, referenced, root, path))

    expected = schema.get("type")
    if isinstance(expected, str):
        type_matches = _schema_type_matches(value, expected)
    elif isinstance(expected, list) and expected:
        type_matches = all(isinstance(item, str) for item in expected) and any(
            _schema_type_matches(value, item) for item in expected
        )
    else:
        type_matches = True
    if not type_matches:
        return [f"{path}: value has the wrong JSON type"]

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: value does not match the acceptance schema constant")
    allowed = schema.get("enum")
    if isinstance(allowed, list) and value not in allowed:
        errors.append(f"{path}: value is not allowed by the acceptance schema")

    if isinstance(value, str):
        minimum = schema.get("minLength")
        maximum = schema.get("maxLength")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path}: string is shorter than the acceptance schema minimum")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path}: string exceeds the acceptance schema maximum")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            try:
                matches = re.search(pattern, value) is not None
            except re.error:
                matches = False
            if not matches:
                errors.append(f"{path}: string does not match the acceptance schema pattern")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        minimum = schema.get("minimum")
        maximum = schema.get("maximum")
        exclusive_minimum = schema.get("exclusiveMinimum")
        exclusive_maximum = schema.get("exclusiveMaximum")
        if isinstance(minimum, (int, float)) and value < minimum:
            errors.append(f"{path}: number is below the acceptance schema minimum")
        if isinstance(maximum, (int, float)) and value > maximum:
            errors.append(f"{path}: number exceeds the acceptance schema maximum")
        if isinstance(exclusive_minimum, (int, float)) and value <= exclusive_minimum:
            errors.append(f"{path}: number is not above the acceptance schema minimum")
        if isinstance(exclusive_maximum, (int, float)) and value >= exclusive_maximum:
            errors.append(f"{path}: number is not below the acceptance schema maximum")

    if isinstance(value, list):
        minimum = schema.get("minItems")
        maximum = schema.get("maxItems")
        if isinstance(minimum, int) and len(value) < minimum:
            errors.append(f"{path}: array has too few items")
        if isinstance(maximum, int) and len(value) > maximum:
            errors.append(f"{path}: array has too many items")
        if schema.get("uniqueItems") is True:
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(encoded) != len(set(encoded)):
                errors.append(f"{path}: array items are not unique")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, item_schema, root, f"{path}[{index}]"))

    if isinstance(value, Mapping):
        required = schema.get("required")
        if isinstance(required, list):
            for name in required:
                if isinstance(name, str) and name not in value:
                    errors.append(f"{path}.{name}: required acceptance metadata is missing")
        properties = schema.get("properties")
        properties = properties if isinstance(properties, Mapping) else {}
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            child = f"{path}.{name}"
            child_schema = properties.get(name)
            if isinstance(child_schema, Mapping):
                errors.extend(_schema_errors(item, child_schema, root, child))
            elif additional is False:
                errors.append(f"{child}: property is not allowed by the acceptance schema")
            elif isinstance(additional, Mapping):
                errors.extend(_schema_errors(item, additional, root, child))

    all_of = schema.get("allOf")
    if isinstance(all_of, list):
        for branch in all_of:
            if isinstance(branch, Mapping):
                errors.extend(_schema_errors(value, branch, root, path))
    for keyword, expected_matches in (("anyOf", "at least one"), ("oneOf", "exactly one")):
        branches = schema.get(keyword)
        if isinstance(branches, list) and branches:
            matches = sum(
                not _schema_errors(value, branch, root, path)
                for branch in branches if isinstance(branch, Mapping)
            )
            if (keyword == "anyOf" and matches == 0) or (keyword == "oneOf" and matches != 1):
                errors.append(f"{path}: value must match {expected_matches} acceptance schema branch")
    return errors


def _acceptance_binding(catalogue: Mapping[str, Any]) -> Mapping[str, Any] | None:
    binding = catalogue.get("compatibility_acceptance")
    if binding is None:
        return None
    if not isinstance(binding, Mapping):
        raise ProfileError("profile catalogue compatibility_acceptance binding must be an object")
    expected = {
        "file": _ACCEPTANCE_FILE,
        "schema": _ACCEPTANCE_SCHEMA_DESCRIPTOR,
        "track": _ACCEPTANCE_TRACK,
        "initial_status": "offered",
    }
    if set(binding) != set(expected) or any(binding.get(key) != value for key, value in expected.items()):
        raise ProfileError("profile catalogue compatibility_acceptance binding is invalid")
    return binding


def _acceptance_from_mapping(catalogue: Mapping[str, Any], acceptance: Mapping[str, Any],
                             schema: Mapping[str, Any], *, family_base: Path | None = None,
                             package_root: Any = None) -> dict[str, Any]:
    binding = _acceptance_binding(catalogue)
    if binding is None:
        raise ProfileError("profile catalogue compatibility acceptance binding is missing")
    errors = _schema_errors(acceptance, schema, schema)
    if errors:
        detail = "; ".join(errors[:4])
        if len(errors) > 4:
            detail += f"; and {len(errors) - 4} more"
        raise ProfileError(f"compatibility acceptance index is malformed: {detail}")
    _reject_raw_acceptance(acceptance)

    for field in ("catalogue_id", "catalogue_version", "profile_schema_version", "browser_build"):
        if catalogue.get(field) != acceptance.get(field):
            raise ProfileError(f"compatibility acceptance {field} does not match profile catalogue")
    if catalogue.get("version") != catalogue.get("catalogue_version"):
        raise ProfileError("profile catalogue version does not match catalogue_version")
    if acceptance.get("acceptance_track") != binding["track"]:
        raise ProfileError("compatibility acceptance track does not match profile catalogue")
    offering_policy = acceptance.get("offering_policy")
    if not isinstance(offering_policy, Mapping) or offering_policy.get("initial_catalogue_status") != binding["initial_status"]:
        raise ProfileError("compatibility acceptance offering policy does not match profile catalogue")

    indexed_families = catalogue.get("families")
    if not isinstance(indexed_families, list) or not indexed_families:
        raise ProfileError("profile catalogue families must be a non-empty list")
    indexed: dict[str, Mapping[str, Any]] = {}
    for entry in indexed_families:
        if not isinstance(entry, Mapping) or not isinstance(entry.get("id"), str) or not entry["id"]:
            raise ProfileError("profile catalogue contains an invalid family")
        family_id = entry["id"]
        if family_id in indexed:
            raise ProfileError(f"profile catalogue duplicates family {family_id}")
        indexed[family_id] = entry
    if catalogue.get("family_count") != len(indexed):
        raise ProfileError("profile catalogue family_count does not match its families")

    inventory = acceptance.get("capture_inventory")
    if not isinstance(inventory, Mapping):
        raise ProfileError("compatibility acceptance capture inventory is invalid")
    if inventory.get("normalized_named_families") != len(indexed):
        raise ProfileError("compatibility acceptance family count does not match profile catalogue")
    if catalogue.get("capture_count") != inventory.get("reported_compatibility_captures"):
        raise ProfileError("compatibility acceptance capture count does not match profile catalogue")

    raw_records = acceptance.get("families")
    if not isinstance(raw_records, list) or not raw_records:
        raise ProfileError("compatibility acceptance families must be a non-empty list")
    records: dict[str, Mapping[str, Any]] = {}
    metadata: dict[str, dict[str, str]] = {}
    for record in raw_records:
        if not isinstance(record, Mapping) or not isinstance(record.get("family_id"), str):
            raise ProfileError("compatibility acceptance contains an invalid family record")
        family_id = record["family_id"]
        if family_id in records:
            raise ProfileError(f"compatibility acceptance duplicates family {family_id}")
        records[family_id] = record
    if set(records) != set(indexed):
        raise ProfileError("compatibility acceptance families do not match profile catalogue")

    for family_id, entry in indexed.items():
        record = records[family_id]
        if record.get("platform") != entry.get("platform"):
            raise ProfileError(f"compatibility acceptance family {family_id} platform does not match profile catalogue")
        if record.get("evidence_class") != entry.get("evidence_class"):
            raise ProfileError(f"compatibility acceptance family {family_id} evidence class does not match profile catalogue")
        source = record.get("source")
        if not isinstance(source, Mapping) or source.get("browser_build") != acceptance.get("browser_build"):
            raise ProfileError(f"compatibility acceptance family {family_id} browser build is invalid")
        if source.get("collector_sha256") != inventory.get("collector_sha256"):
            raise ProfileError(f"compatibility acceptance family {family_id} collector does not match the inventory")
        status = record.get("status")
        if status not in _ACCEPTANCE_STATUSES:
            raise ProfileError(f"compatibility acceptance family {family_id} status is invalid")
        validation_status = "unvalidated" if status == "offered" else status
        expected_binding = {
            "status": status,
            "validation_status": validation_status,
            "record_id": family_id,
        }
        family_binding = entry.get("acceptance")
        if not isinstance(family_binding, Mapping) or dict(family_binding) != expected_binding:
            raise ProfileError(f"profile catalogue family {family_id} acceptance binding disagrees with its record")
        metadata[family_id] = {
            "acceptance_track": str(acceptance["acceptance_track"]),
            "acceptance_status": str(status),
            "validation_status": validation_status,
            "acceptance_record_id": family_id,
        }
    if family_base is not None or package_root is not None:
        source_digest = _family_source_digest(catalogue, family_base, package_root)
        for record_id, record in records.items():
            source = record.get("source")
            if not isinstance(source, Mapping) or source.get("normalized_source_sha256") != source_digest:
                raise ProfileError(f"compatibility acceptance family {record_id} source digest does not match family files")
    return {
        "acceptance_track": acceptance["acceptance_track"],
        "families": metadata,
        "records": {family_id: copy.deepcopy(dict(record)) for family_id, record in records.items()},
    }


def _package_json(package_root: Any, filename: str, description: str) -> Mapping[str, Any]:
    resource = package_root.joinpath("assets", filename)
    is_symlink = getattr(resource, "is_symlink", None)
    try:
        if callable(is_symlink) and is_symlink():
            raise OSError("symbolic link")
        value = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"package {description} is unavailable or invalid") from exc
    if not isinstance(value, Mapping):
        raise ProfileError(f"package {description} must be a JSON object")
    return value


def _load_acceptance(catalogue: Mapping[str, Any], base: Path | None,
                     package_root: Any = None) -> dict[str, Any] | None:
    binding = _acceptance_binding(catalogue)
    if binding is None:
        return None
    if base is not None:
        acceptance_path = base / _ACCEPTANCE_FILE
        if acceptance_path.is_symlink():
            raise ProfileError("compatibility acceptance index must not be a symlink")
        acceptance = _load_json(acceptance_path, "compatibility acceptance index")
        source_schema = Path(__file__).resolve().parents[2] / _ACCEPTANCE_SCHEMA_DESCRIPTOR
        schema_path = source_schema if source_schema.is_file() else base / _ACCEPTANCE_SCHEMA_FILE
        if schema_path.is_symlink():
            raise ProfileError("compatibility acceptance schema must not be a symlink")
        schema = _load_json(schema_path, "compatibility acceptance schema")
    elif package_root is not None:
        acceptance = _package_json(package_root, _ACCEPTANCE_FILE, "compatibility acceptance index")
        # The catalogue stores the source-tree descriptor.  Installed packages
        # intentionally map it to the schema copy beside the packaged index.
        schema = _package_json(package_root, _ACCEPTANCE_SCHEMA_FILE, "compatibility acceptance schema")
    else:
        raise ProfileError("compatibility acceptance index has no resolvable asset location")
    return _acceptance_from_mapping(catalogue, acceptance, schema, family_base=base, package_root=package_root)


def _metadata_version(mapping: Mapping[str, Any], field: str, expected: int, *, label: str) -> None:
    if field in mapping and mapping.get(field) != expected:
        raise ProfileError(f"catalogue family {label} {field} does not match this package")


def _family_platform(value: Any, *, label: str) -> str:
    if not isinstance(value, str):
        raise ProfileError(f"catalogue family {label} has no platform")
    try:
        platform = normalize_platform(value)
    except ConfigurationError as exc:
        raise ProfileError(f"catalogue family {label} has unsupported platform") from exc
    assert platform is not None
    return platform


def _family_index_metadata(entry: Mapping[str, Any], *, expected_catalogue_version: int) -> dict[str, Any]:
    family_id = entry.get("id")
    if not isinstance(family_id, str) or not family_id.strip():
        raise ProfileError("every profile family needs a stable id")
    family_id = family_id.strip()
    if family_id != entry["id"] or family_id in {".", ".."} or "/" in family_id or "\\" in family_id:
        raise ProfileError(f"catalogue family {family_id!r} has an unsafe id")
    platform = _family_platform(entry.get("platform"), label=family_id)
    gpu_family = entry.get("gpu_family")
    if not isinstance(gpu_family, str) or not gpu_family.strip():
        raise ProfileError(f"catalogue family {family_id} is missing gpu_family")
    evidence_class = entry.get("evidence_class")
    if not isinstance(evidence_class, str) or evidence_class not in _SUPPORTED_EVIDENCE:
        raise ProfileError(f"catalogue family {family_id} has invalid evidence_class")
    expected_file = f"families/{family_id}.json"
    relative = entry.get("file")
    if not isinstance(relative, str) or not relative:
        raise ProfileError(f"catalogue family {family_id} file must be {expected_file}")
    if "\0" in relative or "\\" in relative or Path(relative).is_absolute():
        raise ProfileError(f"catalogue family {family_id} file must be a confined relative JSON path")
    if relative.split("/") != ["families", f"{family_id}.json"]:
        raise ProfileError(f"catalogue family {family_id} file must be {expected_file}")
    _metadata_version(entry, "catalogue_version", expected_catalogue_version, label=family_id)
    _metadata_version(entry, "profile_schema_version", PROFILE_SCHEMA_VERSION, label=family_id)
    acceptance = entry.get("acceptance")
    if acceptance is not None:
        if not isinstance(acceptance, Mapping) or acceptance.get("record_id") != family_id:
            raise ProfileError(f"catalogue family {family_id} has invalid acceptance binding")
        if acceptance.get("status") not in {"offered", "validated", "provisional", "limited"}:
            raise ProfileError(f"catalogue family {family_id} has invalid acceptance status")
        if acceptance.get("validation_status") not in {"validated", "provisional", "limited", "unvalidated"}:
            raise ProfileError(f"catalogue family {family_id} has invalid validation status")
    return {
        "id": family_id,
        "file": expected_file,
        "platform": platform,
        "gpu_family": gpu_family.strip(),
        "evidence_class": evidence_class,
        **({"acceptance": copy.deepcopy(dict(acceptance))} if acceptance is not None else {}),
    }


def _validate_family_metadata(entry: Mapping[str, Any], loaded: Mapping[str, Any], *,
                              expected_catalogue_version: int) -> dict[str, Any]:
    index = _family_index_metadata(entry, expected_catalogue_version=expected_catalogue_version)
    label = index["id"]
    if loaded.get("id") != index["id"]:
        raise ProfileError(f"catalogue family {label} id does not match index")
    if _family_platform(loaded.get("platform"), label=label) != index["platform"]:
        raise ProfileError(f"catalogue family {label} platform does not match index")
    gpu_family = loaded.get("gpu_family")
    if not isinstance(gpu_family, str) or not gpu_family.strip() or gpu_family != index["gpu_family"]:
        raise ProfileError(f"catalogue family {label} gpu_family does not match index")
    evidence_class = loaded.get("evidence_class")
    if evidence_class not in _SUPPORTED_EVIDENCE or evidence_class != index["evidence_class"]:
        raise ProfileError(f"catalogue family {label} evidence_class does not match index")
    _metadata_version(loaded, "catalogue_version", expected_catalogue_version, label=label)
    _metadata_version(loaded, "profile_schema_version", PROFILE_SCHEMA_VERSION, label=label)
    provenance = loaded.get("provenance")
    if not isinstance(provenance, str) or not provenance.strip():
        raise ProfileError(f"catalogue family {label} is missing provenance")
    if "provenance" in entry:
        if not isinstance(entry["provenance"], str) or not entry["provenance"].strip():
            raise ProfileError(f"catalogue family {label} index has invalid provenance")
        if entry["provenance"] != provenance:
            raise ProfileError(f"catalogue family {label} provenance does not match index")
    anchor = loaded.get("anchor")
    if not isinstance(anchor, Mapping) or not anchor:
        raise ProfileError(f"catalogue family {label} is missing anchor metadata")
    for field, evidence in anchor.items():
        if not isinstance(field, str) or not field or evidence not in _SUPPORTED_EVIDENCE:
            raise ProfileError(f"catalogue family {label} anchor has invalid evidence metadata")
    if "anchor" in entry:
        if not isinstance(entry["anchor"], Mapping) or not entry["anchor"]:
            raise ProfileError(f"catalogue family {label} index has invalid anchor metadata")
        if dict(entry["anchor"]) != dict(anchor):
            raise ProfileError(f"catalogue family {label} anchor does not match index")
    profile = loaded.get("profile")
    if not isinstance(profile, Mapping):
        raise ProfileError(f"catalogue family {label} has no profile payload")
    if profile.get("id") != index["id"]:
        raise ProfileError(f"catalogue family {label} profile identity does not match index")
    if _profile_platform(profile) != index["platform"]:
        raise ProfileError(f"catalogue family {label} profile platform does not match index")
    merged = dict(index)
    merged["profile"] = dict(profile)
    merged["provenance"] = provenance.strip()
    merged["anchor"] = copy.deepcopy(dict(anchor))
    if "acceptance" in index:
        merged["acceptance"] = copy.deepcopy(index["acceptance"])
    return merged


def _read_package_family(package_root: Any, entry: Mapping[str, Any]) -> dict[str, Any]:
    """Read a family payload from package resources without source paths."""
    index = _family_index_metadata(entry, expected_catalogue_version=CATALOGUE_VERSION)
    resource = package_root.joinpath("assets", "families", f"{index['id']}.json")
    is_symlink = getattr(resource, "is_symlink", None)
    if callable(is_symlink) and is_symlink():
        raise ProfileError(f"package catalogue family file is a symlink: {index['file']}")
    try:
        if not resource.is_file():
            raise FileNotFoundError(index["file"])
        loaded = json.loads(resource.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError(f"package catalogue family file is unavailable or invalid: {index['file']}") from exc
    if not isinstance(loaded, Mapping):
        raise ProfileError(f"package catalogue family file must be a JSON object: {index['file']}")
    return _validate_family_metadata(index, loaded, expected_catalogue_version=CATALOGUE_VERSION)




def _load_catalogue() -> tuple[dict[str, Any], Path | None, dict[str, Any] | None]:
    source = Path(__file__).resolve().parents[2] / "resources" / "profiles" / "catalogue.json"
    if source.is_file():
        loaded = dict(_load_json(source, "profile catalogue"))
        acceptance = _load_acceptance(loaded, source.parent)
        return loaded, source.parent, acceptance
    try:
        package_root = importlib.resources.files("apostate")
        loaded = dict(_package_json(package_root, "catalogue.json", "profile catalogue"))
        acceptance = _load_acceptance(loaded, None, package_root)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ProfileError("package profile catalogue is unavailable or invalid") from exc
    raw_families = loaded.get("families", loaded.get("profiles", []))
    if not isinstance(raw_families, list):
        raise ProfileError("profile catalogue families must be a list")
    loaded["families"] = [
        _read_package_family(package_root, item) if isinstance(item, Mapping) and item.get("file") else dict(item)
        for item in raw_families
    ]
    return loaded, None, acceptance


def _stable_index(seed: int | str, platform: str, catalogue_version: int,
                  browser_version: str, count: int) -> int:
    if count <= 0:
        raise ProfileError("profile catalogue has no compatible families")
    source = json.dumps(
        {"seed": seed, "platform": platform, "catalogue_version": catalogue_version,
         "browser_build": browser_version},
        sort_keys=True, separators=(",", ":"),
    ).encode("utf-8")
    return int.from_bytes(hashlib.sha256(source).digest()[:8], "big") % count


def _profile_file(value: Any) -> Mapping[str, Any] | None:
    if isinstance(value, Path):
        return _load_json(value.expanduser(), "profile file")
    if not isinstance(value, str):
        return None
    path = Path(value).expanduser()
    looks_like_path = value.endswith(".json") or "/" in value or "\\" in value or value.startswith(".") or path.is_absolute()
    if not looks_like_path:
        return None
    return _load_json(path, "profile file")


def _confined_file(base: Path, entry: Mapping[str, Any], *, expected_catalogue_version: int) -> tuple[Path, dict[str, Any]]:
    index = _family_index_metadata(entry, expected_catalogue_version=expected_catalogue_version)
    base_resolved = base.resolve()
    family_dir = base / "families"
    if family_dir.is_symlink():
        raise ProfileError("catalogue family directory must not be a symlink")
    candidate = family_dir / f"{index['id']}.json"
    if candidate.is_symlink():
        raise ProfileError(f"catalogue family file is a symlink: {index['file']}")
    try:
        candidate.resolve().relative_to(base_resolved)
    except (OSError, ValueError) as exc:
        raise ProfileError("catalogue family file escapes the catalogue directory") from exc
    if not candidate.is_file():
        raise ProfileError(f"catalogue family file is missing: {index['file']}")
    return candidate, index


def _read_family_file(base: Path, entry: Mapping[str, Any], *, expected_catalogue_version: int) -> dict[str, Any]:
    path, index = _confined_file(base, entry, expected_catalogue_version=expected_catalogue_version)
    loaded = _load_json(path, "catalogue family file")
    return _validate_family_metadata(index, loaded, expected_catalogue_version=expected_catalogue_version)


def _family_profile(family: Mapping[str, Any]) -> dict[str, Any]:
    """Extract only the profile-schema payload from a family envelope."""
    payload = family.get("profile") or family.get("profile_payload")
    if isinstance(payload, Mapping):
        return dict(payload)
    anchor = family.get("anchor")
    if isinstance(anchor, Mapping):
        nested = anchor.get("profile")
        if isinstance(nested, Mapping):
            return dict(nested)
        direct = {key: copy.deepcopy(anchor[key]) for key in _CANONICAL_PROFILE_KEYS if key in anchor}
        if direct:
            return direct
    direct = {key: copy.deepcopy(family[key]) for key in _CANONICAL_PROFILE_KEYS if key in family}
    if direct:
        return direct
    raise ProfileError(f"catalogue family {family.get('id', '<unknown>')} has no profile payload")


def _as_families(catalogue: Mapping[str, Any], base: Path | None = None,
                 *, expected_catalogue_version: int) -> list[dict[str, Any]]:
    raw = catalogue.get("families", catalogue.get("profiles", []))
    if not isinstance(raw, list):
        raise ProfileError("profile catalogue families must be a list")
    families: list[dict[str, Any]] = []
    for item in raw:
        if not isinstance(item, Mapping):
            raise ProfileError("profile catalogue contains a non-object family")
        family = (_read_family_file(base, item, expected_catalogue_version=expected_catalogue_version)
                  if base is not None else
                  _validate_family_metadata(item, item, expected_catalogue_version=expected_catalogue_version))
        validate_profile(_family_profile(family))
        families.append(family)
    return sorted(families, key=lambda value: value["id"])

def _resolution_family_metadata(family: Mapping[str, Any], acceptance: Mapping[str, Any] | None = None) -> dict[str, Any]:
    metadata = {
        "gpu_family": family["gpu_family"],
        "evidence_class": family["evidence_class"],
        "provenance": family["provenance"],
        "anchor": copy.deepcopy(dict(family["anchor"])),
        "family_file": family["file"],
    }
    binding = family.get("acceptance")
    if isinstance(binding, Mapping):
        metadata["compatibility_acceptance"] = copy.deepcopy(dict(binding))
        metadata["acceptance_track"] = acceptance.get("acceptance_track") if isinstance(acceptance, Mapping) else None
        metadata["acceptance_status"] = binding.get("status")
        metadata["validation_status"] = binding.get("validation_status")
        metadata["acceptance_record_id"] = binding.get("record_id")
    return {key: value for key, value in metadata.items() if value is not None}


def _profile_platform(profile: Mapping[str, Any]) -> str | None:
    value = profile.get("platform")
    if isinstance(value, Mapping):
        value = value.get("name")
    if not isinstance(value, str) or not value.strip():
        return None
    aliases = {"macos": "macos", "mac": "macos", "mac os": "macos", "mac os x": "macos",
               "os x": "macos", "osx": "macos", "darwin": "macos", "windows": "windows",
               "win": "windows", "win32": "windows", "linux": "linux"}
    lowered = value.strip().lower()
    return aliases.get(lowered)


def _nested_locale(profile: Mapping[str, Any]) -> tuple[str | None, str | None]:
    value = profile.get("locale")
    if not isinstance(value, Mapping):
        return None, None
    languages = value.get("accept_languages")
    timezone = value.get("timezone")
    return (
        languages if isinstance(languages, str) and languages else None,
        timezone if isinstance(timezone, str) and timezone else None,
    )


@dataclass(frozen=True)
class ProfileResolution:
    """Schema-valid profile plus provenance useful to diagnostics."""

    profile: dict[str, Any]
    profile_id: str
    catalogue_version: int
    browser_version: str
    platform: str
    locale_source: str
    timezone_source: str
    identity: str
    warnings: tuple[str, ...] = ()
    profile_schema_version: int = PROFILE_SCHEMA_VERSION
    gpu_family: str | None = None
    evidence_class: str | None = None
    provenance: str | None = None
    anchor: Mapping[str, Any] | None = None
    family_file: str | None = None
    compatibility_acceptance: Mapping[str, Any] | None = None
    acceptance_track: str | None = None
    acceptance_status: str | None = None
    validation_status: str | None = None
    acceptance_record_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        """Return a detached resolution/diagnostics envelope.

        Acceptance and family metadata deliberately live beside ``profile``;
        they are never merged into the schema-valid native payload.
        """
        output: dict[str, Any] = {
            "profile": copy.deepcopy(self.profile),
            "profile_id": self.profile_id,
            "catalogue_version": self.catalogue_version,
            "profile_schema_version": self.profile_schema_version,
            "browser_build": self.browser_version,
            "platform": self.platform,
            "locale_source": self.locale_source,
            "timezone_source": self.timezone_source,
            "identity": self.identity,
            "warnings": list(self.warnings),
        }
        optional = {
            "gpu_family": self.gpu_family,
            "evidence_class": self.evidence_class,
            "provenance": self.provenance,
            "family_file": self.family_file,
            "compatibility_acceptance": self.compatibility_acceptance,
            "acceptance_track": self.acceptance_track,
            "acceptance_status": self.acceptance_status,
            "validation_status": self.validation_status,
            "acceptance_record_id": self.acceptance_record_id,
        }
        if self.anchor is not None:
            optional["anchor"] = self.anchor
        for key, value in optional.items():
            if value is not None:
                output[key] = copy.deepcopy(value)
        return output


class DeterministicResolver:
    """Select a catalogue family with a stable SHA-256 keyed selector."""

    def __init__(self, catalogue: Mapping[str, Any] | str | Path | None = None,
                 *, browser_version: str = CHROMIUM_VERSION,
                 catalogue_version: int = CATALOGUE_VERSION) -> None:
        acceptance: dict[str, Any] | None
        if catalogue is None:
            loaded, base, acceptance = _load_catalogue()
        elif isinstance(catalogue, (str, Path)):
            path = Path(catalogue).expanduser()
            loaded = dict(_load_json(path, "profile catalogue"))
            acceptance = _load_acceptance(loaded, path.parent)
            base = path.parent
        else:
            loaded, base = dict(catalogue), None
            acceptance = None
            if "compatibility_acceptance" in loaded:
                raise ProfileError("inline profile catalogue cannot resolve compatibility acceptance assets")
        self.catalogue = loaded
        self._acceptance = acceptance
        actual_version = loaded.get("catalogue_version", CATALOGUE_VERSION)
        if actual_version != CATALOGUE_VERSION or catalogue_version != CATALOGUE_VERSION:
            raise ProfileError("profile catalogue catalogue_version does not match this package")
        self.catalogue_version = actual_version
        declared_schema_version = loaded.get("profile_schema_version", PROFILE_SCHEMA_VERSION)
        if declared_schema_version != PROFILE_SCHEMA_VERSION:
            raise ProfileError("profile catalogue profile_schema_version does not match this package")
        declared_version = loaded.get("version")
        if declared_version is not None and declared_version != self.catalogue_version:
            raise ProfileError("profile catalogue version does not match catalogue_version")
        self.profile_schema_version = declared_schema_version
        declared_build = loaded.get("browser_build")
        if declared_build is not None and declared_build != browser_version:
            raise ProfileError("profile catalogue browser_build does not match this package")
        supported = loaded.get("supported_browser_builds")
        if isinstance(supported, list) and browser_version not in supported:
            raise ProfileError("profile catalogue does not support this browser build")
        self.browser_version = browser_version
        self._families = _as_families(loaded, base, expected_catalogue_version=self.catalogue_version)

    def resolve(self, config: LaunchConfig, *, geoip: Mapping[str, Any] | None = None) -> ProfileResolution:
        profile_value = config.profile
        explicit_file = _profile_file(profile_value)
        warnings: list[str] = []
        family_metadata: dict[str, Any] | None = None
        if explicit_file is not None:
            selected = dict(explicit_file)
            profile_id = str(selected.get("id") or "explicit")
            selected_platform = _profile_platform(selected)
        elif isinstance(profile_value, Mapping):
            selected = dict(profile_value)
            profile_id = str(selected.get("id") or "explicit")
            selected_platform = _profile_platform(selected)
        elif isinstance(profile_value, str):
            matches = [item for item in self._families if item["id"] == profile_value]
            if not matches:
                raise ProfileError(f"unknown profile id: {profile_value}")
            family = matches[0]
            selected = _family_profile(family)
            profile_id = family["id"]
            selected_platform = family["platform"]
            family_metadata = _resolution_family_metadata(family, self._acceptance)
        elif profile_value is None and config.fingerprint is None and config.fingerprint_platform is None:
            selected = {}
            profile_id = "host-inherited"
            selected_platform = host_persona()
            warnings.append("host-inherited mode; no fingerprint, platform persona, or profile was supplied")
        else:
            platform = normalize_platform(config.fingerprint_platform) or host_persona()
            compatible = [item for item in self._families if item["platform"] == platform]
            if not compatible:
                raise ProfileError(f"profile catalogue has no {platform} family")
            seed = config.fingerprint if config.fingerprint is not None else "platform-default"
            index = _stable_index(seed, platform, self.catalogue_version, self.browser_version, len(compatible))
            family = compatible[index]
            selected = _family_profile(family)
            profile_id = family["id"]
            selected_platform = platform
            family_metadata = _resolution_family_metadata(family, self._acceptance)
            if config.fingerprint is None:
                warnings.append("selected versioned platform default; no fingerprint seed was supplied")

        if config.fingerprint_platform is not None and selected_platform is not None:
            requested = normalize_platform(config.fingerprint_platform)
            if requested != selected_platform:
                raise ProfileError("explicit profile platform does not match fingerprint_platform")
        platform = selected_platform or normalize_platform(config.fingerprint_platform) or host_persona()
        if not selected.get("id") and profile_id != "host-inherited":
            selected["id"] = profile_id

        profile_locale, profile_timezone = _nested_locale(selected)
        geo_locale = geoip.get("locale") if isinstance(geoip, Mapping) else None
        if not isinstance(geo_locale, str) and isinstance(geoip, Mapping):
            languages = geoip.get("languages")
            if isinstance(languages, (list, tuple)) and languages:
                geo_locale = ",".join(str(item) for item in languages)
        geo_timezone = geoip.get("timezone") if isinstance(geoip, Mapping) else None
        if config.locale is not None:
            locale = config.locale
            locale_source = "explicit"
        elif isinstance(geo_locale, str) and geo_locale:
            locale = geo_locale
            locale_source = "geoip-derived"
        else:
            locale = profile_locale
            locale_source = "profile-selected" if locale else "host"
        if config.timezone is not None:
            timezone = config.timezone
            timezone_source = "explicit"
        elif isinstance(geo_timezone, str) and geo_timezone:
            timezone = geo_timezone
            timezone_source = "geoip-derived"
        else:
            timezone = profile_timezone
            timezone_source = "profile-selected" if timezone else "host"
        if locale or timezone:
            value = selected.get("locale")
            locale_block = dict(value) if isinstance(value, Mapping) else {}
            if locale:
                locale_block["accept_languages"] = locale
            if timezone:
                locale_block["timezone"] = timezone
            selected["locale"] = locale_block

        identity_payload = {
            "profile_id": profile_id, "fingerprint": config.fingerprint, "platform": platform,
            "catalogue_version": self.catalogue_version, "browser_build": self.browser_version,
        }
        identity = hashlib.sha256(
            json.dumps(identity_payload, sort_keys=True, separators=(",", ":")).encode("utf-8")
        ).hexdigest()
        selected = validate_profile(selected)
        resolution_kwargs: dict[str, Any] = {"profile_schema_version": self.profile_schema_version}
        if family_metadata is not None:
            resolution_kwargs.update(family_metadata)
        return ProfileResolution(
            profile=selected, profile_id=profile_id, catalogue_version=self.catalogue_version,
            browser_version=self.browser_version, platform=platform, locale_source=locale_source,
            timezone_source=timezone_source, identity=identity, warnings=tuple(warnings),
            **resolution_kwargs,
        )

    def __call__(self, config: LaunchConfig) -> dict[str, Any]:
        return self.resolve(config).profile


def resolve(config: LaunchConfig, *, catalogue: Mapping[str, Any] | str | Path | None = None,
            browser_version: str = CHROMIUM_VERSION,
            geoip: Mapping[str, Any] | None = None) -> ProfileResolution:
    return DeterministicResolver(catalogue, browser_version=browser_version).resolve(config, geoip=geoip)


def resolve_profile(*, fingerprint: int | str | None = None, fingerprint_platform: str | None = None,
                    profile: Any = None, locale: str | None = None, timezone: str | None = None,
                    catalogue: Mapping[str, Any] | str | Path | None = None,
                    browser_version: str = CHROMIUM_VERSION,
                    geoip: Mapping[str, Any] | None = None) -> ProfileResolution:
    """Resolve a deterministic profile from the public launch selectors."""
    config = translate_options(
        fingerprint=fingerprint, fingerprint_platform=fingerprint_platform,
        profile=profile, locale=locale, timezone=timezone, geoip=False,
    )
    return resolve(config, catalogue=catalogue, browser_version=browser_version, geoip=geoip)


__all__ = ["DeterministicResolver", "ProfileResolution", "resolve", "resolve_profile"]
