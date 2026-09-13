#!/usr/bin/env python3
"""Deterministic, dependency-free profile catalogue resolver.

The catalogue contains normalized compatibility candidates.  This module keeps
catalogue metadata and provenance outside the profile object sent to Chromium:
``resolve()`` returns only fields accepted by ``config/profile.schema.json``;
``resolve_with_diagnostics()`` returns that payload plus a machine-readable
resolution envelope.

The selector is deliberately not a random-number generator.  Every choice is
an indexed SHA-256 digest of explicit inputs, so a process can resolve once at
its profile boundary and reuse the same result for its lifetime.
"""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
import os
import re
import sys
from pathlib import Path
from typing import Any, Mapping, Sequence

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CATALOGUE = REPO_ROOT / "resources" / "profiles" / "catalogue.json"
DEFAULT_PROFILE_SCHEMA = REPO_ROOT / "config" / "profile.schema.json"
CATALOGUE_VERSION = 1
PROFILE_SCHEMA_VERSION = 2
DEFAULT_PLATFORM = "macos"
SUPPORTED_EVIDENCE = {
    "physical-ground-truth",
    "compatibility-capture",
    "catalogue-value",
    "native-derived",
    "proxy-derived",
    "host-inherited",
}
PLATFORMS = {"macos", "windows", "linux"}
_PLATFORM_ALIASES = {
    "mac": "macos",
    "macos": "macos",
    "osx": "macos",
    "darwin": "macos",
    "win": "windows",
    "win32": "windows",
    "windows": "windows",
    "linux": "linux",
}
_POLICY_KINDS = ("display", "hardware", "locale", "theme")
_PROFILE_TOP_LEVEL = {
    "id",
    "source_capture",
    "cpu",
    "memory",
    "platform",
    "browser",
    "speech",
    "screen",
    "gl_limits",
    "gl_extensions",
    "gl_precisions",
    "webgpu",
    "gpu",
    "locale",
    "theme",
    "input",
    "audio",
    "media",
    "keyboard",
    "fonts",
}
_BUILD_RE = re.compile(r"^(\d+)\.(\d+)\.(\d+)\.(\d+)$")
_CHROME_UA_RE = re.compile(r"(?:Chrome|Chromium)/(\d+)(?:\.|\s|$)")
_LOCALE_RE = re.compile(r"^[A-Za-z]{2,3}(?:-[A-Za-z0-9]{2,8})*$")
_TIMEZONE_RE = re.compile(r"^(?:UTC|[A-Za-z0-9._+-]+(?:/[A-Za-z0-9._+-]+)*)$")
_FAMILY_ID_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")


class ResolverError(ValueError):
    """Raised when catalogue or launch inputs cannot produce a coherent profile."""


class ProfileValidationError(ResolverError):
    """Raised when a runtime profile does not satisfy the repository schema."""


def _type_matches(value: Any, expected: str) -> bool:
    if expected == "object":
        return isinstance(value, dict)
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


def _schema_path(path: str, key: Any) -> str:
    if isinstance(key, int):
        return f"{path}[{key}]"
    return f"{path}.{key}" if path != "$" else f"$.{key}"


def _canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _schema_errors(value: Any, schema: Mapping[str, Any], path: str = "$") -> list[str]:
    """Small JSON-Schema 2020-12 subset sufficient for profile.schema.json.

    Keeping this validator in the resolver avoids making a launch-time
    dependency on jsonschema.  It implements every assertion keyword used by
    the checked-in profile schema, including contains/minContains and dynamic
    additionalProperties.
    """
    errors: list[str] = []
    expected = schema.get("type")
    if expected is not None:
        choices = expected if isinstance(expected, list) else [expected]
        if not any(_type_matches(value, item) for item in choices):
            errors.append(f"{path}: expected {expected}")
            return errors

    if "const" in schema and value != schema["const"]:
        errors.append(f"{path}: expected {schema['const']!r}")
    if "enum" in schema and value not in schema["enum"]:
        errors.append(f"{path}: expected one of {schema['enum']!r}")

    if isinstance(value, dict):
        for required in schema.get("required", []):
            if required not in value:
                errors.append(f"{path}: missing required property {required!r}")
        properties = schema.get("properties", {})
        additional = schema.get("additionalProperties", True)
        for key, item in value.items():
            if key in properties:
                errors.extend(_schema_errors(item, properties[key], _schema_path(path, key)))
            elif additional is False:
                errors.append(f"{_schema_path(path, key)}: additional property is not allowed")
            elif isinstance(additional, dict):
                errors.extend(_schema_errors(item, additional, _schema_path(path, key)))
        property_names = schema.get("propertyNames")
        if isinstance(property_names, dict):
            for key in value:
                errors.extend(_schema_errors(key, property_names, f"{path} property name {key!r}"))

    if isinstance(value, list):
        item_schema = schema.get("items")
        if isinstance(item_schema, dict):
            for index, item in enumerate(value):
                errors.extend(_schema_errors(item, item_schema, _schema_path(path, index)))
        if schema.get("uniqueItems"):
            seen: set[str] = set()
            for index, item in enumerate(value):
                encoded = _canonical_json(item)
                if encoded in seen:
                    errors.append(f"{_schema_path(path, index)}: duplicate array item")
                seen.add(encoded)
        contains = schema.get("contains")
        if isinstance(contains, dict):
            matches = sum(not _schema_errors(item, contains, _schema_path(path, i)) for i, item in enumerate(value))
            minimum = schema.get("minContains", 1)
            maximum = schema.get("maxContains")
            if matches < minimum:
                errors.append(f"{path}: contains matched {matches}, minimum is {minimum}")
            if maximum is not None and matches > maximum:
                errors.append(f"{path}: contains matched {matches}, maximum is {maximum}")

    if isinstance(value, str):
        if "minLength" in schema and len(value) < schema["minLength"]:
            errors.append(f"{path}: shorter than minLength {schema['minLength']}")
        if "maxLength" in schema and len(value) > schema["maxLength"]:
            errors.append(f"{path}: longer than maxLength {schema['maxLength']}")
        if "pattern" in schema:
            try:
                matched = re.search(schema["pattern"], value)
            except re.error as exc:
                errors.append(f"{path}: invalid schema pattern: {exc}")
            else:
                if matched is None:
                    errors.append(f"{path}: does not match {schema['pattern']!r}")

    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if "minimum" in schema and value < schema["minimum"]:
            errors.append(f"{path}: below minimum {schema['minimum']}")
        if "maximum" in schema and value > schema["maximum"]:
            errors.append(f"{path}: above maximum {schema['maximum']}")
        if "exclusiveMinimum" in schema and value <= schema["exclusiveMinimum"]:
            errors.append(f"{path}: must be greater than {schema['exclusiveMinimum']}")

    if isinstance(value, (dict, list, str)):
        if "minItems" in schema and isinstance(value, list) and len(value) < schema["minItems"]:
            errors.append(f"{path}: fewer than minItems {schema['minItems']}")
        if "maxItems" in schema and isinstance(value, list) and len(value) > schema["maxItems"]:
            errors.append(f"{path}: more than maxItems {schema['maxItems']}")
    return errors


def _load_profile_schema(path: Path = DEFAULT_PROFILE_SCHEMA) -> Mapping[str, Any]:
    try:
        schema = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ResolverError(f"cannot read profile schema {path}: {exc}") from exc
    if not isinstance(schema, dict):
        raise ResolverError(f"profile schema {path} is not an object")
    return schema


def validate_profile(profile: Mapping[str, Any], schema_path: Path = DEFAULT_PROFILE_SCHEMA) -> bool:
    """Validate a native runtime profile against the checked-in profile schema.

    Returns ``True`` for a valid profile and raises ProfileValidationError for
    invalid data.  No catalogue metadata is accepted in this object.
    """
    if not isinstance(profile, dict):
        raise ProfileValidationError("profile must be a JSON object")
    errors = _schema_errors(profile, _load_profile_schema(schema_path))
    if errors:
        raise ProfileValidationError("; ".join(errors[:12]))
    return True


def _read_object(path: Path, what: str) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise ResolverError(f"cannot read {what} {path}: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise ResolverError(f"{what} {path} is not valid JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ResolverError(f"{what} {path} must contain a JSON object")
    return value


def _normalise_platform(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ResolverError("fingerprint_platform must be a non-empty string")
    platform = _PLATFORM_ALIASES.get(value.strip().lower())
    if platform is None:
        raise ResolverError(f"unsupported fingerprint platform {value!r}")
    return platform


def _platform_from_profile(profile: Mapping[str, Any]) -> str | None:
    name = ((profile.get("platform") or {}).get("name") if isinstance(profile.get("platform"), dict) else None)
    if not isinstance(name, str):
        return None
    lowered = name.strip().lower()
    if lowered in {"macos", "mac os", "mac os x", "darwin"}:
        return "macos"
    if lowered in {"windows", "win32", "win64"}:
        return "windows"
    if lowered == "linux":
        return "linux"
    return None


def _first(mapping: Mapping[str, Any], *keys: str, default: Any = None) -> Any:
    for key in keys:
        if key in mapping and mapping[key] is not None:
            return mapping[key]
    return default


def _normalise_config(config: Mapping[str, Any], overrides: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(config, Mapping):
        raise ResolverError("resolver config must be a JSON object")
    merged = dict(config)
    merged.update({key: value for key, value in overrides.items() if value is not None})
    aliases = {
        "fingerprint": ("seed",),
        "fingerprint_platform": ("platform",),
        "profile_id": ("profileId",),
        "profile_file": ("profile_path", "profileFile"),
        "browser_build": ("chromium_build", "browserBuild"),
        "fingerprint_locale": ("locale",),
        "fingerprint_timezone": ("timezone",),
    }
    for canonical, names in aliases.items():
        if canonical not in merged:
            for alias in names:
                if alias in merged and merged[alias] is not None:
                    merged[canonical] = merged[alias]
                    break
    return merged


def _coerce_seed(value: Any) -> str:
    if value is None:
        return "0"
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        raise ResolverError("fingerprint/seed must be an integer or non-empty string")
    text = str(value)
    if not text:
        raise ResolverError("fingerprint/seed must not be empty")
    return text


def _normalise_build(value: Any, catalogue: Mapping[str, Any]) -> str:
    if value is None:
        value = catalogue.get("browser_build")
    if not isinstance(value, str) or _BUILD_RE.fullmatch(value) is None:
        raise ResolverError("browser_build must be a Chromium four-component version")
    baseline = catalogue.get("browser_build")
    baseline_match = _BUILD_RE.fullmatch(baseline) if isinstance(baseline, str) else None
    if baseline_match and _BUILD_RE.fullmatch(value).group(1) != baseline_match.group(1):
        raise ResolverError(
            f"browser build {value} is incompatible with catalogue baseline {baseline}"
        )
    return value


def _digest_index(*parts: Any, size: int) -> int:
    key = "\x00".join(str(part) for part in parts)
    digest = hashlib.sha256(key.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % size


def _deep_merge(base: Mapping[str, Any], overlay: Mapping[str, Any]) -> dict[str, Any]:
    result = copy.deepcopy(dict(base))
    for key, value in overlay.items():
        if isinstance(value, dict) and isinstance(result.get(key), dict):
            result[key] = _deep_merge(result[key], value)
        else:
            result[key] = copy.deepcopy(value)
    return result


def _family_root(base_dir: Path) -> Path:
    return (base_dir / "families").resolve()


def _confined_family_path(base_dir: Path, family_root: Path, relative: Any) -> Path:
    if not isinstance(relative, str) or not relative or os.path.isabs(relative):
        raise ResolverError("catalogue family file must be a relative path")
    candidate = (base_dir / relative).resolve()
    try:
        confined = os.path.commonpath((str(candidate), str(family_root))) == str(family_root)
    except ValueError:
        confined = False
    if not confined or candidate.parent != family_root:
        raise ResolverError(f"catalogue family file escapes families/: {relative!r}")
    if candidate.suffix != ".json":
        raise ResolverError(f"catalogue family file must be JSON: {relative!r}")
    return candidate


def _validate_policy_entry(kind: str, entry: Any, index: int) -> None:
    path = f"policies.{kind}[{index}]"
    if not isinstance(entry, dict):
        raise ResolverError(f"{path} must be an object")
    policy_id = entry.get("id")
    if not isinstance(policy_id, str) or not _FAMILY_ID_RE.fullmatch(policy_id):
        raise ResolverError(f"{path}.id is invalid")
    platform = entry.get("platform")
    if platform != "all":
        platform = _normalise_platform(platform)
    if entry.get("evidence_class") not in SUPPORTED_EVIDENCE:
        raise ResolverError(f"{path}.evidence_class is invalid")
    value = entry.get("value")
    if not isinstance(value, dict):
        raise ResolverError(f"{path}.value must be an object")
    try:
        validate_profile(value)
    except ProfileValidationError as exc:
        raise ResolverError(f"{path}.value is not schema-valid: {exc}") from exc


def _validate_catalogue_index(catalogue: Mapping[str, Any]) -> None:
    if catalogue.get("catalogue_id") != "apostate":
        raise ResolverError("catalogue_id must be 'apostate'")
    if catalogue.get("catalogue_version") != CATALOGUE_VERSION or catalogue.get("version") != CATALOGUE_VERSION:
        raise ResolverError("unsupported catalogue version")
    if catalogue.get("profile_schema_version") != PROFILE_SCHEMA_VERSION:
        raise ResolverError("unsupported profile schema version")
    if not isinstance(catalogue.get("browser_build"), str):
        raise ResolverError("catalogue browser_build is missing")
    if _BUILD_RE.fullmatch(catalogue["browser_build"]) is None:
        raise ResolverError("catalogue browser_build is invalid")
    acceptance_binding = catalogue.get("compatibility_acceptance")
    if not isinstance(acceptance_binding, dict):
        raise ResolverError("catalogue compatibility_acceptance binding is missing")
    expected_acceptance_binding = {
        "file": "compatibility-acceptance.json",
        "schema": "ledger/schema/compatibility-acceptance.schema.json",
        "track": "V3-C/V4-C",
        "initial_status": "offered",
    }
    if any(acceptance_binding.get(key) != value for key, value in expected_acceptance_binding.items()):
        raise ResolverError("catalogue compatibility_acceptance binding is invalid")
    families = catalogue.get("families")
    if not isinstance(families, list) or not families:
        raise ResolverError("catalogue families must be a non-empty array")
    if catalogue.get("family_count") != len(families):
        raise ResolverError("catalogue family_count does not match families")
    ids: set[str] = set()
    for index, entry in enumerate(families):
        path = f"families[{index}]"
        if not isinstance(entry, dict):
            raise ResolverError(f"{path} must be an object")
        family_id = entry.get("id")
        if not isinstance(family_id, str) or _FAMILY_ID_RE.fullmatch(family_id) is None:
            raise ResolverError(f"{path}.id is invalid")
        if family_id in ids:
            raise ResolverError(f"duplicate family id {family_id!r}")
        ids.add(family_id)
        platform = _normalise_platform(entry.get("platform"))
        if platform == "linux":
            raise ResolverError(f"catalogue family {family_id} has unsupported Linux platform")
        if not isinstance(entry.get("file"), str):
            raise ResolverError(f"{path}.file is missing")
        if not isinstance(entry.get("gpu_family"), str) or not entry["gpu_family"]:
            raise ResolverError(f"{path}.gpu_family is missing")
        if entry.get("evidence_class") not in SUPPORTED_EVIDENCE:
            raise ResolverError(f"{path}.evidence_class is invalid")
        acceptance = entry.get("acceptance")
        if not isinstance(acceptance, dict):
            raise ResolverError(f"{path}.acceptance is missing")
        if acceptance.get("record_id") != family_id:
            raise ResolverError(f"{path}.acceptance.record_id does not match family id")
        if acceptance.get("status") not in {"offered", "validated", "provisional", "limited"}:
            raise ResolverError(f"{path}.acceptance.status is invalid")
        if acceptance.get("validation_status") not in {"validated", "provisional", "limited", "unvalidated"}:
            raise ResolverError(f"{path}.acceptance.validation_status is invalid")
    policies = catalogue.get("policies")
    if not isinstance(policies, dict):
        raise ResolverError("catalogue policies must be an object")
    for kind in _POLICY_KINDS:
        entries = policies.get(kind)
        if not isinstance(entries, list) or not entries:
            raise ResolverError(f"catalogue policies.{kind} must be non-empty")
        policy_ids: set[str] = set()
        for index, entry in enumerate(entries):
            _validate_policy_entry(kind, entry, index)
            if entry["id"] in policy_ids:
                raise ResolverError(f"duplicate {kind} policy id {entry['id']!r}")
            policy_ids.add(entry["id"])
    distributions = catalogue.get("distributions")
    if not isinstance(distributions, dict):
        raise ResolverError("catalogue distributions must be an object")
    for name, distribution in distributions.items():
        if not isinstance(distribution, dict):
            raise ResolverError(f"distribution {name!r} must be an object")
        dist_platform = _normalise_platform(distribution.get("platform"))
        members = distribution.get("family_ids")
        if not isinstance(members, list) or not members or any(member not in ids for member in members):
            raise ResolverError(f"distribution {name!r} has invalid family_ids")
        if any(family.get("platform") == dist_platform and family.get("id") not in members for family in families):
            raise ResolverError(f"distribution {name!r} omits a family for {dist_platform}")


def _as_families(catalogue: Mapping[str, Any], base_dir: Path | None = None) -> list[dict[str, Any]]:
    """Load and validate every family reference in a catalogue index.

    References are confined to the sibling ``families/`` directory.  The
    function intentionally reads every entry, even when a caller later picks
    one family, so missing or tampered catalogue files fail before launch.
    """
    if not isinstance(catalogue, Mapping):
        raise ResolverError("catalogue must be an object")
    _validate_catalogue_index(catalogue)
    base = Path(base_dir) if base_dir is not None else DEFAULT_CATALOGUE.parent
    base = base.resolve()
    root = _family_root(base)
    result: list[dict[str, Any]] = []
    for entry in catalogue["families"]:
        family_id = entry["id"]
        path = _confined_family_path(base, root, entry["file"])
        if not path.is_file():
            raise ResolverError(f"catalogue family file is missing: {path}")
        family = _read_object(path, f"catalogue family {family_id}")
        if family.get("id") != family_id:
            raise ResolverError(f"family file {path} id does not match catalogue entry {family_id!r}")
        expected_platform = _normalise_platform(entry["platform"])
        if family.get("platform") != expected_platform:
            raise ResolverError(f"family {family_id} platform does not match catalogue entry")
        if family.get("gpu_family") != entry["gpu_family"]:
            raise ResolverError(f"family {family_id} gpu_family does not match catalogue entry")
        if family.get("evidence_class") != entry["evidence_class"]:
            raise ResolverError(f"family {family_id} evidence_class does not match catalogue entry")
        if family.get("evidence_class") not in SUPPORTED_EVIDENCE:
            raise ResolverError(f"family {family_id} has invalid evidence_class")
        if not isinstance(family.get("provenance"), str) or not family["provenance"].strip():
            raise ResolverError(f"family {family_id} must include a provenance statement")
        anchor = family.get("anchor")
        if not isinstance(anchor, dict) or not anchor:
            raise ResolverError(f"family {family_id} must include classified anchor fields")
        for field, evidence in anchor.items():
            if not isinstance(field, str) or not field or evidence not in SUPPORTED_EVIDENCE:
                raise ResolverError(f"family {family_id} anchor {field!r} has invalid evidence class")
        profile = family.get("profile")
        if not isinstance(profile, dict):
            raise ResolverError(f"family {family_id} profile must be an object")
        forbidden_metadata = set(profile) - _PROFILE_TOP_LEVEL
        if forbidden_metadata:
            raise ResolverError(f"family {family_id} profile contains non-native fields: {sorted(forbidden_metadata)}")
        if profile.get("id") != family_id:
            raise ResolverError(f"family {family_id} profile.id must equal family id")
        if _platform_from_profile(profile) != expected_platform:
            raise ResolverError(f"family {family_id} profile platform disagrees with family")
        try:
            validate_profile(profile)
        except ProfileValidationError as exc:
            raise ResolverError(f"family {family_id} profile is not schema-valid: {exc}") from exc
        family["_acceptance"] = copy.deepcopy(entry["acceptance"])
        family = copy.deepcopy(family)
        family["_path"] = str(path)
        result.append(family)
    return result


def _load_catalogue(path: Path | str | None = None) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    catalogue_path = Path(path) if path is not None else DEFAULT_CATALOGUE
    catalogue_path = catalogue_path.resolve()
    catalogue = _read_object(catalogue_path, "catalogue")
    _validate_catalogue_index(catalogue)
    families = _as_families(catalogue, catalogue_path.parent)
    return catalogue, families


def load_catalogue(path: Path | str | None = None) -> dict[str, Any]:
    """Load and integrity-check the catalogue index and every family payload."""
    catalogue, _ = _load_catalogue(path)
    return catalogue


def validate_catalogue(path: Path | str | None = None) -> bool:
    """Return True only if all catalogue references and profiles validate."""
    _load_catalogue(path)
    return True


def _profile_mapping(value: Any) -> dict[str, Any] | None:
    if value is None:
        return None
    if not isinstance(value, Mapping):
        return None
    nested = value.get("profile")
    if isinstance(nested, Mapping):
        return dict(nested)
    return dict(value)


def _load_explicit_file(value: Any) -> dict[str, Any]:
    if not isinstance(value, (str, os.PathLike)):
        raise ResolverError("profile_file must be a path")
    path = Path(value)
    profile = _read_object(path, "profile file")
    nested = profile.get("profile")
    if isinstance(nested, dict):
        profile = nested
    try:
        validate_profile(profile)
    except ProfileValidationError as exc:
        raise ResolverError(f"explicit profile file is not schema-valid: {exc}") from exc
    return profile


def _family_by_id(families: Sequence[Mapping[str, Any]], family_id: str) -> dict[str, Any]:
    matches = [family for family in families if family.get("id") == family_id]
    if len(matches) != 1:
        raise ResolverError(f"unknown profile id {family_id!r}")
    return dict(matches[0])


def _family_for_profile(profile: Mapping[str, Any], families: Sequence[Mapping[str, Any]]) -> Mapping[str, Any] | None:
    profile_id = profile.get("id")
    if isinstance(profile_id, str):
        for family in families:
            if family.get("id") == profile_id:
                return family
    renderer = str((profile.get("gpu") or {}).get("unmasked_renderer", "")).lower()
    for family in families:
        token = str(family.get("gpu_family", "")).split("-")[0]
        if token and token in renderer:
            return family
    return None


def _policy_request(config: Mapping[str, Any], kind: str) -> Any:
    value = config.get(f"{kind}_policy")
    if value is not None:
        return value
    # Mapping-shaped direct values are accepted for callers that do not want to
    # mint a named policy.  Strings in locale/theme are reserved for overrides.
    direct = config.get(kind)
    if isinstance(direct, Mapping):
        return direct
    return None


def _choose_policy(
    catalogue: Mapping[str, Any],
    config: Mapping[str, Any],
    kind: str,
    platform: str,
    seed: str,
    browser_build: str,
    family_id: str,
) -> tuple[dict[str, Any], str]:
    candidates = [
        entry for entry in catalogue["policies"][kind]
        if entry.get("platform") in ("all", platform)
    ]
    candidates.sort(key=lambda entry: entry["id"])
    if not candidates:
        raise ResolverError(f"no {kind} policies support platform {platform}")
    requested = _policy_request(config, kind)
    if isinstance(requested, str):
        matches = [entry for entry in candidates if entry.get("id") == requested]
        if len(matches) != 1:
            raise ResolverError(f"unknown {kind} policy {requested!r} for platform {platform}")
        return copy.deepcopy(matches[0]), matches[0]["id"]
    if isinstance(requested, Mapping):
        value = dict(requested)
        try:
            validate_profile(value)
        except ProfileValidationError as exc:
            raise ResolverError(f"explicit {kind} policy is not schema-valid: {exc}") from exc
        return {"id": f"explicit-{kind}", "platform": platform, "evidence_class": "catalogue-value", "value": value}, f"explicit-{kind}"
    selected = candidates[_digest_index(seed, platform, CATALOGUE_VERSION, browser_build, family_id, kind, size=len(candidates))]
    return copy.deepcopy(selected), selected["id"]


def _apply_locale_overrides(profile: dict[str, Any], config: Mapping[str, Any]) -> str:
    locale = config.get("fingerprint_locale")
    timezone = config.get("fingerprint_timezone")
    if locale is not None:
        if not isinstance(locale, str) or _LOCALE_RE.fullmatch(locale) is None:
            raise ResolverError(f"invalid locale {locale!r}")
        current = str((profile.get("locale") or {}).get("accept_languages", ""))
        fallback = locale.split("-", 1)[0]
        accept_languages = f"{locale},{fallback}"
        if current and current.split(",", 1)[0].lower() == locale.lower():
            accept_languages = current
        profile.setdefault("locale", {})["accept_languages"] = accept_languages
    if timezone is not None:
        if not isinstance(timezone, str) or _TIMEZONE_RE.fullmatch(timezone) is None:
            raise ResolverError(f"invalid timezone {timezone!r}")
        profile.setdefault("locale", {})["timezone"] = timezone
    return "explicit" if locale is not None or timezone is not None else "profile-selected"


def _coherence_check(profile: Mapping[str, Any], platform: str, browser_build: str) -> None:
    actual_platform = _platform_from_profile(profile)
    if actual_platform is not None and actual_platform != platform:
        raise ResolverError(f"profile platform {actual_platform} conflicts with requested {platform}")
    screen = profile.get("screen") or {}
    if screen.get("hdr") is True and screen.get("color_gamut") == "srgb":
        raise ResolverError("HDR display cannot use sRGB gamut")
    input_section = profile.get("input") or {}
    if input_section.get("pointer_type") == "none" and input_section.get("hover") is True:
        raise ResolverError("pointer_type=none with hover=true is incoherent")
    browser = profile.get("browser") or {}
    user_agent = browser.get("user_agent")
    if isinstance(user_agent, str):
        matched = _CHROME_UA_RE.search(user_agent)
        build_match = _BUILD_RE.fullmatch(browser_build)
        if matched and build_match and matched.group(1) != build_match.group(1):
            raise ResolverError("profile user-agent major version conflicts with browser_build")


def _identity(
    profile: Mapping[str, Any],
    seed: str,
    platform: str,
    browser_build: str,
    catalogue: Mapping[str, Any],
    family_id: str,
    policy_ids: Mapping[str, str],
) -> tuple[str, str]:
    profile_digest = hashlib.sha256(_canonical_json(profile).encode("utf-8")).hexdigest()
    material = {
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "catalogue_version": catalogue["catalogue_version"],
        "browser_build": browser_build,
        "platform": platform,
        "seed": seed,
        "family_id": family_id,
        "policies": dict(sorted(policy_ids.items())),
        "profile_sha256": profile_digest,
    }
    digest = hashlib.sha256(_canonical_json(material).encode("utf-8")).hexdigest()
    return digest, profile_digest


def _resolve_internal(config: Mapping[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    cfg = _normalise_config(config or {}, overrides)
    catalogue_path = cfg.get("catalogue_path")
    catalogue, families = _load_catalogue(catalogue_path)

    # Validate an inline profile before any seed/family selection.  This is
    # intentionally done even when a higher-precedence profile file is present,
    # so malformed config.profile never reaches launch unnoticed.
    inline_profile = _profile_mapping(cfg.get("profile"))
    if cfg.get("profile") is not None and inline_profile is not None:
        try:
            validate_profile(inline_profile)
        except ProfileValidationError as exc:
            raise ResolverError(f"config.profile is not schema-valid: {exc}") from exc
    explicit_file = cfg.get("profile_file")
    file_profile = _load_explicit_file(explicit_file) if explicit_file is not None else None

    profile_id_value = cfg.get("profile_id")
    if profile_id_value is None and isinstance(cfg.get("profile"), str):
        profile_id_value = cfg["profile"]
    if profile_id_value is not None and (not isinstance(profile_id_value, str) or not _FAMILY_ID_RE.fullmatch(profile_id_value)):
        raise ResolverError("profile_id must be a catalogue family id")

    explicit_profile = file_profile if file_profile is not None else inline_profile
    id_family = _family_by_id(families, profile_id_value) if profile_id_value is not None else None
    inferred_family = _family_for_profile(explicit_profile, families) if explicit_profile is not None else None

    platform_value = cfg.get("fingerprint_platform")
    if platform_value is None:
        source_family = id_family or inferred_family
        if source_family is not None:
            platform = _normalise_platform(source_family["platform"])
        elif explicit_profile is not None:
            platform = _platform_from_profile(explicit_profile) or DEFAULT_PLATFORM
        else:
            platform = DEFAULT_PLATFORM
    else:
        platform = _normalise_platform(platform_value)
    if platform == "linux":
        raise ResolverError("catalogue has no Linux family; choose macos or windows")

    if id_family is not None and id_family.get("platform") != platform:
        raise ResolverError(f"profile_id {profile_id_value!r} is not available on {platform}")
    if explicit_profile is not None:
        profile_platform = _platform_from_profile(explicit_profile)
        if profile_platform is not None and profile_platform != platform:
            raise ResolverError(f"explicit profile platform {profile_platform} conflicts with {platform}")

    seed = _coerce_seed(_first(cfg, "fingerprint", "seed", default=None))
    browser_build = _normalise_build(cfg.get("browser_build"), catalogue)
    requested_catalogue_version = cfg.get("catalogue_version")
    if requested_catalogue_version is not None and requested_catalogue_version != catalogue["catalogue_version"]:
        raise ResolverError("requested catalogue_version does not match loaded catalogue")

    policy_ids: dict[str, str] = {}
    policy_entries: dict[str, dict[str, Any]] = {}
    if explicit_profile is not None:
        profile = copy.deepcopy(explicit_profile)
        family = inferred_family or id_family
        family_id = str(family["id"]) if family is not None else "explicit-profile"
        for kind in _POLICY_KINDS:
            policy_ids[kind] = "explicit-profile"
    else:
        family_candidates = sorted(
            [family for family in families if family.get("platform") == platform],
            key=lambda family: family["id"],
        )
        if not family_candidates:
            raise ResolverError(f"catalogue has no families for platform {platform}")
        if id_family is not None:
            family = id_family
        else:
            family = family_candidates[
                _digest_index(seed, platform, catalogue["catalogue_version"], browser_build, size=len(family_candidates))
            ]
        family_id = family["id"]
        profile = copy.deepcopy(family["profile"])
        for kind in _POLICY_KINDS:
            selected, policy_id = _choose_policy(
                catalogue, cfg, kind, platform, seed, browser_build, family_id
            )
            policy_entries[kind] = selected
            policy_ids[kind] = policy_id
            profile = _deep_merge(profile, selected["value"])

    locale_source = _apply_locale_overrides(profile, cfg)
    validate_profile(profile)
    _coherence_check(profile, platform, browser_build)
    identity_digest, profile_digest = _identity(
        profile, seed, platform, browser_build, catalogue, family_id, policy_ids
    )

    family_evidence = family.get("evidence_class", "user-supplied") if family is not None else "user-supplied"
    family_acceptance = family.get("_acceptance") if family is not None else None
    policy_provenance = {
        kind: (policy_entries[kind].get("evidence_class") if kind in policy_entries else "host-inherited")
        for kind in _POLICY_KINDS
    }
    warnings = [
        "family and catalogue policy values are normalized compatibility/catalogue values, not physical-device claims",
        "canvas, text, fonts, audio emitters, and platform APIs remain native-derived or host-inherited unless represented by a schema field",
    ]
    if cfg.get("fingerprint_platform") is None and explicit_profile is None:
        warnings.append(f"fingerprint_platform defaulted to {platform}")
    diagnostics = {
        "profile_id": f"apostate-{identity_digest[:24]}",
        "identity": f"sha256:{identity_digest}",
        "profile_sha256": profile_digest,
        "profile_schema_version": PROFILE_SCHEMA_VERSION,
        "catalogue_version": catalogue["catalogue_version"],
        "browser_build": browser_build,
        "platform": platform,
        "gpu_family": family.get("gpu_family", "explicit") if family is not None else "explicit",
        "family": family_id,
        "display": policy_ids["display"],
        "hardware": policy_ids["hardware"],
        "locale": policy_ids["locale"],
        "theme": policy_ids["theme"],
        "locale_source": locale_source,
        "timezone": (profile.get("locale") or {}).get("timezone"),
        "provenance": {"family": family_evidence, "policies": policy_provenance},
        "compatibility_acceptance": family_acceptance,
        "warnings": warnings,
    }
    native_loader_support = {
        "profile_schema": "config/profile.schema.json",
        "profile_schema_valid": True,
        "unsupported_fields": [],
        "native_derived_sections": ["platform", "browser", "gpu", "webgpu", "gl_limits", "gl_extensions", "screen", "cpu", "memory", "audio", "media", "fonts"],
        "metadata_only_fields": ["profile_id", "identity", "catalogue_version", "profile_schema_version", "browser_build", "gpu_family", "family", "display", "hardware", "locale_source", "compatibility_acceptance", "provenance", "warnings"],
    }
    return {"profile": profile, "diagnostics": diagnostics, "native_loader_support": native_loader_support}


def resolve_with_diagnostics(config: Mapping[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Resolve a profile and return the profile plus diagnostics metadata."""
    return _resolve_internal(config, **overrides)


def resolve_profile(config: Mapping[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Resolve and return only the native profile payload."""
    return _resolve_internal(config, **overrides)["profile"]


def resolve(config: Mapping[str, Any] | None = None, **overrides: Any) -> dict[str, Any]:
    """Short alias returning only the schema-valid native profile payload."""
    return resolve_profile(config, **overrides)


def list_catalogue(path: Path | str | None = None) -> dict[str, Any]:
    catalogue, families = _load_catalogue(path)
    return {
        "catalogue_version": catalogue["catalogue_version"],
        "profile_schema_version": catalogue["profile_schema_version"],
        "browser_build": catalogue["browser_build"],
        "families": [
            {
                "id": family["id"],
                "platform": family["platform"],
                "gpu_family": family["gpu_family"],
                "evidence_class": family["evidence_class"],
                "acceptance": copy.deepcopy(family.get("_acceptance")),
            }
            for family in sorted(families, key=lambda item: item["id"])
        ],
        "policies": {
            kind: [entry["id"] for entry in sorted(catalogue["policies"][kind], key=lambda item: item["id"])]
            for kind in _POLICY_KINDS
        },
    }


def _json_output(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n"


def _cli(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    operations = parser.add_mutually_exclusive_group(required=True)
    operations.add_argument("--resolve", action="store_true", help="resolve a profile")
    operations.add_argument("--list", action="store_true", help="list families and policies")
    operations.add_argument("--catalogue", action="store_true", help="print the validated catalogue index")
    parser.add_argument("--catalogue-path", type=Path)
    parser.add_argument("--fingerprint")
    parser.add_argument("--seed")
    parser.add_argument("--fingerprint-platform")
    parser.add_argument("--platform")
    parser.add_argument("--profile", help="catalogue profile id")
    parser.add_argument("--profile-id")
    parser.add_argument("--profile-file", type=Path)
    parser.add_argument("--fingerprint-locale")
    parser.add_argument("--locale")
    parser.add_argument("--fingerprint-timezone")
    parser.add_argument("--timezone")
    parser.add_argument("--display-policy")
    parser.add_argument("--hardware-policy")
    parser.add_argument("--locale-policy")
    parser.add_argument("--theme-policy")
    parser.add_argument("--browser-build")
    parser.add_argument("--runtime-only", action="store_true", help="emit only the native profile payload")
    args = parser.parse_args(argv)
    try:
        if args.catalogue_path is not None:
            catalogue_path = args.catalogue_path
        else:
            catalogue_path = None
        if args.catalogue:
            value = load_catalogue(catalogue_path)
        elif args.list:
            value = list_catalogue(catalogue_path)
        else:
            config = {
                "fingerprint": args.fingerprint if args.fingerprint is not None else args.seed,
                "fingerprint_platform": args.fingerprint_platform if args.fingerprint_platform is not None else args.platform,
                "profile_id": args.profile_id if args.profile_id is not None else args.profile,
                "profile_file": args.profile_file,
                "fingerprint_locale": args.fingerprint_locale if args.fingerprint_locale is not None else args.locale,
                "fingerprint_timezone": args.fingerprint_timezone if args.fingerprint_timezone is not None else args.timezone,
                "display_policy": args.display_policy,
                "hardware_policy": args.hardware_policy,
                "locale_policy": args.locale_policy,
                "theme_policy": args.theme_policy,
                "browser_build": args.browser_build,
                "catalogue_path": catalogue_path,
            }
            envelope = resolve_with_diagnostics(config)
            value = envelope["profile"] if args.runtime_only else envelope
        sys.stdout.write(_json_output(value))
        return 0
    except (ResolverError, OSError, TypeError, ValueError) as exc:
        sys.stderr.write(_json_output({"error": str(exc)}))
        return 2


if __name__ == "__main__":
    raise SystemExit(_cli())
