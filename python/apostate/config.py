"""Canonical launch configuration and deterministic profile selection.

This module is intentionally dependency-free. It is the Python wrapper's
single translation point from user-facing snake_case options to the logical
launch contract shared by the other Apostate frontends.
"""

from __future__ import annotations

import hashlib
import json
import platform as host_platform
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Mapping

from .errors import ConfigurationError, ProfileError

PACKAGE_VERSION = "0.1.0"
CHROMIUM_VERSION = "152.0.7977.83"
CATALOGUE_VERSION = 1
PROFILE_SCHEMA_VERSION = 2
SUPPORTED_PLATFORMS = frozenset({"windows", "macos", "linux"})


def _json_value(value: Any) -> Any:
    try:
        json.dumps(value, sort_keys=True, separators=(",", ":"))
    except (TypeError, ValueError) as exc:
        raise ConfigurationError(f"launch option is not JSON-compatible: {exc}") from exc
    return value


def _string_or_none(name: str, value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ConfigurationError(f"{name} must be a non-empty string or None")
    return value


def normalize_platform(value: str | None) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ConfigurationError("fingerprint_platform must be a string or None")
    aliases = {
        "win": "windows", "win32": "windows", "darwin": "macos",
        "mac": "macos", "osx": "macos", "linux2": "linux",
    }
    normalized = aliases.get(value.strip().lower(), value.strip().lower())
    if normalized not in SUPPORTED_PLATFORMS:
        raise ConfigurationError(
            "fingerprint_platform must be one of windows, macos, or linux"
        )
    return normalized


def host_persona() -> str:
    system = host_platform.system().lower()
    if system == "windows":
        return "windows"
    if system == "darwin":
        return "macos"
    if system == "linux":
        return "linux"
    raise ConfigurationError(f"unsupported host platform: {system or 'unknown'}")


def _normalize_seed(value: int | str | None) -> int | str | None:
    if value is None:
        return None
    if isinstance(value, int) and not isinstance(value, bool):
        if value < 0:
            raise ConfigurationError("fingerprint must be nonnegative")
        return value
    if isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:-]*", value.strip()):
        return value.strip()
    if isinstance(value, str) and value.strip():
        raise ConfigurationError("fingerprint string must match [A-Za-z0-9][A-Za-z0-9._:-]*")
    raise ConfigurationError("fingerprint must be an integer, stable string, or None")

def _normalize_args(value: Any) -> list[str]:
    if value is None:
        return []
    if isinstance(value, (str, bytes)) or not isinstance(value, (list, tuple)):
        raise ConfigurationError("args must be a sequence of strings")
    args = []
    for item in value:
        if not isinstance(item, str):
            raise ConfigurationError("args must be a sequence of strings")
        args.append(item)
    return args


def _path_value(value: str | Path | None, name: str) -> str | None:
    if value is None:
        return None
    if isinstance(value, Path):
        return str(value.expanduser())
    if isinstance(value, str) and value:
        return value
    raise ConfigurationError(f"{name} must be a path-like string or None")


@dataclass(frozen=True)
class LaunchConfig:
    """Immutable canonical launch configuration."""

    fingerprint: int | str | None = None
    fingerprint_platform: str | None = None
    profile: Any = None
    locale: str | None = None
    timezone: str | None = None
    geoip: bool = True
    proxy: str | Mapping[str, Any] | None = None
    headless: bool = True
    user_data_dir: str | None = None
    args: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        object.__setattr__(self, "fingerprint", _normalize_seed(self.fingerprint))
        object.__setattr__(self, "fingerprint_platform", normalize_platform(self.fingerprint_platform))
        object.__setattr__(self, "locale", _string_or_none("locale", self.locale))
        object.__setattr__(self, "timezone", _string_or_none("timezone", self.timezone))
        if not isinstance(self.geoip, bool):
            raise ConfigurationError("geoip must be a boolean")
        if not isinstance(self.headless, bool):
            raise ConfigurationError("headless must be a boolean")
        if self.proxy is not None and not isinstance(self.proxy, (str, Mapping)):
            raise ConfigurationError("proxy must be a URL, mapping, or None")
        object.__setattr__(self, "user_data_dir", _path_value(self.user_data_dir, "user_data_dir"))
        object.__setattr__(self, "args", tuple(_normalize_args(self.args)))
        if self.profile is not None and not isinstance(self.profile, (str, Path, Mapping)):
            _json_value(self.profile)

    def to_dict(self) -> dict[str, Any]:
        return {
            "fingerprint": self.fingerprint,
            "fingerprint_platform": self.fingerprint_platform,
            "profile": self.profile,
            "locale": self.locale,
            "timezone": self.timezone,
            "geoip": self.geoip,
            "proxy": self.proxy,
            "headless": self.headless,
            "user_data_dir": self.user_data_dir,
            "args": list(self.args),
        }

    def canonical_json(self) -> str:
        return json.dumps(self.to_dict(), sort_keys=True, separators=(",", ":"), default=str)

    def resolver_identity(self, *, browser_version: str = CHROMIUM_VERSION,
                         catalogue_version: int = CATALOGUE_VERSION) -> str:
        payload = {
            "fingerprint": self.fingerprint,
            "fingerprint_platform": self.fingerprint_platform or host_persona(),
            "profile": self.profile,
            "catalogue_version": catalogue_version,
            "browser_version": browser_version,
            "profile_schema_version": PROFILE_SCHEMA_VERSION,
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        return hashlib.sha256(encoded).hexdigest()


Resolver = Callable[[LaunchConfig], Mapping[str, Any]]


def translate_options(
    *,
    fingerprint: int | str | None = None,
    fingerprint_platform: str | None = None,
    profile: Any = None,
    locale: str | None = None,
    timezone: str | None = None,
    geoip: bool = True,
    proxy: str | Mapping[str, Any] | None = None,
    headless: bool = True,
    user_data_dir: str | Path | None = None,
    args: list[str] | tuple[str, ...] | None = None,
    **unknown: Any,
) -> LaunchConfig:
    """Translate public Python keyword options into canonical snake_case."""
    if unknown:
        names = ", ".join(sorted(unknown))
        raise ConfigurationError(f"unsupported launch option(s): {names}")
    return LaunchConfig(
        fingerprint=fingerprint,
        fingerprint_platform=fingerprint_platform,
        profile=profile,
        locale=locale,
        timezone=timezone,
        geoip=geoip,
        proxy=proxy,
        headless=headless,
        user_data_dir=user_data_dir,
        args=tuple(_normalize_args(args)),
    )


def resolve_profile(config: LaunchConfig, resolver: Resolver | None = None) -> dict[str, Any] | None:
    """Resolve an explicit profile through an injectable resolver boundary."""
    if config.profile is None and resolver is None:
        return None
    if config.profile is not None:
        profile = config.profile
        if isinstance(profile, (str, Path)):
            path = Path(profile).expanduser()
            try:
                profile = json.loads(path.read_text(encoding="utf-8"))
            except OSError as exc:
                raise ProfileError(f"unable to read profile file {path}: {exc}") from exc
            except json.JSONDecodeError as exc:
                raise ProfileError(f"profile file is not valid JSON: {path}") from exc
        if not isinstance(profile, Mapping):
            raise ProfileError("profile must be a mapping or JSON profile path")
        return dict(profile)
    assert resolver is not None
    resolved = resolver(config)
    if not isinstance(resolved, Mapping):
        raise ProfileError("profile resolver must return a mapping")
    return dict(resolved)


__all__ = [
    "PACKAGE_VERSION", "CHROMIUM_VERSION", "CATALOGUE_VERSION",
    "PROFILE_SCHEMA_VERSION", "LaunchConfig", "Resolver", "host_persona",
    "normalize_platform", "resolve_profile", "translate_options",
]
