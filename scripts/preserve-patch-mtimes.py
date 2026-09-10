#!/usr/bin/env python3
"""Keep timestamps only for byte-identical patch inputs after a clean reapply.

This never changes file contents. Changed, missing, symlinked or mode-changed
files retain their new timestamps so the build must reconsider them.
"""
import argparse
import hashlib
import json
from pathlib import Path
import os
import stat


def fingerprint(path):
    state = path.lstat()
    if not stat.S_ISREG(state.st_mode):
        return None
    return {"sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
            "mode": stat.S_IMODE(state.st_mode), "mtime_ns": state.st_mtime_ns}


def safe_path(root, relative):
    root = root.resolve(strict=True)
    if Path(relative).is_absolute() or ".." in Path(relative).parts:
        raise ValueError(f"invalid patch path: {relative}")
    path = root / relative
    if not path.resolve().is_relative_to(root):
        raise ValueError(f"patch path escapes source root: {relative}")
    return path


def snapshot(root, patches):
    paths = set()
    for patch in sorted(patches.glob("*.patch")):
        for line in patch.read_text().splitlines():
            if line.startswith(("+++ b/", "--- a/")):
                paths.add(line[6:].split("\t", 1)[0])
    result = {}
    for relative in sorted(paths):
        path = safe_path(root, relative)
        try:
            value = fingerprint(path)
        except FileNotFoundError:
            continue
        if value:
            result[relative] = value
    return result


def restore(root, saved):
    count = 0
    for relative, old in saved.items():
        path = safe_path(root, relative)
        try:
            current = fingerprint(path)
        except FileNotFoundError:
            continue
        if current and all(current[key] == old[key] for key in ("sha256", "mode")):
            state = path.stat()
            os.utime(path, ns=(state.st_atime_ns, old["mtime_ns"]), follow_symlinks=False)
            count += 1
    return count


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=["snapshot", "restore"])
    parser.add_argument("--src", type=Path, required=True)
    parser.add_argument("--state", type=Path, required=True)
    parser.add_argument("--patches", type=Path)
    args = parser.parse_args()
    root = args.src.resolve(strict=True)
    if args.mode == "snapshot":
        if not args.patches:
            parser.error("snapshot requires --patches")
        args.state.write_text(json.dumps(snapshot(root, args.patches)) + "\n")
    else:
        count = restore(root, json.loads(args.state.read_text()))
        print(f"Preserved timestamps for {count} byte-identical patch inputs")
