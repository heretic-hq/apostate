"""Proxy-aware GeoIP integration with an injectable provider boundary."""

from __future__ import annotations

import importlib
import inspect
import re
from dataclasses import dataclass
from typing import Any, Callable, Mapping, Protocol, Sequence
from urllib.parse import quote, urlsplit, urlunsplit

from .errors import GeoIPError, GeoIPUnavailableError


class GeoIPProvider(Protocol):
    def lookup(self, proxy: str | None = None, *, timeout: float = 10.0) -> Mapping[str, Any]:
        """Return a mapping containing locale and timezone for the exit IP."""


@dataclass(frozen=True)
class GeoIPResult:
    country_code: str | None = None
    region: str | None = None
    timezone: str | None = None
    locale: str | None = None
    languages: tuple[str, ...] = ()
    ip: str | None = None
    provider: str | None = None
    provider_version: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "country_code": self.country_code,
            "region": self.region,
            "timezone": self.timezone,
            "locale": self.locale,
            "languages": list(self.languages),
            "ip": self.ip,
            "provider": self.provider,
            "provider_version": self.provider_version,
        }


def _proxy_parts(proxy: str) -> tuple[str, str, int | None] | None:
    try:
        parsed = urlsplit(proxy)
        if not parsed.scheme or not parsed.hostname:
            return None
        host = parsed.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        return parsed.scheme, host, parsed.port
    except ValueError:
        return None


def redact_proxy(proxy: str | None) -> str | None:
    """Hide proxy usernames/passwords while retaining useful host context."""
    if proxy is None:
        return None
    parts = _proxy_parts(proxy)
    if parts is None:
        return "<proxy>"
    scheme, host, port = parts
    return urlunsplit((scheme, f"{host}{':' + str(port) if port is not None else ''}", "", "", ""))


def _provider_callable(provider: Any) -> Callable[..., Any]:
    if provider is None:
        try:
            module = importlib.import_module("scripts.geoip")
        except ImportError as exc:
            raise GeoIPUnavailableError(
                "geoip=True requires the repository GeoIP helper or an injected geoip_provider"
            ) from exc
        method = getattr(module, "resolve_prelaunch_geoip", None)
        if callable(method):
            return method
        provider = module
    if callable(provider):
        return provider
    for name in ("lookup", "resolve_prelaunch_geoip", "lookup_exit_ip", "resolve_geoip", "resolve"):
        method = getattr(provider, name, None)
        if callable(method):
            return method
    raise GeoIPUnavailableError("configured GeoIP provider has no lookup method")


def _call_provider(provider: Any, proxy: str | None, timeout: float) -> Any:
    method = _provider_callable(provider)
    try:
        parameters = inspect.signature(method).parameters
    except (TypeError, ValueError):
        parameters = {}
    kwargs: dict[str, Any] = {}
    if "geoip" in parameters:
        kwargs["geoip"] = True
    if "proxy" in parameters:
        kwargs["proxy"] = proxy
    if "timeout" in parameters:
        kwargs["timeout"] = timeout
    if kwargs:
        return method(**kwargs)
    try:
        return method(proxy, timeout)
    except TypeError:
        return method(proxy)


def _first(mapping: Mapping[str, Any], *names: str) -> Any:
    for name in names:
        value = mapping.get(name)
        if value is not None:
            return value
    return None


def normalize_result(value: Any) -> GeoIPResult:
    if isinstance(value, GeoIPResult):
        return value
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
    if not isinstance(value, Mapping):
        raise GeoIPError("GeoIP provider returned a non-object result")
    timezone = _first(value, "timezone", "time_zone", "tz")
    locale = _first(value, "locale", "language", "default_locale")
    languages = _first(value, "languages", "accept_languages")
    if isinstance(languages, str):
        languages = tuple(part.strip() for part in re.split(r"[,;]", languages) if part.strip())
    elif isinstance(languages, Sequence) and not isinstance(languages, (bytes, bytearray)):
        languages = tuple(str(part) for part in languages if part)
    else:
        languages = ()
    provider_name = value.get("provider") if isinstance(value.get("provider"), str) else None
    version = value.get("provider_version") if isinstance(value.get("provider_version"), str) else None
    return GeoIPResult(
        country_code=_first(value, "country_code", "countryCode", "country"),
        region=_first(value, "region", "region_code", "regionCode"),
        timezone=timezone if isinstance(timezone, str) else None,
        locale=locale if isinstance(locale, str) else None,
        languages=languages,
        ip=_first(value, "ip", "address", "exit_ip"),
        provider=provider_name,
        provider_version=version,
    )


def resolve_geoip(provider: Any = None, *, proxy: str | None = None,
                  timeout: float = 10.0, require_locale: bool = True,
                  require_timezone: bool = True) -> GeoIPResult:
    if timeout <= 0:
        raise GeoIPError("GeoIP timeout must be greater than zero")
    try:
        result = normalize_result(_call_provider(provider, proxy, timeout))
    except GeoIPError:
        raise
    except Exception as exc:
        detail = str(exc)
        if proxy:
            detail = detail.replace(proxy, redact_proxy(proxy) or "<proxy>")
        raise GeoIPError(
            f"GeoIP lookup failed for {redact_proxy(proxy) or 'direct network'}: {detail}"
        ) from exc
    missing = []
    if require_timezone and not result.timezone:
        missing.append("timezone")
    if require_locale and not result.locale and not result.languages:
        missing.append("locale")
    if missing:
        raise GeoIPError("GeoIP lookup did not provide " + " or ".join(missing))
    return result


__all__ = ["GeoIPProvider", "GeoIPResult", "redact_proxy", "normalize_result", "resolve_geoip"]
