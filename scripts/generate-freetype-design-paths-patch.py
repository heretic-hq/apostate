#!/usr/bin/env python3
"""Generate 0049 from immutable Chromium/Skia in a private scratch.

Exact before/after source edits live in templates/freetype-design-path-edits.json.
No shared checkout is read or changed. --scratch retains a fresh private tree.
"""

import argparse
import base64
import concurrent.futures
import difflib
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "scripts/templates/freetype-design-path-edits.json"
HEADER = """Subject: [PATCH] fonts: preserve design-space paths for Mac profiles

FreeType rounds outline points at SkFont's 64px canonical path size before
Skia rescales them. For byte-identical Songti, this predicts every measured
18/40px bound error. Select design-space extraction with an immutable SkFont
flag supplied only by Blink's existing Mac-profile loader; no Profile in Skia.

The flag participates in SkFont identity/serialization and full scaler records
used by strike/path caches. Flagged unhinted scalable paths load at units_per_EM
size without NO_SCALE or NO_RECURSE. Keep the actual variation/composite loader,
restore size/transform, and use Skia's float transform after decomposition.
Synthetic bold, tricky fonts and color/SVG/sbix faces retain the original
path and operation order, including hybrid faces with monochrome outlines. Advance precision and the missing PingFang HVF provider are separate.

Reference: resources/fingerprints/raw/m4-max-chrome-20260908T163229Z.json and
same-version native/candidate named-face diagnostics. Candidate requires
integrated Skia V1, cache tests and native multi-size comparisons before V3.

"""


def generate(scratch, out):
    template = json.loads(TEMPLATE.read_text())
    paths = list(dict.fromkeys(edit["path"] for edit in template["edits"]))
    def fetch(path):
        if path.startswith("third_party/skia/"):
            url = (f"https://skia.googlesource.com/skia/+/{template['skia']}/"
                   f"{path.removeprefix('third_party/skia/')}?format=TEXT")
        else:
            url = (f"https://chromium.googlesource.com/chromium/src/+/{template['chromium']}/"
                   f"{path}?format=TEXT")
        data = base64.b64decode(urllib.request.urlopen(url, timeout=30).read())
        target = scratch / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    with concurrent.futures.ThreadPoolExecutor(max_workers=6) as pool:
        list(pool.map(fetch, paths))
    subprocess.run(["git", "init", "-q", str(scratch)], check=True)
    includes = ["--include=" + path for path in paths]
    for name in (ROOT / "patches/series").read_text().splitlines():
        if not name or name.startswith("#") or name[:4] >= "0049":
            continue
        subprocess.run(["git", "apply", *includes, str(ROOT / "patches" / name)],
                       cwd=scratch, check=True, capture_output=True)
    original = {path: (scratch / path).read_text() for path in paths}
    changed = dict(original)
    for edit in template["edits"]:
        path, old, new = edit["path"], edit["before"], edit["after"]
        if changed[path].count(old) != 1:
            raise ValueError(f"source anchor changed in {path}: {old[:80]!r}")
        changed[path] = changed[path].replace(old, new, 1)
    ft_path = "third_party/skia/src/ports/SkFontHost_FreeType.cpp"
    function = "SkScalerContext_FreeType::generatePath(const SkGlyph& glyph) {"
    tail = "\n    uint32_t flags = fLoadGlyphFlags;"
    def native_tail(source):
        start = source.index(function)
        start = source.index(tail, start)
        end = source.index("\nvoid SkScalerContext_FreeType::generateFontMetrics", start)
        return source[start:end]
    if native_tail(original[ft_path]) != native_tail(changed[ft_path]):
        raise ValueError("native generatePath fallback changed")
    diff = [HEADER]
    for path in paths:
        diff.append(f"diff --git a/{path} b/{path}\n")
        diff.extend(difflib.unified_diff(original[path].splitlines(True), changed[path].splitlines(True),
                                        fromfile="a/" + path, tofile="b/" + path))
    candidate = scratch / "0049.patch"
    candidate.write_text("".join(diff))
    subprocess.run(["git", "apply", "--check", str(candidate)], cwd=scratch, check=True)
    subprocess.run(["git", "apply", str(candidate)], cwd=scratch, check=True)
    subprocess.run(["git", "apply", "--reverse", "--check", str(candidate)], cwd=scratch, check=True)
    out.write_text(candidate.read_text())
    print(f"generated and checked {out}")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, default=ROOT / "patches/0049-mac-freetype-design-space-paths.patch")
    parser.add_argument("--scratch", type=Path, help="optional fresh directory for generated source")
    args = parser.parse_args()
    if args.scratch:
        scratch = args.scratch.resolve()
        scratch.mkdir(parents=True, exist_ok=False)
        generate(scratch, args.out)
    else:
        with tempfile.TemporaryDirectory(prefix="apostate-design-path-") as name:
            generate(Path(name), args.out)


if __name__ == "__main__":
    main()
