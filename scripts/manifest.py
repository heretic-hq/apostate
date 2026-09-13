#!/usr/bin/env python3
"""Generate and validate deterministic Apostate release manifests.

This helper intentionally writes a release manifest separately from the legacy
build/MANIFEST.lock.  It has no Chromium or third-party dependencies and does
not include timestamps, absolute paths, or host-specific values.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Iterable, Mapping, Sequence

MANIFEST_VERSION = 1
_HEX_LENGTH = 64


def _is_digest(value: str) -> bool:
    return len(value) == _HEX_LENGTH and all(character in "0123456789abcdef" for character in value)


class ManifestError(ValueError):
    """Raised when manifest inputs are incomplete or inconsistent."""


def sha256_bytes(data: bytes) -> str:
    """Return the lowercase SHA-256 digest for *data*."""
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: os.PathLike[str] | str) -> str:
    """Hash a file without loading the complete file into memory."""
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    except OSError as error:
        raise ManifestError(f"cannot read {path}: {error}") from error
    return digest.hexdigest()


def _hash_directory(path: Path) -> str:
    """Hash directory files in sorted relative-path order."""
    digest = hashlib.sha256()
    files = sorted(item for item in path.rglob("*") if item.is_file())
    for item in files:
        relative = item.relative_to(path).as_posix().encode("utf-8")
        digest.update(relative)
        digest.update(b"\0")
        digest.update(bytes.fromhex(sha256_file(item)))
        digest.update(b"\0")
    return digest.hexdigest()


def sha256_path(path: os.PathLike[str] | str) -> str:
    """Hash a file or directory using a stable representation."""
    candidate = Path(path)
    if candidate.is_file():
        return sha256_file(candidate)
    if candidate.is_dir():
        return _hash_directory(candidate)
    raise ManifestError(f"missing output: {candidate}")


def _series_entries(series_path: Path, patch_root: Path) -> list[tuple[str, bytes]]:
    try:
        lines = series_path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ManifestError(f"cannot read patch series {series_path}: {error}") from error
    entries: list[tuple[str, bytes]] = []
    for line in lines:
        name = line.partition("#")[0].strip()
        if not name:
            continue
        patch = patch_root / name
        if not patch.is_file():
            raise ManifestError(f"missing patch listed in series: {name}")
        entries.append((name, patch.read_bytes()))
    if not entries:
        raise ManifestError(f"patch series has no patches: {series_path}")
    return entries


def patch_series_hashes(
    series_path: os.PathLike[str] | str,
    patch_root: os.PathLike[str] | str | None = None,
) -> dict[str, str]:
    """Return hashes for the series file and ordered patch contents.

    The contents digest includes each listed filename and NUL separators, so a
    reorder or rename is observable even when patch bytes are unchanged.
    """
    series = Path(series_path)
    root = Path(patch_root) if patch_root is not None else series.parent
    entries = _series_entries(series, root)
    contents = hashlib.sha256()
    for name, data in entries:
        contents.update(name.encode("utf-8"))
        contents.update(b"\0")
        contents.update(data)
        contents.update(b"\0")
    return {
        "patch_series_sha256": sha256_file(series),
        "patch_contents_sha256": contents.hexdigest(),
    }


def output_hashes(
    output_dir: os.PathLike[str] | str,
    outputs: Iterable[str | os.PathLike[str]],
) -> dict[str, str]:
    """Hash named outputs, rejecting missing paths and path escapes."""
    root = Path(output_dir)
    if not root.is_dir():
        raise ManifestError(f"missing output directory: {root}")
    names = sorted({Path(name).as_posix() for name in outputs})
    if not names:
        raise ManifestError("at least one output is required")
    result: dict[str, str] = {}
    for name in names:
        relative = Path(name)
        if relative.is_absolute() or ".." in relative.parts:
            raise ManifestError(f"output must be relative to output directory: {name}")
        candidate = root / relative
        if not candidate.is_file() and not candidate.is_dir():
            raise ManifestError(f"missing output: {candidate}")
        result[name] = sha256_path(candidate)
    return result


def _required_text(value: str, field: str) -> str:
    if not value or "\n" in value or "\r" in value:
        raise ManifestError(f"{field} must be a non-empty single-line value")
    return value


def build_manifest(
    *,
    target: str,
    chromium_version: str,
    chromium_revision: str,
    depot_tools_revision: str,
    args_file: os.PathLike[str] | str,
    toolchain: str,
    image: str,
    patch_series: os.PathLike[str] | str,
    patch_root: os.PathLike[str] | str | None = None,
    output_dir: os.PathLike[str] | str,
    outputs: Iterable[str | os.PathLike[str]],
) -> dict[str, object]:
    """Build a deterministic manifest from pinned source and output inputs."""
    values = {
        "target": target,
        "chromium_version": chromium_version,
        "chromium_revision": chromium_revision,
        "depot_tools_revision": depot_tools_revision,
        "toolchain": toolchain,
        "image": image,
    }
    for field, value in values.items():
        _required_text(value, field)
    args = Path(args_file)
    if not args.is_file():
        raise ManifestError(f"missing effective args: {args}")
    args_sha256 = sha256_file(args)
    manifest: dict[str, object] = {
        "manifest_version": MANIFEST_VERSION,
        **values,
        # Keep both names: args_sha256 matches build/MANIFEST.lock while the
        # explicit name makes clear this is the concatenated effective config.
        "args_sha256": args_sha256,
        "effective_args_sha256": args_sha256,
        "source_revision": chromium_revision,
        **patch_series_hashes(patch_series, patch_root),
        "outputs": output_hashes(output_dir, outputs),
    }
    return manifest


def canonical_json(manifest: Mapping[str, object]) -> bytes:
    """Serialize a manifest in the one stable on-disk representation."""
    return (json.dumps(manifest, ensure_ascii=True, sort_keys=True, separators=(",", ":")) + "\n").encode("utf-8")


def write_manifest(
    manifest: Mapping[str, object],
    destination: os.PathLike[str] | str,
    *,
    overwrite: bool = False,
) -> None:
    """Write a release manifest, never replacing the legacy MANIFEST.lock."""
    validate_manifest(manifest)
    path = Path(destination)
    if path.name == "MANIFEST.lock":
        raise ManifestError("refusing to write legacy build/MANIFEST.lock; use a release manifest path")
    if path.exists() and not overwrite:
        raise ManifestError(f"refusing to overwrite existing manifest: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(canonical_json(manifest))


def validate_manifest(manifest: Mapping[str, object]) -> None:
    """Validate required stable fields without needing a Chromium checkout."""
    required = {
        "manifest_version", "target", "chromium_version", "chromium_revision",
        "source_revision", "depot_tools_revision", "toolchain", "image",
        "args_sha256", "effective_args_sha256", "patch_series_sha256",
        "patch_contents_sha256", "outputs",
    }
    missing = sorted(required - set(manifest))
    if missing:
        raise ManifestError("manifest missing fields: " + ", ".join(missing))
    if manifest["manifest_version"] != MANIFEST_VERSION:
        raise ManifestError("unsupported manifest version")
    for field in required - {"manifest_version", "outputs"}:
        value = manifest[field]
        if not isinstance(value, str):
            raise ManifestError(f"invalid manifest field: {field}")
        if field.endswith("sha256") and not _is_digest(value):
            raise ManifestError(f"invalid manifest field: {field}")
    outputs = manifest["outputs"]
    if not isinstance(outputs, dict) or not outputs:
        raise ManifestError("manifest outputs must be a non-empty object")
    for name, digest in outputs.items():
        if (not isinstance(name, str) or not name or Path(name).is_absolute()
                or ".." in Path(name).parts or not isinstance(digest, str)
                or not _is_digest(digest)):
            raise ManifestError(f"invalid output hash: {name}")
def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    generate = subparsers.add_parser("generate", help="write a deterministic release manifest")
    generate.add_argument("--target", required=True)
    generate.add_argument("--chromium-version", required=True)
    generate.add_argument("--chromium-revision", required=True)
    generate.add_argument("--depot-tools-revision", required=True)
    generate.add_argument("--args-file", type=Path, required=True)
    generate.add_argument("--toolchain", required=True)
    generate.add_argument("--image", required=True)
    generate.add_argument("--patch-series", type=Path, required=True)
    generate.add_argument("--patch-root", type=Path)
    generate.add_argument("--output-dir", type=Path, required=True)
    generate.add_argument("--output", action="append", required=True, metavar="NAME")
    generate.add_argument("--manifest", type=Path, required=True)
    generate.add_argument("--overwrite", action="store_true")
    check = subparsers.add_parser("validate", help="validate a manifest JSON file")
    check.add_argument("manifest", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "generate":
            manifest = build_manifest(
                target=args.target,
                chromium_version=args.chromium_version,
                chromium_revision=args.chromium_revision,
                depot_tools_revision=args.depot_tools_revision,
                args_file=args.args_file,
                toolchain=args.toolchain,
                image=args.image,
                patch_series=args.patch_series,
                patch_root=args.patch_root,
                output_dir=args.output_dir,
                outputs=args.output,
            )
            write_manifest(manifest, args.manifest, overwrite=args.overwrite)
        else:
            validate_manifest(json.loads(args.manifest.read_text(encoding="utf-8")))
    except (ManifestError, OSError, json.JSONDecodeError) as error:
        print(f"manifest error: {error}", file=os.sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
