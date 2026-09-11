#!/usr/bin/env python3
"""Prepare isolated pinned FFmpeg trees for a genuine baseline/HEVC config comparison.

Only --configure or --build invokes the copied upstream builder. All writes are
inside --work. Compiler/NASM binaries are read-only inputs from a supplied pinned
checkout; no source/output/build configuration there is modified.
"""
import argparse
import base64
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import urllib.request

CHROMIUM = "79460ebecaa5625e57a5fb679a735659e73dc687"
FFMPEG = "2b68d2babae73714846961fb0ee47e3b3d2e39a9"
ROOT_URL = "https://chromium.googlesource.com/chromium/src/+/" + CHROMIUM + "/"


def fetch(path):
    return base64.b64decode(urllib.request.urlopen(ROOT_URL + path + "?format=TEXT", timeout=30).read())


def fetch_tree(path, destination):
    data = json.loads(urllib.request.urlopen(ROOT_URL + path + "?format=JSON", timeout=30).read().decode()[4:])
    destination.mkdir(parents=True, exist_ok=True)
    for entry in data["entries"]:
        if entry["type"] == "tree":
            fetch_tree(path + entry["name"] + "/", destination / entry["name"])
        elif entry["name"].endswith((".py", ".h", ".txt", ".md", ".pl")) or path.endswith("license_texts/"):
            (destination / entry["name"]).write_bytes(fetch(path + entry["name"]))


def sha(path):
    h = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            h.update(data)
    return h.hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--work", type=Path, required=True)
    parser.add_argument("--reference-source", type=Path, required=True)
    parser.add_argument("--configure", action="store_true")
    parser.add_argument("--build", action="store_true")
    parser.add_argument("--sysroot", action="store_true", help="use the pinned Chromium sysroot for isolated controls")
    args = parser.parse_args()
    work = args.work.resolve()
    reference = args.reference_source.resolve()
    if work.is_relative_to(reference):
        raise ValueError("isolated work must not be inside the generated source checkout")
    if subprocess.check_output(["git", "-C", str(reference), "rev-parse", "HEAD"], text=True).strip() != CHROMIUM:
        raise ValueError("reference source pin mismatch")
    work.mkdir(parents=True, exist_ok=False)
    inputs = work / "inputs"
    fetch_tree("media/ffmpeg/scripts/", inputs / "scripts")
    fetch_tree("third_party/opus/src/include/", inputs / "opus_include")
    (inputs / "gn_helpers.py").write_bytes(fetch("build/gn_helpers.py"))
    (inputs / "AUTHORS").write_bytes(fetch("AUTHORS"))
    (inputs / "licensecheck.pl").write_bytes(fetch("third_party/devscripts/licensecheck.pl"))
    (inputs / "licensecheck.pl").chmod(0o755)
    mirror = work / "ffmpeg-input"
    subprocess.run(["git", "init", "--quiet", str(mirror)], check=True)
    subprocess.run(["git", "-C", str(mirror), "fetch", "--quiet", "--depth=1", "--filter=blob:none",
                    "https://chromium.googlesource.com/chromium/third_party/ffmpeg.git", FFMPEG], check=True)
    subprocess.run(["git", "-C", str(mirror), "checkout", "--quiet", "--detach", "FETCH_HEAD"], check=True)
    compiler = reference / "third_party/llvm-build/Release+Asserts/bin/clang"
    nasm = reference / "out/linux-x64/nasm"
    tool_hashes = {str(p): sha(p) for p in [compiler, nasm]}
    receipt = {"chromium_revision": CHROMIUM, "ffmpeg_revision": FFMPEG,
        "reference_tool_hashes": tool_hashes, "input_script_hashes": {str(p.relative_to(inputs)): sha(p)
        for p in inputs.rglob("*") if p.is_file()}, "variants": {}, "shared_source_mutated": False}
    for variant in ["baseline", "hevc"]:
        source = work / variant / "src"
        source.mkdir(parents=True)
        (source / "AUTHORS").write_bytes((inputs / "AUTHORS").read_bytes())
        scripts = source / "media/ffmpeg/scripts"
        shutil.copytree(inputs / "scripts", scripts)
        (source / "build").mkdir()
        shutil.copyfile(inputs / "gn_helpers.py", source / "build/gn_helpers.py")
        third_party = source / "third_party"
        third_party.mkdir()
        (third_party / "llvm-build").symlink_to(reference / "third_party/llvm-build", target_is_directory=True)
        shutil.copytree(inputs / "opus_include", third_party / "opus/src/include")
        (third_party / "devscripts").mkdir()
        shutil.copyfile(inputs / "licensecheck.pl", third_party / "devscripts/licensecheck.pl")
        (third_party / "devscripts/licensecheck.pl").chmod(0o755)
        ffmpeg = third_party / "ffmpeg"
        subprocess.run(["git", "clone", "--quiet", "--shared", str(mirror), str(ffmpeg)], check=True)
        subprocess.run(["git", "-C", str(ffmpeg), "checkout", "--quiet", "--detach", FFMPEG], check=True)
        tools = work / variant / "tools"
        tools.mkdir()
        (tools / "nasm").symlink_to(nasm)
        builder = scripts / "build_ffmpeg.py"
        if variant == "hevc":
            text = builder.read_text()
            anchor = "    configure_flags['ChromeAndroid'].extend(["
            if text.count(anchor) != 1: raise ValueError("builder anchor changed")
            text = text.replace(anchor, "    if target_os == 'linux' and target_arch == 'x64':\n"
                "        configure_flags['Chrome'].extend([\n"
                "            '--enable-decoder=hevc', '--enable-parser=hevc',\n"
                "        ])\n\n" + anchor)
            builder.write_text(text)
        environment = dict(os.environ)
        environment["PATH"] = str(source / "third_party/llvm-build/Release+Asserts/bin") + ":" + str(tools) + ":" + environment["PATH"]
        command = ["python3", str(builder), "linux", "x64", "--branding", "Chrome"]
        if not args.build: command.append("--config-only")
        if args.sysroot:
            command.extend(["--", "--sysroot=" + str(reference / "build/linux/debian_bullseye_amd64-sysroot")])
        info = {"source": str(source), "command": command, "configured": False, "built": False}
        receipt["variants"][variant] = info
        if args.configure or args.build:
            if os.uname().sysname != "Linux" or not os.environ.get("APOSTATE_BUILD_IMAGE_ID"):
                raise ValueError("configure/build must run inside the pinned Linux build container")
            with (work / (variant + ".log")).open("w") as log:
                result = subprocess.run(command, cwd=ffmpeg, env=environment,
                    stdout=log, stderr=subprocess.STDOUT, timeout=600)
            info.update(returncode=result.returncode, configured=result.returncode == 0,
                        built=args.build and result.returncode == 0)
            (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
            if result.returncode: raise RuntimeError("isolated FFmpeg variant failed: " + variant)
    if any(sha(Path(path)) != expected for path, expected in tool_hashes.items()):
        raise ValueError("reference tool input changed during preparation")
    (work / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"work": str(work), "prepared": True}))
if __name__ == "__main__": main()
