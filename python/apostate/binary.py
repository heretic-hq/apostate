"""Verified Apostate binary discovery, cache, and extraction.

Only the standard library is used.  A release manifest is authenticated with
an Ed25519 SubjectPublicKeyInfo key before an artifact is considered.  The
artifact's SHA-256 is checked before extraction, and cached archives are
re-hashed on every cache hit.
"""

from __future__ import annotations

import base64
import copy
import hashlib
import importlib.resources
import json
import os
import platform
import re
import shutil
import stat
import subprocess
import tarfile
import tempfile
import urllib.error
import urllib.request
import zipfile
from pathlib import Path
from typing import Any, Callable, Mapping

from ._ed25519 import verify as verify_ed25519
from .config import CATALOGUE_VERSION, CHROMIUM_VERSION, PACKAGE_VERSION
from .errors import (
    BinaryError,
    BinaryNotFoundError,
    IntegrityError,
    ManifestError,
    SignatureVerificationError,
    UnpublishedArtifactError,
    UnsupportedArchiveError,
)

_PUBLIC_KEY_RESOURCE = "assets/public-key.txt"
_MANIFEST_RESOURCE = "assets/release-manifest.json"


def _default_cache_dir() -> Path:
    override = os.environ.get("APOSTATE_CACHE_DIR")
    if override:
        return Path(override).expanduser()
    if os.name == "nt":
        base = os.environ.get("LOCALAPPDATA") or os.environ.get("TEMP") or str(Path.home())
        return Path(base) / "apostate" / "cache"
    if platform.system() == "Darwin":
        return Path.home() / "Library" / "Caches" / "apostate"
    base = os.environ.get("XDG_CACHE_HOME")
    return (Path(base) if base else Path.home() / ".cache") / "apostate"


def target_platform(value: str | None = None) -> str:
    if value:
        normalized = value.strip().lower().replace("_", "-")
        aliases = {"darwin-arm64": "macos-arm64", "mac-arm64": "macos-arm64",
                   "osx-arm64": "macos-arm64", "win-x64": "windows-x64",
                   "linux-aarch64": "linux-arm64", "linux-amd64": "linux-x64"}
        normalized = aliases.get(normalized, normalized)
        if normalized in {"linux-x64", "linux-arm64", "macos-arm64", "windows-x64"}:
            return normalized
        raise BinaryError(f"unsupported binary target: {value}")
    system = platform.system().lower()
    machine = platform.machine().lower()
    arch = "arm64" if machine in {"arm64", "aarch64"} else "x64" if machine in {"x86_64", "amd64", "x86-64"} else machine
    if system == "darwin" and arch == "arm64":
        return "macos-arm64"
    if system == "linux" and arch in {"x64", "arm64"}:
        return f"linux-{arch}"
    if system == "windows" and arch == "x64":
        return "windows-x64"
    raise BinaryError(f"unsupported host binary target: {system}-{machine}")


def _read_resource(name: str) -> bytes:
    try:
        return importlib.resources.files("apostate").joinpath(name).read_bytes()
    except OSError as exc:
        raise ManifestError(f"package release asset is unavailable: {name}") from exc


def _load_public_key(value: bytes | str | Path | None) -> bytes | str:
    if value is None:
        return _read_resource(_PUBLIC_KEY_RESOURCE)
    if isinstance(value, Path):
        try:
            return value.read_bytes()
        except OSError as exc:
            raise SignatureVerificationError("configured release public key is unreadable") from exc
    if isinstance(value, str):
        path = Path(value).expanduser()
        if path.is_file():
            try:
                return path.read_bytes()
            except OSError as exc:
                raise SignatureVerificationError("configured release public key is unreadable") from exc
        return value
    return bytes(value)


def canonical_manifest_bytes(manifest: Mapping[str, Any]) -> bytes:
    """Return sorted compact UTF-8 bytes signed by release tooling."""
    value = copy.deepcopy(dict(manifest))
    value["signature"] = None
    return (json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True) + "\n").encode("utf-8")


def _decode_signature(value: Any) -> bytes:
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9+/]{86}==", value):
        raise SignatureVerificationError("release manifest signature must be padded RFC4648 base64")
    try:
        raw = base64.b64decode(value, validate=True)
    except (ValueError, base64.binascii.Error) as exc:
        raise SignatureVerificationError("release manifest signature is not valid base64") from exc
    if len(raw) != 64 or base64.b64encode(raw).decode("ascii") != value:
        raise SignatureVerificationError("release manifest signature must canonically decode to 64 bytes")
    return raw


def _read_manifest(value: Mapping[str, Any] | str | Path | None) -> dict[str, Any]:
    if value is None:
        raw = _read_resource(_MANIFEST_RESOURCE)
    elif isinstance(value, Mapping):
        raw = json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")
    else:
        path = Path(value).expanduser()
        try:
            raw = path.read_bytes()
        except OSError as exc:
            raise ManifestError("release manifest is unreadable") from exc
    try:
        parsed = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ManifestError("release manifest is not valid JSON") from exc
    if not isinstance(parsed, Mapping):
        raise ManifestError("release manifest must be a JSON object")
    status = parsed.get("status")
    if status is not None and status not in {"published", "unpublished"}:
        raise ManifestError("release manifest status must be published or unpublished")
    required = ("package_version", "chromium_version", "catalogue_version", "signature")
    missing = [key for key in required if key not in parsed]
    if missing:
        raise ManifestError("release manifest is missing " + ", ".join(missing))
    if parsed["package_version"] != PACKAGE_VERSION:
        raise ManifestError("release manifest package_version does not match this package")
    if parsed["chromium_version"] != CHROMIUM_VERSION:
        raise ManifestError("release manifest chromium_version does not match this package")
    if parsed["catalogue_version"] != CATALOGUE_VERSION:
        raise ManifestError("release manifest catalogue_version does not match this package")
    if status == "unpublished" or (
        (isinstance(parsed.get("artifacts"), (Mapping, list)) and not parsed["artifacts"])
        and "artifact" not in parsed
    ):
        raise UnpublishedArtifactError(
            "release manifest is unpublished; no package artifact is available"
        )
    has_artifacts = "artifacts" in parsed
    has_scalar = all(key in parsed for key in ("platform", "artifact", "sha256"))
    if not has_artifacts and not has_scalar:
        raise ManifestError("release manifest needs artifacts or scalar platform/artifact/sha256 fields")
    if has_artifacts and not isinstance(parsed["artifacts"], (Mapping, list)):
        raise ManifestError("release manifest artifacts must be an object or list")
    return dict(parsed)


def verify_manifest(manifest: Mapping[str, Any], public_key: bytes | str | Path | None = None) -> None:
    signature = _decode_signature(manifest.get("signature"))
    key = _load_public_key(public_key)
    if not verify_ed25519(signature, canonical_manifest_bytes(manifest), key):
        raise SignatureVerificationError("release manifest Ed25519 signature verification failed")

def _artifact_record(manifest: Mapping[str, Any], target: str, requested: Any = None) -> dict[str, Any]:
    if isinstance(requested, Mapping):
        record = dict(requested)
    elif all(key in manifest for key in ("platform", "artifact", "sha256")):
        if manifest.get("platform") != target:
            raise BinaryNotFoundError(f"release manifest is for {manifest.get('platform')}, not {target}")
        record = {key: manifest[key] for key in ("platform", "artifact", "sha256")}
        for key in ("name", "executable", "binary", "path", "url"):
            if key in manifest:
                record[key] = manifest[key]
    else:
        records = manifest["artifacts"]
        if isinstance(records, Mapping):
            record = records.get(target)
            if record is None:
                record = records.get("artifacts/" + target)
        else:
            matches = [item for item in records if isinstance(item, Mapping) and item.get("platform") == target]
            record = matches[0] if len(matches) == 1 else None
        if not isinstance(record, Mapping):
            raise BinaryNotFoundError(f"release manifest has no artifact for {target}")
        record = dict(record)
    if not isinstance(record.get("sha256"), str) or len(record["sha256"]) != 64:
        raise ManifestError(f"artifact metadata for {target} has no valid sha256")
    try:
        int(record["sha256"], 16)
    except ValueError as exc:
        raise ManifestError(f"artifact metadata for {target} has invalid sha256") from exc
    if not (record.get("url") or record.get("path") or record.get("artifact")):
        raise ManifestError(f"artifact metadata for {target} has no download location")
    return record


def _payload_bytes(value: Any) -> bytes:
    if isinstance(value, bytes):
        return value
    if isinstance(value, bytearray):
        return bytes(value)
    if isinstance(value, Path):
        return value.read_bytes()
    if hasattr(value, "read"):
        raw = value.read()
        return bytes(raw)
    if isinstance(value, str):
        return Path(value).read_bytes()
    raise BinaryError("binary downloader returned unsupported data")


def _hash_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_member(name: str) -> Path:
    candidate = Path(name)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise UnsupportedArchiveError("artifact archive contains an unsafe path")
    return candidate


def _extract_zip(archive: Path, destination: Path) -> None:
    try:
        with zipfile.ZipFile(archive) as source:
            for member in source.infolist():
                relative = _safe_member(member.filename)
                mode = (member.external_attr >> 16) & 0o170000
                if mode == stat.S_IFLNK:
                    raise UnsupportedArchiveError("artifact archive contains a symbolic link")
                target = destination / relative
                if member.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with source.open(member) as input_stream, target.open("wb") as output_stream:
                    shutil.copyfileobj(input_stream, output_stream)
                target.chmod(target.stat().st_mode | stat.S_IXUSR)
    except zipfile.BadZipFile as exc:
        raise UnsupportedArchiveError("artifact is not a valid zip archive") from exc


def _extract_tar(archive: Path, destination: Path) -> None:
    try:
        with tarfile.open(archive, mode="r:*") as source:
            for member in source.getmembers():
                relative = _safe_member(member.name)
                if member.issym() or member.islnk() or member.isdev():
                    raise UnsupportedArchiveError("artifact archive contains an unsafe tar member")
                target = destination / relative
                if member.isdir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                if not member.isfile():
                    raise UnsupportedArchiveError("artifact archive contains an unsupported member")
                target.parent.mkdir(parents=True, exist_ok=True)
                stream = source.extractfile(member)
                if stream is None:
                    raise UnsupportedArchiveError("artifact archive contains an unreadable member")
                with stream, target.open("wb") as output_stream:
                    shutil.copyfileobj(stream, output_stream)
                target.chmod(target.stat().st_mode | stat.S_IXUSR)
    except (tarfile.TarError, EOFError) as exc:
        raise UnsupportedArchiveError("artifact is not a supported tar archive") from exc


def _find_binary(root: Path, requested: str | None = None) -> Path:
    if requested:
        candidate = root / _safe_member(requested)
        if candidate.is_file():
            return candidate
        raise BinaryNotFoundError("artifact executable path is not present")
    candidates = []
    names = {"chrome", "chromium", "chromium-browser", "Chromium", "chrome.exe", "chromium.exe"}
    for path in root.rglob("*"):
        if path.is_file() and path.name in names:
            candidates.append(path)
    if len(candidates) != 1:
        raise BinaryNotFoundError("artifact does not contain exactly one recognized browser executable")
    return candidates[0]

def _materialize_binary(archive: Path, destination: Path, record: Mapping[str, Any]) -> Path:
    """Extract/select the executable from an already hash-verified archive."""
    name = str(record.get("executable") or record.get("binary") or "") or None
    lower_name = archive.name.lower()
    if lower_name.endswith(".part"):
        lower_name = lower_name[:-5]
    if lower_name.endswith(".zip"):
        _extract_zip(archive, destination)
        return _find_binary(destination, name)
    if lower_name.endswith((".tar", ".tar.gz", ".tgz", ".tar.bz2", ".tar.xz", ".tar.zst", ".zst")):
        try:
            _extract_tar(archive, destination)
        except (tarfile.ReadError, UnsupportedArchiveError) as exc:
            if lower_name.endswith((".zst", ".tar.zst")):
                raise UnsupportedArchiveError("zstandard archives require a stdlib-compatible tar reader") from exc
            raise
        return _find_binary(destination, name)
    return archive


def _archive_binary_hash(archive: Path, record: Mapping[str, Any]) -> str:
    """Compute the executable hash from the signed archive on every cache hit."""
    with tempfile.TemporaryDirectory(prefix="verify-", dir=str(archive.parent)) as temporary:
        selected = _materialize_binary(archive, Path(temporary), record)
        return _hash_file(selected)


def _redact_url(value: Any) -> str:
    return "configured artifact source"


class BinaryManager:
    """Resolve a verified release artifact into a deterministic cache path."""

    def __init__(self, *, cache_dir: str | Path | None = None,
                 manifest: Mapping[str, Any] | str | Path | None = None,
                 public_key: bytes | str | Path | None = None,
                 downloader: Callable[[str], Any] | None = None) -> None:
        self.cache_dir = Path(cache_dir).expanduser() if cache_dir is not None else _default_cache_dir()
        self.manifest_value = manifest
        self.public_key = public_key
        self.downloader = downloader

    def _manifest(self) -> dict[str, Any]:
        manifest = _read_manifest(self.manifest_value)
        verify_manifest(manifest, self.public_key)
        return manifest

    def _download(self, record: Mapping[str, Any]) -> bytes:
        source = record.get("path") or record.get("artifact") or record.get("url")
        if isinstance(source, (str, Path)) and Path(str(source)).is_file():
            try:
                return Path(str(source)).read_bytes()
            except OSError as exc:
                raise BinaryError("configured artifact source is unreadable") from exc
        try:
            if self.downloader is not None:
                return _payload_bytes(self.downloader(str(source)))
            with urllib.request.urlopen(str(source), timeout=30) as response:
                return response.read()
        except Exception as exc:
            if isinstance(exc, BinaryError):
                raise
            raise BinaryError(f"unable to download {_redact_url(source)}") from exc

    def _paths(self, target: str, manifest: Mapping[str, Any], record: Mapping[str, Any]) -> tuple[Path, Path, Path]:
        version = str(manifest["chromium_version"])
        root = self.cache_dir / version / target
        source = record.get("name") or record.get("artifact") or record.get("path") or record.get("url") or ""
        source_name = Path(str(source)).name
        archive_name = source_name or f"apostate-{version}-{target}.archive"
        return root, root / archive_name, root / "binary"


    def ensure(self, *, target: str | None = None, artifact: Mapping[str, Any] | str | Path | None = None) -> Path:
        target_name = target_platform(target)
        manifest = self._manifest()
        requested_record: Any = artifact if isinstance(artifact, Mapping) else None
        record = _artifact_record(manifest, target_name, requested_record)
        root, archive, binary = self._paths(target_name, manifest, record)
        root.mkdir(parents=True, exist_ok=True)
        expected_hash = str(record["sha256"]).lower()
        if isinstance(artifact, (str, Path)):
            source_path = Path(artifact).expanduser()
            if not source_path.is_file():
                raise BinaryNotFoundError("configured binary artifact is missing")
            data = source_path.read_bytes()
        else:
            data = None
        marker = root / "verified.json"
        archive_is_verified = False
        if archive.is_file() and not archive.is_symlink():
            try:
                archive_is_verified = _hash_file(archive) == expected_hash
            except OSError:
                archive_is_verified = False
        if archive_is_verified and binary.is_file() and not binary.is_symlink() and marker.is_file():
            try:
                marker_data = json.loads(marker.read_text(encoding="utf-8"))
                archive_binary_sha = _archive_binary_hash(archive, record)
                binary_sha = _hash_file(binary)
            except (OSError, BinaryError, json.JSONDecodeError):
                marker_data = {}
                archive_binary_sha = None
                binary_sha = None
            if (
                isinstance(marker_data, Mapping)
                and marker_data.get("artifact_sha256") == expected_hash
                and marker_data.get("archive_binary_sha256") == archive_binary_sha
                and marker_data.get("binary_sha256") == archive_binary_sha
                and binary_sha == archive_binary_sha
            ):
                return binary
        if data is None and archive_is_verified:
            try:
                data = archive.read_bytes()
            except OSError:
                data = None
        if data is None:
            data = self._download(record)
        actual = hashlib.sha256(data).hexdigest()
        if actual != expected_hash:
            raise IntegrityError("downloaded Apostate artifact failed SHA-256 verification")
        temporary_archive = root / (archive.name + ".part")
        try:
            temporary_archive.write_bytes(data)
            # Manifest authentication has already succeeded; hash verification
            # above completes authenticity/integrity checks before extraction.
            with tempfile.TemporaryDirectory(prefix="extract-", dir=str(root)) as temporary:
                extracted = Path(temporary)
                selected = _materialize_binary(temporary_archive, extracted, record)
                staged = root / "binary.part"
                shutil.copyfile(selected, staged)
                staged.chmod(staged.stat().st_mode | stat.S_IXUSR)
                binary_sha = _hash_file(staged)
                os.replace(staged, binary)
            os.replace(temporary_archive, archive)
            marker.write_text(json.dumps({
                "format": 1,
                "package_version": manifest["package_version"],
                "chromium_version": manifest["chromium_version"],
                "platform": target_name,
                "artifact_sha256": expected_hash,
                "archive_binary_sha256": binary_sha,
                "binary_sha256": binary_sha,
            }, sort_keys=True, separators=(",", ":")) + "\n", encoding="utf-8")
            return binary
        except Exception:
            for path in (temporary_archive, root / "binary.part"):
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
            raise

    def info(self, *, target: str | None = None) -> dict[str, Any]:
        target_name = target_platform(target)
        manifest = self._manifest()
        try:
            record = _artifact_record(manifest, target_name)
        except BinaryError:
            return {"available": False, "platform": target_name, "chromium_version": manifest["chromium_version"]}
        root, archive, binary = self._paths(target_name, manifest, record)
        available = archive.is_file() and binary.is_file()
        verified = False
        if available:
            try:
                verified = _hash_file(archive) == record["sha256"]
            except OSError:
                verified = False
        return {
            "available": available and verified,
            "verified": verified,
            "platform": target_name,
            "chromium_version": manifest["chromium_version"],
            "package_version": manifest["package_version"],
            "catalogue_version": manifest["catalogue_version"],
            "path": str(binary) if available else None,
            "sha256": record["sha256"],
        }

    def clear(self) -> None:
        if self.cache_dir.is_symlink():
            raise BinaryError("refusing to clear a symlink cache directory")
        if self.cache_dir.exists():
            shutil.rmtree(self.cache_dir)


def ensure_binary(*, target: str | None = None, cache_dir: str | Path | None = None,
                  manifest: Mapping[str, Any] | str | Path | None = None,
                  public_key: bytes | str | Path | None = None,
                  downloader: Callable[[str], Any] | None = None,
                  artifact: Mapping[str, Any] | str | Path | None = None) -> Path:
    return BinaryManager(cache_dir=cache_dir, manifest=manifest, public_key=public_key,
                         downloader=downloader).ensure(target=target, artifact=artifact)


def binary_info(*, target: str | None = None, cache_dir: str | Path | None = None,
                manifest: Mapping[str, Any] | str | Path | None = None,
                public_key: bytes | str | Path | None = None) -> dict[str, Any]:
    return BinaryManager(cache_dir=cache_dir, manifest=manifest, public_key=public_key).info(target=target)


def clear_cache(*, cache_dir: str | Path | None = None) -> None:
    BinaryManager(cache_dir=cache_dir).clear()


__all__ = [
    "BinaryManager", "binary_info", "canonical_manifest_bytes", "clear_cache",
    "ensure_binary", "target_platform", "verify_manifest",
]
