#!/usr/bin/env python3
"""Register a pinned, operator-supplied CDM in a fresh Chromium user directory.

Usage: scripts/provision-widevine.py --source /existing/WidevineCdm \
    --user-data-dir /fresh/browser-profile

Requires a browser built with enable_widevine=true. Validates the external
input before writing the standard Chromium component hint. Does not copy or
download CDM bytes. An identical hint is preserved; any conflict is an error.
Provisioning does not establish CDM initialization or encrypted playback.
"""

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile


PIN_FILE = Path(__file__).resolve().parents[1] / "build/widevine-local.json"
HINT_NAME = "latest-component-updated-widevine-cdm"


class ProvisionError(ValueError):
    """An input failed validation or existing browser state conflicts."""


def _versions(manifest, key):
    value = manifest.get(key)
    if not isinstance(value, str):
        raise ProvisionError(f"manifest {key} must be a comma-separated string")
    try:
        versions = [int(part.strip()) for part in value.split(",")]
    except ValueError as exc:
        raise ProvisionError(f"invalid manifest {key}") from exc
    if not versions or any(version < 1 for version in versions):
        raise ProvisionError(f"invalid manifest {key}")
    return versions


def validate_source(source, pin):
    source = Path(source).resolve(strict=True)
    if not source.is_dir():
        raise ProvisionError("CDM source must be a directory")
    if pin.get("format") != 1 or pin.get("platform") != "linux_x64":
        raise ProvisionError("unsupported CDM pin format or platform")
    manifest_path = source / "manifest.json"
    library = source / "_platform_specific/linux_x64/libwidevinecdm.so"
    for path in (manifest_path, library):
        if not path.resolve(strict=True).is_relative_to(source) or not path.is_file():
            raise ProvisionError(f"CDM file is not inside the supplied directory: {path}")
    manifest_bytes = manifest_path.read_bytes()
    if hashlib.sha256(manifest_bytes).hexdigest() != pin["manifest_sha256"]:
        raise ProvisionError("CDM manifest SHA-256 does not match build/widevine-local.json")
    manifest = json.loads(manifest_bytes)
    if not isinstance(manifest, dict) or manifest.get("version") != pin["version"]:
        raise ProvisionError("CDM manifest version does not match the pin")
    # Chromium .83 supports module4, enabled interfaces10/11, and hosts10..12.
    # See media/cdm/supported_cdm_versions.h at CHROMIUM_VERSION.
    for field, pin_key, supported in (
        ("x-cdm-module-versions", "module_versions", {4}),
        ("x-cdm-interface-versions", "interface_versions", {10, 11}),
        ("x-cdm-host-versions", "host_versions", {10, 11, 12}),
    ):
        actual = _versions(manifest, field)
        if actual != pin[pin_key] or not set(actual).intersection(supported):
            raise ProvisionError(f"unsupported or unpinned {field}")
    if manifest.get("x-cdm-codecs", "").split(",") != pin["codecs"]:
        raise ProvisionError("CDM codecs do not match the pin")
    if manifest.get("x-cdm-supported-encryption-schemes") != pin["encryption_schemes"]:
        raise ProvisionError("CDM encryption schemes do not match the pin")
    digest = hashlib.sha256()
    size = 0
    with library.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            size += len(chunk)
            digest.update(chunk)
    if size != pin["library_size"] or digest.hexdigest() != pin["library_sha256"]:
        raise ProvisionError("CDM library size or SHA-256 does not match the pin")
    return source


def _matching_hint(hint, expected):
    if hint.is_symlink() or not hint.is_file():
        raise ProvisionError(f"refusing non-file or symlink CDM hint: {hint}")
    try:
        actual = json.loads(hint.read_text())
    except (ValueError, UnicodeError) as exc:
        raise ProvisionError(f"existing CDM hint is invalid: {hint}") from exc
    if actual != expected:
        raise ProvisionError(f"existing CDM hint conflicts; leaving it unchanged: {hint}")


def provision(source, user_data_dir, pin=None):
    pin = json.loads(PIN_FILE.read_text()) if pin is None else pin
    source = validate_source(source, pin)
    user_dir = Path(user_data_dir).absolute()
    if user_dir.is_symlink():
        raise ProvisionError("user-data-dir must not be a symlink")
    user_dir = user_dir.resolve()
    if user_dir == source or source.is_relative_to(user_dir) or user_dir.is_relative_to(source):
        raise ProvisionError("CDM source and user-data-dir must be separate directories")
    for marker in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        if os.path.lexists(user_dir / marker):
            raise ProvisionError(f"user-data-dir is in use or has stale browser state: {marker}")
    component_dir = user_dir / "WidevineCdm"
    if component_dir.is_symlink():
        raise ProvisionError("refusing symlink component directory")
    hint = component_dir / HINT_NAME
    expected = {"Path": str(source)}
    if os.path.lexists(hint):
        _matching_hint(hint, expected)
        return {"status": "unchanged", "hint": str(hint), "version": pin["version"]}

    component_dir.mkdir(parents=True, exist_ok=True)
    temp_path = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8",
                                         dir=component_dir, prefix=".cdm-hint-",
                                         delete=False) as stream:
            temp_path = Path(stream.name)
            stream.write(json.dumps(expected, sort_keys=True) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        try:
            # link() publishes the complete file atomically and cannot replace
            # a hint created by another process after our initial check.
            os.link(temp_path, hint)
            status = "created"
        except FileExistsError:
            _matching_hint(hint, expected)
            status = "unchanged"
        directory_fd = os.open(component_dir, os.O_RDONLY)
        try:
            os.fsync(directory_fd)
        finally:
            os.close(directory_fd)
    finally:
        if temp_path is not None:
            temp_path.unlink(missing_ok=True)
    return {"status": status, "hint": str(hint), "version": pin["version"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", required=True, type=Path,
                        help="explicit existing operator-supplied WidevineCdm directory")
    parser.add_argument("--user-data-dir", required=True, type=Path,
                        help="fresh, inactive browser directory owned by the caller")
    args = parser.parse_args()
    try:
        print(json.dumps(provision(args.source, args.user_data_dir), sort_keys=True))
        return 0
    except (OSError, ValueError, KeyError, TypeError) as exc:
        print(f"Widevine provisioning failed: {exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
