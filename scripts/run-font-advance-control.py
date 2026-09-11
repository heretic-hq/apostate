#!/usr/bin/env python3
"""Compile and run a bounded, read-only native font advance diagnostic."""
import argparse
import hashlib
import json
from pathlib import Path
import platform
import subprocess

ROOT = Path(__file__).resolve().parents[1]
PIN = "656cb777798fa420a13faba3758779e9ed6c4798"

def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--backend", choices=["coretext", "freetype"], required=True)
    p.add_argument("--font", type=Path, required=True)
    p.add_argument("--face", required=True, help="PostScript name for CoreText; face index for FreeType")
    p.add_argument("--codepoint", type=int, nargs="+", required=True)
    p.add_argument("--freetype-source", type=Path)
    p.add_argument("--freetype-build", type=Path)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args()
    if not all(0 < codepoint <= 0xFFFF for codepoint in args.codepoint):
        p.error("this control accepts one BMP codepoint")
    args.out.mkdir(parents=True, exist_ok=False)
    binary = args.out.resolve() / "advance-control"
    inputs = {"font_sha256": digest(args.font), "face": args.face, "codepoint": args.codepoint}
    if args.backend == "coretext":
        if platform.system() != "Darwin":
            p.error("CoreText requires macOS")
        source = ROOT / "scripts/fixtures/font-advance-coretext.mm"
        command = ["clang++", "-std=c++17", "-fobjc-arc", "-O2", str(source),
                   "-framework", "Foundation", "-framework", "CoreText", "-o", str(binary)]
    else:
        if args.freetype_source is None or args.freetype_build is None:
            p.error("FreeType requires an existing pinned source and build")
        revision = subprocess.check_output(["git", "-C", str(args.freetype_source),
                                             "rev-parse", "HEAD"], text=True).strip()
        if revision != PIN:
            p.error("FreeType source pin mismatch")
        int(args.face)
        library = args.freetype_build / "libfreetype.a"
        inputs.update(freetype_revision=revision, freetype_library_sha256=digest(library))
        source = ROOT / "scripts/fixtures/font-advance-freetype.cc"
        command = ["c++", "-std=c++17", "-O2", str(source),
                   "-I" + str(args.freetype_build / "include"),
                   "-I" + str(args.freetype_source / "include"), str(library), "-o", str(binary)]
    inputs["fixture_sha256"] = digest(source)
    receipt = dict(diagnostic_only=True, corpus_admission=False, backend=args.backend, inputs=inputs,
                   compilation=command, passed=False)
    try:
        with (args.out / "compile.log").open("w") as log:
            subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, check=True, timeout=60)
        receipt["binary_sha256"] = digest(binary)
        results = []
        with (args.out / "stderr.log").open("w") as log:
            for codepoint in args.codepoint:
                process = subprocess.run([str(binary), str(args.font.resolve()), args.face, str(codepoint)],
                                         stdout=subprocess.PIPE, stderr=log, text=True, check=True, timeout=30)
                result = json.loads(process.stdout)
                assert len(result["rows"]) == 12
                results.append(dict(codepoint=codepoint, **result))
        result = results[0] if len(results) == 1 else {"cases": results}
        (args.out / "result.json").write_text(json.dumps(result, indent=2) + "\n")
        receipt["passed"] = True
    finally:
        (args.out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

if __name__ == "__main__":
    main()
