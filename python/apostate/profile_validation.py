"""Small JSON Schema validator for the shipped native profile contract.

The runtime has no third-party dependency.  When running from this checkout
we validate against the authoritative ``config/profile.schema.json``; the
same schema is bundled as package data for installed distributions.
"""

from __future__ import annotations

import importlib.resources
import json
from pathlib import Path
from typing import Any, Mapping

from .errors import ProfileError


def _schema_path() -> Path | None:
    source_schema = Path(__file__).resolve().parents[2] / "config" / "profile.schema.json"
    if source_schema.is_file():
        return source_schema
    try:
        resource = importlib.resources.files("apostate").joinpath("assets/profile.schema.json")
        # ``as_file`` is needed for zipped distributions, but all supported
        # package environments expose a normal file.  Keep the read path
        # simple and fail closed when it is unavailable.
        candidate = Path(str(resource))
        return candidate if candidate.is_file() else None
    except (OSError, TypeError):
        return None


def load_schema() -> Mapping[str, Any]:
    path = _schema_path()
    if path is None:
        raise ProfileError("authoritative profile schema is unavailable")
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ProfileError("authoritative profile schema is invalid") from exc
    if not isinstance(value, Mapping):
        raise ProfileError("authoritative profile schema is not an object")
    return value


def _type_ok(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, Mapping)
    if expected == "array":
        return isinstance(value, list)
    if expected == "string":
        return isinstance(value, str)
    if expected == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if expected == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if expected == "boolean":
        return isinstance(value, bool)
    if expected == "null":
        return value is None
    return True


def _validate(value: Any, schema: Mapping[str, Any], path: str) -> list[str]:
    errors: list[str] = []
    expected = schema.get("type")
    if isinstance(expected, list):
        if not any(_type_ok(value, item) for item in expected if isinstance(item, str)):
            errors.append(f"{path}: expected one of {expected}")
            return errors
    elif isinstance(expected, str) and not _type_ok(value, expected):
        errors.append(f"{path}: expected {expected}")
        return errors
    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: must equal {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: value is not permitted")
    if isinstance(value, str):
        if isinstance(schema.get("minLength"), int) and len(value) < schema["minLength"]:
            errors.append(f"{path}: is shorter than minLength")
        if isinstance(schema.get("maxLength"), int) and len(value) > schema["maxLength"]:
            errors.append(f"{path}: exceeds maxLength")
        pattern = schema.get("pattern")
        if isinstance(pattern, str):
            import re
            if re.fullmatch(pattern, value) is None:
                errors.append(f"{path}: does not match pattern")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if isinstance(schema.get("minimum"), (int, float)) and value < schema["minimum"]:
            errors.append(f"{path}: is below minimum")
        if isinstance(schema.get("maximum"), (int, float)) and value > schema["maximum"]:
            errors.append(f"{path}: exceeds maximum")
        if isinstance(schema.get("exclusiveMinimum"), (int, float)) and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: is not above exclusiveMinimum")
    if isinstance(value, list):
        if isinstance(schema.get("minItems"), int) and len(value) < schema["minItems"]:
            errors.append(f"{path}: has fewer than minItems")
        if isinstance(schema.get("maxItems"), int) and len(value) > schema["maxItems"]:
            errors.append(f"{path}: exceeds maxItems")
        item_schema = schema.get("items")
        if isinstance(item_schema, Mapping):
            for index, item in enumerate(value):
                errors.extend(_validate(item, item_schema, f"{path}[{index}]"))
        if schema.get("uniqueItems"):
            encoded = [json.dumps(item, sort_keys=True, separators=(",", ":")) for item in value]
            if len(set(encoded)) != len(encoded):
                errors.append(f"{path}: items must be unique")
        contains = schema.get("contains")
        if isinstance(contains, Mapping):
            matches = sum(not _validate(item, contains, f"{path}[]") for item in value)
            minimum = schema.get("minContains", 1)
            maximum = schema.get("maxContains")
            if matches < minimum:
                errors.append(f"{path}: contains too few matching items")
            if isinstance(maximum, int) and matches > maximum:
                errors.append(f"{path}: contains too many matching items")
    if isinstance(value, Mapping):
        required = schema.get("required", [])
        if isinstance(required, list):
            for name in required:
                if name not in value:
                    errors.append(f"{path}: missing required property {name!r}")
        properties = schema.get("properties", {})
        if not isinstance(properties, Mapping):
            properties = {}
        additional = schema.get("additionalProperties", True)
        for name, item in value.items():
            child = f"{path}.{name}" if path else str(name)
            if name in properties and isinstance(properties[name], Mapping):
                errors.extend(_validate(item, properties[name], child))
            elif additional is False:
                errors.append(f"{child}: additional property is not allowed")
            elif isinstance(additional, Mapping):
                errors.extend(_validate(item, additional, child))
        property_names = schema.get("propertyNames")
        if isinstance(property_names, Mapping):
            for name in value:
                errors.extend(_validate(name, property_names, f"{path} property name"))
    for keyword in ("allOf",):
        branches = schema.get(keyword)
        if isinstance(branches, list):
            for branch in branches:
                if isinstance(branch, Mapping):
                    errors.extend(_validate(value, branch, path))
    for keyword in ("anyOf", "oneOf"):
        branches = schema.get(keyword)
        if isinstance(branches, list) and branches:
            valid = [not _validate(value, branch, path) for branch in branches if isinstance(branch, Mapping)]
            if keyword == "anyOf" and not any(valid):
                errors.append(f"{path}: does not match any allowed schema")
            if keyword == "oneOf" and sum(valid) != 1:
                errors.append(f"{path}: does not match exactly one allowed schema")
    return errors


def validate_profile(profile: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(profile, Mapping):
        raise ProfileError("profile must be a JSON object")
    errors = _validate(profile, load_schema(), "$")
    if errors:
        detail = "; ".join(errors[:4])
        if len(errors) > 4:
            detail += f"; and {len(errors) - 4} more"
        raise ProfileError(f"profile does not match config/profile.schema.json: {detail}")
    return dict(profile)


__all__ = ["load_schema", "validate_profile"]
