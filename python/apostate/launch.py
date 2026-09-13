"""Sync and async Patchright-compatible launch APIs for Apostate."""

from __future__ import annotations

import asyncio
import base64
import copy
import importlib
import json
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, Mapping
from urllib.parse import quote, urlsplit, urlunsplit

from .binary import ensure_binary
from .config import LaunchConfig, translate_options
from .errors import ConfigurationError, LaunchError, ProfileError
from .geoip import GeoIPResult, resolve_geoip
from .profile_validation import validate_profile
from .resolver import DeterministicResolver, ProfileResolution


def _proxy_url(value: str | Mapping[str, Any] | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        if not value.strip():
            raise ConfigurationError("proxy must be a non-empty URL")
        return value.strip()
    if not isinstance(value, Mapping):
        raise ConfigurationError("proxy must be a URL or mapping")
    server = value.get("server") or value.get("url")
    if not isinstance(server, str) or not server.strip():
        raise ConfigurationError("proxy mapping requires a non-empty server or url")
    parsed = urlsplit(server.strip())
    if not parsed.scheme or not parsed.hostname:
        raise ConfigurationError("proxy server must include a scheme and host")
    username, password = value.get("username"), value.get("password")
    if username is not None and not isinstance(username, str):
        raise ConfigurationError("proxy username must be a string")
    if password is not None and not isinstance(password, str):
        raise ConfigurationError("proxy password must be a string")
    userinfo = ""
    if username is not None:
        userinfo = quote(username, safe="")
        if password is not None:
            userinfo += ":" + quote(password, safe="")
        userinfo += "@"
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"{userinfo}{host}{port}", "", "", ""))


def _proxy_server_arg(value: str | Mapping[str, Any] | None) -> str | None:
    raw = _proxy_url(value)
    if raw is None:
        return None
    parsed = urlsplit(raw)
    if not parsed.hostname:
        raise ConfigurationError("proxy server host is required")
    host = parsed.hostname
    if ":" in host and not host.startswith("["):
        host = f"[{host}]"
    port = f":{parsed.port}" if parsed.port is not None else ""
    return urlunsplit((parsed.scheme, f"{host}{port}", "", "", ""))


def _playwright_proxy(value: str | Mapping[str, Any] | None) -> dict[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, Mapping):
        result = dict(value)
        server = result.get("server") or result.get("url")
        if not isinstance(server, str) or not server.strip():
            raise ConfigurationError("proxy mapping requires a non-empty server or url")
        result["server"] = server.strip()
        result.pop("url", None)
        for key in ("username", "password", "bypass"):
            if key in result and result[key] is not None and not isinstance(result[key], str):
                raise ConfigurationError(f"proxy {key} must be a string")
        return result
    raw = _proxy_url(value)
    assert raw is not None
    parsed = urlsplit(raw)
    server = _proxy_server_arg(raw)
    assert server is not None
    result: dict[str, Any] = {"server": server}
    if parsed.username is not None:
        result["username"] = parsed.username
    if parsed.password is not None:
        result["password"] = parsed.password
    return result


def _profile_payload(profile: Mapping[str, Any] | None) -> str | None:
    if profile is None:
        return None
    validated = validate_profile(profile)

    def native_value(value: Any) -> Any:
        if isinstance(value, Mapping):
            return {key: native_value(item) for key, item in value.items() if key != "source_capture"}
        if isinstance(value, list):
            return [native_value(item) for item in value]
        return copy.deepcopy(value)

    encoded = json.dumps(native_value(validated), sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return base64.b64encode(encoded).decode("ascii")


def _geoip_dict(value: GeoIPResult | Mapping[str, Any] | None) -> Mapping[str, Any] | None:
    if value is None:
        return None
    if isinstance(value, GeoIPResult):
        return value.to_dict()
    to_dict = getattr(value, "to_dict", None)
    if callable(to_dict):
        value = to_dict()
    if not isinstance(value, Mapping):
        raise ProfileError("geoip provider must return a mapping or GeoIPResult")
    return value


@dataclass(frozen=True)
class LaunchPlan:
    config: LaunchConfig
    profile: dict[str, Any]
    resolution: ProfileResolution | None
    geoip: GeoIPResult | None
    diagnostics: dict[str, Any]


def _resolve_plan(config: LaunchConfig, *, resolver: Any = None, catalogue: Any = None,
                  geoip_provider: Any = None, geoip_timeout: float = 10.0) -> LaunchPlan:
    network_result: GeoIPResult | None = None
    proxy_value = _proxy_url(config.proxy)
    if config.geoip and (config.locale is None or config.timezone is None):
        network_result = resolve_geoip(geoip_provider, proxy=proxy_value, timeout=geoip_timeout)
    network_mapping = _geoip_dict(network_result)

    resolution: ProfileResolution | None = None
    if resolver is None:
        if config.profile is None and config.fingerprint is None and config.fingerprint_platform is None:
            profile: dict[str, Any] = {}
            diagnostics: dict[str, Any] = {
                "profile_id": "host-inherited", "catalogue_version": None,
                "browser_build": None, "platform": None, "locale_source": "host",
                "timezone_source": "host", "warnings": ["host-inherited mode"],
            }
            locale = config.locale or (network_mapping or {}).get("locale")
            if not isinstance(locale, str):
                locale = ",".join((network_mapping or {}).get("languages") or []) or (network_mapping or {}).get("accept_languages")
            timezone = config.timezone or (network_mapping or {}).get("timezone")
            if locale or timezone:
                profile["locale"] = {
                    **({"accept_languages": locale} if locale else {}),
                    **({"timezone": timezone} if timezone else {}),
                }
            profile = validate_profile(profile)
            diagnostics["locale_source"] = "explicit" if config.locale else ("geoip-derived" if locale else "host")
            diagnostics["timezone_source"] = "explicit" if config.timezone else ("geoip-derived" if timezone else "host")
        else:
            resolution = DeterministicResolver(catalogue).resolve(config, geoip=network_mapping)
            profile = resolution.profile
            diagnostics = resolution.to_dict()
    elif isinstance(resolver, DeterministicResolver):
        resolution = resolver.resolve(config, geoip=network_mapping)
        profile = resolution.profile
        diagnostics = resolution.to_dict()
    elif callable(resolver):
        value = resolver(config)
        if isinstance(value, ProfileResolution):
            resolution = value
            profile = value.profile
            diagnostics = value.to_dict()
        else:
            if not isinstance(value, Mapping):
                raise ProfileError("profile resolver must return a mapping")
            profile = dict(value)
            diagnostics = {"profile_id": str(profile.get("id") or "custom"), "warnings": []}
    else:
        raise ConfigurationError("resolver must be callable or DeterministicResolver")
    # This second validation is intentional: custom resolvers are untrusted
    # integration points and no process may start before this check succeeds.
    profile = validate_profile(profile)
    return LaunchPlan(config=config, profile=profile, resolution=resolution,
                      geoip=network_result, diagnostics=diagnostics)


def _native_args(plan: LaunchPlan, *, persistent: bool = False) -> list[str]:
    config = plan.config
    args: list[str] = []
    if config.headless:
        args.append("--headless=new")
    args.extend(("--no-first-run", "--no-default-browser-check"))
    encoded = _profile_payload(plan.profile)
    if encoded:
        args.append("--apostate-profile=" + encoded)
    if config.user_data_dir and not persistent:
        args.append("--user-data-dir=" + config.user_data_dir)
    proxy = _proxy_server_arg(config.proxy)
    if proxy:
        args.append("--proxy-server=" + proxy)
    args.extend(config.args)
    return args


def _load_sync_backend() -> Any:
    for module_name in ("patchright.sync_api", "playwright.sync_api"):
        try:
            return importlib.import_module(module_name).sync_playwright
        except ModuleNotFoundError:
            continue
        except ImportError as exc:
            raise LaunchError(f"unable to import the {module_name.split('.')[0]} sync API") from exc
    raise LaunchError("launch requires a Patchright-compatible Playwright installation")


def _load_async_backend() -> Any:
    for module_name in ("patchright.async_api", "playwright.async_api"):
        try:
            return importlib.import_module(module_name).async_playwright
        except ModuleNotFoundError:
            continue
        except ImportError as exc:
            raise LaunchError(f"unable to import the {module_name.split('.')[0]} async API") from exc
    raise LaunchError("async launch requires a Patchright-compatible Playwright installation")


def _backend_error(exc: Exception) -> LaunchError:
    text = str(exc)
    # Playwright errors can echo the complete command line. Never expose a
    # credential-bearing proxy URL in a package exception.
    for token in ("http://", "https://", "socks5://", "socks5h://"):
        if token in text:
            text = text.split(token, 1)[0].rstrip() + " [proxy details redacted]"
            break
    return LaunchError(f"native Apostate browser launch failed: {text or 'unknown error'}")


def launch(*, fingerprint: int | str | None = None, fingerprint_platform: str | None = None,
           profile: Any = None, locale: str | None = None, timezone: str | None = None,
           geoip: bool = True, proxy: str | Mapping[str, Any] | None = None,
           headless: bool = True, user_data_dir: str | Path | None = None,
           args: list[str] | tuple[str, ...] | None = None,
           binary_path: str | Path | None = None, cache_dir: str | Path | None = None,
           manifest: Mapping[str, Any] | str | Path | None = None,
           public_key: bytes | str | Path | None = None,
           downloader: Callable[[str], Any] | None = None, resolver: Any = None,
           catalogue: Any = None, geoip_provider: Any = None, geoip_timeout: float = 10.0,
           **playwright_options: Any) -> Any:
    """Launch the native browser through a Patchright-compatible sync API."""
    config = translate_options(fingerprint=fingerprint, fingerprint_platform=fingerprint_platform,
                               profile=profile, locale=locale, timezone=timezone, geoip=geoip,
                               proxy=proxy, headless=headless, user_data_dir=user_data_dir, args=args)
    plan = _resolve_plan(config, resolver=resolver, catalogue=catalogue,
                         geoip_provider=geoip_provider, geoip_timeout=geoip_timeout)
    if binary_path is None:
        binary = ensure_binary(cache_dir=cache_dir, manifest=manifest, public_key=public_key,
                               downloader=downloader)
    else:
        binary = Path(binary_path).expanduser()
        if not binary.is_file():
            raise LaunchError(f"Apostate browser binary was not found: {binary}")
        if not os.access(binary, os.X_OK):
            raise LaunchError("Apostate browser binary is not executable")
    sync_playwright = _load_sync_backend()
    playwright = sync_playwright().start()
    launch_options = dict(playwright_options)
    launch_options.update(executable_path=str(binary), headless=config.headless, args=_native_args(plan))
    if config.proxy is not None and "proxy" not in launch_options:
        launch_options["proxy"] = _playwright_proxy(config.proxy)
    try:
        return playwright.chromium.launch(**launch_options)
    except Exception as exc:
        try:
            playwright.stop()
        except Exception:
            pass
        raise _backend_error(exc) from exc


def launch_context(*, context_options: Mapping[str, Any] | None = None, **options: Any) -> Any:
    """Launch a browser and create a non-persistent Playwright context."""
    context_options = dict(context_options or {})
    # Explicit context options are kept separate so canonical launch options
    # cannot accidentally become page-visible context configuration.
    browser = launch(**options)
    try:
        return browser.new_context(**context_options)
    except Exception as exc:
        try:
            browser.close()
        except Exception:
            pass
        raise _backend_error(exc) from exc


def launch_persistent_context(user_data_dir: str | Path, *, context_options: Mapping[str, Any] | None = None,
                              **options: Any) -> Any:
    """Launch a native persistent context backed by ``user_data_dir``."""
    path = Path(user_data_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    config = translate_options(
        fingerprint=options.pop("fingerprint", None),
        fingerprint_platform=options.pop("fingerprint_platform", None),
        profile=options.pop("profile", None), locale=options.pop("locale", None),
        timezone=options.pop("timezone", None), geoip=options.pop("geoip", True),
        proxy=options.pop("proxy", None), headless=options.pop("headless", True),
        user_data_dir=path, args=options.pop("args", None),
    )
    plan = _resolve_plan(config, resolver=options.pop("resolver", None),
                         catalogue=options.pop("catalogue", None),
                         geoip_provider=options.pop("geoip_provider", None),
                         geoip_timeout=options.pop("geoip_timeout", 10.0))
    binary_path = options.pop("binary_path", None)
    if binary_path is None:
        binary = ensure_binary(cache_dir=options.pop("cache_dir", None),
                               manifest=options.pop("manifest", None),
                               public_key=options.pop("public_key", None),
                               downloader=options.pop("downloader", None))
    else:
        binary = Path(binary_path).expanduser()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise LaunchError(f"Apostate browser binary is unavailable or not executable: {binary}")
    async_api = options.pop("_async", False)
    if options.get("user_data_dir") is not None:
        raise ConfigurationError("user_data_dir is the positional persistent-context path")
    launch_options = dict(context_options or {})
    launch_options.update(options)
    launch_options.update(executable_path=str(binary), headless=config.headless,
                          args=_native_args(plan, persistent=True), user_data_dir=str(path))
    if config.proxy is not None and "proxy" not in launch_options:
        launch_options["proxy"] = _playwright_proxy(config.proxy)
    sync_playwright = _load_sync_backend()
    playwright = sync_playwright().start()
    try:
        context = playwright.chromium.launch_persistent_context(**launch_options)
    except Exception as exc:
        try:
            playwright.stop()
        except Exception:
            pass
        raise _backend_error(exc) from exc
    return context


async def launch_async(**options: Any) -> Any:
    """Launch the native browser through a Patchright-compatible async API."""
    config = translate_options(fingerprint=options.pop("fingerprint", None),
                               fingerprint_platform=options.pop("fingerprint_platform", None),
                               profile=options.pop("profile", None), locale=options.pop("locale", None),
                               timezone=options.pop("timezone", None), geoip=options.pop("geoip", True),
                               proxy=options.pop("proxy", None), headless=options.pop("headless", True),
                               user_data_dir=options.pop("user_data_dir", None), args=options.pop("args", None))
    plan = _resolve_plan(config, resolver=options.pop("resolver", None), catalogue=options.pop("catalogue", None),
                         geoip_provider=options.pop("geoip_provider", None), geoip_timeout=options.pop("geoip_timeout", 10.0))
    binary_path = options.pop("binary_path", None)
    if binary_path is None:
        binary = ensure_binary(cache_dir=options.pop("cache_dir", None), manifest=options.pop("manifest", None),
                               public_key=options.pop("public_key", None), downloader=options.pop("downloader", None))
    else:
        binary = Path(binary_path).expanduser()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise LaunchError(f"Apostate browser binary is unavailable or not executable: {binary}")
    async_playwright = _load_async_backend()
    playwright = await async_playwright().start()
    launch_options = dict(options)
    launch_options.update(executable_path=str(binary), headless=config.headless, args=_native_args(plan))
    if config.proxy is not None and "proxy" not in launch_options:
        launch_options["proxy"] = _playwright_proxy(config.proxy)
    try:
        return await playwright.chromium.launch(**launch_options)
    except Exception as exc:
        try:
            await playwright.stop()
        except Exception:
            pass
        raise _backend_error(exc) from exc


async def launch_context_async(*, context_options: Mapping[str, Any] | None = None, **options: Any) -> Any:
    browser = await launch_async(**options)
    try:
        return await browser.new_context(**dict(context_options or {}))
    except Exception as exc:
        try:
            await browser.close()
        except Exception:
            pass
        raise _backend_error(exc) from exc


async def launch_persistent_context_async(user_data_dir: str | Path, *, context_options: Mapping[str, Any] | None = None,
                                          **options: Any) -> Any:
    path = Path(user_data_dir).expanduser()
    path.mkdir(parents=True, exist_ok=True)
    options["user_data_dir"] = path
    # Keep delegation explicit and awaitable; this avoids a second sync API
    # implementation and makes async failures observable at the same boundary.
    config = translate_options(fingerprint=options.pop("fingerprint", None), fingerprint_platform=options.pop("fingerprint_platform", None),
                               profile=options.pop("profile", None), locale=options.pop("locale", None), timezone=options.pop("timezone", None),
                               geoip=options.pop("geoip", True), proxy=options.pop("proxy", None), headless=options.pop("headless", True),
                               user_data_dir=path, args=options.pop("args", None))
    plan = _resolve_plan(config, resolver=options.pop("resolver", None), catalogue=options.pop("catalogue", None),
                         geoip_provider=options.pop("geoip_provider", None), geoip_timeout=options.pop("geoip_timeout", 10.0))
    binary_path = options.pop("binary_path", None)
    if binary_path is None:
        binary = ensure_binary(cache_dir=options.pop("cache_dir", None), manifest=options.pop("manifest", None),
                               public_key=options.pop("public_key", None), downloader=options.pop("downloader", None))
    else:
        binary = Path(binary_path).expanduser()
        if not binary.is_file() or not os.access(binary, os.X_OK):
            raise LaunchError(f"Apostate browser binary is unavailable or not executable: {binary}")
    async_playwright = _load_async_backend()
    playwright = await async_playwright().start()
    launch_options = dict(context_options or {})
    launch_options.update(options)
    launch_options.update(executable_path=str(binary), headless=config.headless,
                          args=_native_args(plan, persistent=True), user_data_dir=str(path))
    if config.proxy is not None and "proxy" not in launch_options:
        launch_options["proxy"] = _playwright_proxy(config.proxy)
    try:
        return await playwright.chromium.launch_persistent_context(**launch_options)
    except Exception as exc:
        try:
            await playwright.stop()
        except Exception:
            pass
        raise _backend_error(exc) from exc


__all__ = [
    "LaunchPlan", "launch", "launch_async", "launch_context", "launch_context_async",
    "launch_persistent_context", "launch_persistent_context_async",
]
