#!/usr/bin/env python3
"""Generate the test-only0051 patch in a fresh private checkout (never shared src)."""
import argparse
import base64
import difflib
import json
import re
from pathlib import Path
import subprocess
import tempfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CHROMIUM = "79460ebecaa5625e57a5fb679a735659e73dc687"
DAWN = "ab8827bc57b176eeaa89f71324130c02d4d41145"

def fetch(root, pin, path):
    return base64.b64decode(urllib.request.urlopen(f"{root}/+/{pin}/{path}?format=TEXT", timeout=30).read()).decode()

def generate(scratch, out):
    gn = fetch("https://chromium.googlesource.com/chromium/src", CHROMIUM, "gpu/BUILD.gn")
    dawn = json.loads(fetch("https://dawn.googlesource.com/dawn", DAWN, "src/dawn/dawn.json"))
    (scratch / "gpu").mkdir(parents=True)
    (scratch / "gpu/BUILD.gn").write_text(gn)
    subprocess.run(["git", "init", "-q", str(scratch)], check=True)
    for name in (ROOT / "patches/series").read_text().splitlines():
        if name and not name.startswith("#") and name[:4] < "0051":
            subprocess.run(["git", "apply", "--include=gpu/BUILD.gn", str(ROOT / "patches" / name)],
                           cwd=scratch, check=True, capture_output=True)
    gn = (scratch / "gpu/BUILD.gn").read_text()
    source = (ROOT / "scripts/templates/software-webgpu/check.cc").read_text()
    def camel(name):
        words = name.split()
        return words[0] + "".join(word[0].upper() + word[1:] for word in words[1:])
    fields = [camel(member["name"]) for member in dawn["limits"]["members"]]
    def pascal(name):
        return "".join(word[0].upper() + word[1:] for word in name.split())
    enums = {pascal(name): {pascal(v["name"]) for v in spec["values"]}
             for name, spec in dawn.items()
             if isinstance(spec, dict) and spec.get("category") in {"enum", "bitmask"}}
    for kind, value in re.findall(r"wgpu::(\w+)::(\w+)", source):
        if kind not in enums or value not in enums[kind]:
            raise ValueError(f"unknown pinned Dawn enum value {kind}::{value}")
    generated = "\n".join(f'  result.Set("{field}", static_cast<double>(value.{field}));' for field in fields)
    source = source.replace("  // GENERATED_LIMIT_FIELDS", generated)
    assert "GENERATED_LIMIT_FIELDS" not in source
    changes = {
        "gpu/BUILD.gn": (gn, gn + "\n" + (ROOT / "scripts/templates/software-webgpu/target.gn").read_text()),
        "gpu/apostate_dawn_software_check.cc": ("", source),
    }
    result = ["Subject: [PATCH] tests: validate a pinned software Core provider without unsafe APIs\n\n"
              "Standalone, test-only Linux diagnostic. No CPU-adapter admission or\n"
              "browser packaging change. The non-component executable restricts dlopen\n"
              "to a hash-verified SwiftShader library, requests real Core devices,\n"
              "checks limits and validation, and runs bounded compute/texture transfers.\n"
              "Vulkan layer activation is not inferred from a validation request.\n\n"]
    for path, (before, after) in changes.items():
        result.append(f"diff --git a/{path} b/{path}\n")
        if not before:
            result.append("new file mode 100644\n")
        result.extend(difflib.unified_diff(before.splitlines(True), after.splitlines(True),
                      fromfile="a/" + path if before else "/dev/null", tofile="b/" + path))
    candidate = scratch / "0051.patch"
    candidate.write_text("".join(result))
    for args in [["--check"], [], ["--reverse", "--check"]]:
        subprocess.run(["git", "apply", *args, str(candidate)], cwd=scratch, check=True)
    out.write_text(candidate.read_text())
    (scratch / "v0.json").write_text(json.dumps({"patch_applies_and_reverses": True,
        "limits_members_verified_against_pinned_dawn_json": fields,
        "wgpu_enum_values_verified_against_pinned_dawn_json": True,
        "compiled": False, "executed": False}, indent=2) + "\n")
    print(f"generated {out}; source-only V0 passed")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "patches/0051-safe-software-webgpu-check.patch")
    args = parser.parse_args()
    if args.scratch:
        scratch = args.scratch.resolve()
        scratch.mkdir(parents=True, exist_ok=False)
        generate(scratch, args.out)
    else:
        with tempfile.TemporaryDirectory(prefix="apostate-0051-") as work:
            generate(Path(work), args.out)
if __name__ == "__main__":
    main()
