#!/usr/bin/env python3
"""Generate conditional production0056 from immutable pins and prior patch prefix.

No shared generated source is read or edited. The candidate is not a V3 closure.
"""
import argparse
import base64
import difflib
import json
from pathlib import Path
import subprocess
import tempfile
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
TEMPLATES = ROOT / "scripts/templates/software-webgpu-admission"

def generate(scratch, output):
    specification = json.loads((TEMPLATES / "edits.json").read_text())
    paths = list(dict.fromkeys(edit["path"] for edit in specification["edits"]))
    paths += [path for path in specification["new_files"] if path not in paths]
    for path in paths:
        if path in specification["new_files"]:
            continue
        if path.startswith("third_party/dawn/"):
            root, pin, relative = "https://dawn.googlesource.com/dawn", specification["dawn"], path.removeprefix("third_party/dawn/")
        else:
            root, pin, relative = "https://chromium.googlesource.com/chromium/src", specification["chromium"], path
        try:
            content = base64.b64decode(urllib.request.urlopen(f"{root}/+/{pin}/{relative}?format=TEXT", timeout=30).read())
        except urllib.error.HTTPError as error:
            # This authored test source is introduced by0051, not upstream.
            if error.code == 404 and path == "gpu/apostate_dawn_software_check.cc":
                continue
            raise
        target = scratch / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
    subprocess.run(["git", "init", "-q", str(scratch)], check=True)
    includes = ["--include=" + path for path in paths]
    prefix = []
    for name in (ROOT / "patches/series").read_text().splitlines():
        if not name or name.startswith("#") or name[:4] >= "0056":
            continue
        subprocess.run(["git", "apply", *includes, str(ROOT / "patches" / name)], cwd=scratch,
                       capture_output=True, check=True)
        prefix.append(name)
    # Root may not have admitted the test candidate to series yet; apply only
    # its new source hunk when needed, keeping generation independent of admission.
    test_source = scratch / "gpu/apostate_dawn_software_check.cc"
    if not test_source.exists():
        subprocess.run(["git", "apply", "--include=gpu/apostate_dawn_software_check.cc",
                        str(ROOT / "patches/0051-safe-software-webgpu-check.patch")], cwd=scratch, check=True)
        prefix.append("0051-safe-software-webgpu-check.patch (test source only)")
    before = {path: (scratch / path).read_text() if (scratch / path).exists() else "" for path in paths}
    after = dict(before)
    for edit in specification["edits"]:
        path, old, new = edit["path"], edit["before"], edit["after"]
        if after[path].count(old) != 1:
            raise ValueError("source anchor is not unique: " + path + " " + old[:90])
        after[path] = after[path].replace(old, new, 1)
    for path, template in specification["new_files"].items():
        if after[path]: raise ValueError("new file already exists: " + path)
        after[path] = (ROOT / template).read_text()
    header = """Subject: [PATCH] webgpu: admit an exclusive software provider for selected profiles

Candidate conditional on safe Core and browser shared-image validation.
For Windows/macOS profiles on an actual Linux ANGLE SwiftShader GL context,
select only the bundled SwiftShader ICD through an exclusive native Dawn
instance. Missing software cannot load a system ICD. Restrict library lookup
to the caller's module directory; use existing pinned packaging provenance.

Ignore only the CPUAdapter deployment reason for this verified factory and
CPU identity. Keep other block reasons, actual identity, native limits,
validation, robustness, cache isolation and shared-image error paths intact.
No unsafe-WebGPU switch, new profile field, Mojo interface or accessor rewrite.
Unprofiled and other runtime contexts retain their existing behavior.

"""
    patch = [header]
    for path in paths:
        if before[path] == after[path]: continue
        patch.append(f"diff --git a/{path} b/{path}\n")
        if not before[path]: patch.append("new file mode 100644\n")
        patch.extend(difflib.unified_diff(before[path].splitlines(True), after[path].splitlines(True),
            fromfile="a/" + path if before[path] else "/dev/null", tofile="b/" + path))
    candidate = scratch / "0056.patch"
    candidate.write_text("".join(patch))
    for flags in [["--check"], [], ["--reverse", "--check"]]:
        subprocess.run(["git", "apply", *flags, str(candidate)], cwd=scratch, check=True)
    output.write_text(candidate.read_text())
    (scratch / "v0.json").write_text(json.dumps({"patch_applies_reverses": True,
        "prefix": prefix, "compiled": False, "executed": False}, indent=2) + "\n")
    print(f"generated and checked {output}")

def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scratch", type=Path)
    parser.add_argument("--out", type=Path, default=ROOT / "patches/0056-profile-software-webgpu-admission.patch")
    args = parser.parse_args()
    if args.scratch:
        scratch = args.scratch.resolve(); scratch.mkdir(parents=True, exist_ok=False)
        generate(scratch, args.out)
    else:
        with tempfile.TemporaryDirectory(prefix="apostate-0056-") as work:
            generate(Path(work), args.out)
if __name__ == "__main__": main()
