#!/usr/bin/env python3
"""Fetch a missing pinned Chromium blob through Gitiles, verifying its Git hash.

This is a transport fallback for partial clones whose lazy Git fetch stalls.
It writes the verified object database entry; checkout remains Git's job.
"""

import argparse
import base64
import hashlib
from pathlib import Path
import subprocess
import urllib.parse
import urllib.request


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("path", help="repository-relative file at CHROMIUM_VERSION")
    args = parser.parse_args()
    repo = Path(__file__).resolve().parent.parent
    version = (repo / "build/CHROMIUM_VERSION").read_text().strip()
    git = ["git", "-C", str(args.src)]
    commit = subprocess.check_output(
        [*git, "rev-parse", f"refs/tags/{version}^{{commit}}"], text=True).strip()
    entry = subprocess.check_output(
        [*git, "ls-tree", "-z", commit, "--", args.path]).split(b"\0")
    if len(entry) != 2 or not entry[0]:
        parser.error("expected one pinned file")
    metadata, path = entry[0].split(b"\t", 1)
    mode, kind, expected = metadata.decode().split()
    if kind != "blob" or mode not in ("100644", "100755"):
        parser.error("expected a regular file")
    url = (f"https://chromium.googlesource.com/chromium/src/+/{commit}/"
           f"{urllib.parse.quote(path.decode(), safe='/')}?format=TEXT")
    with urllib.request.urlopen(url, timeout=60) as response:
        encoded = response.read(24 * 1024 * 1024 + 1)
    if len(encoded) > 24 * 1024 * 1024:
        parser.error("blob exceeds fallback size limit")
    data = base64.b64decode(encoded, validate=True)
    actual = hashlib.sha1(f"blob {len(data)}\0".encode() + data).hexdigest()
    if actual != expected:
        parser.error(f"object hash mismatch: expected {expected}, got {actual}")
    written = subprocess.check_output([*git, "hash-object", "-w", "--stdin"], input=data).decode().strip()
    if written != expected:
        parser.error("Git wrote an unexpected object hash")
    print(f"verified {version} {args.path}: {written} ({len(data)} bytes)")


if __name__ == "__main__":
    main()
