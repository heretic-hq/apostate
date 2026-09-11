#!/usr/bin/env python3
"""Run candidate0051 only in an isolated, digest-pinned container.

The manifest is an operator/build receipt, not proof independently reconstructed
from the binary. Validate its source pins and every bundled file before launch.
This script never builds, launches a browser, downloads an image, or changes a GPU.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import shutil
import sys
import subprocess
import time
import uuid

PINS = {
    "chromium_revision": "79460ebecaa5625e57a5fb679a735659e73dc687",
    "dawn_revision": "ab8827bc57b176eeaa89f71324130c02d4d41145",
    "swiftshader_revision": "5b0479bd2d15058aaa9eb490e364f920ff824a8c",
}
FILES = {"apostate_dawn_software_check", "libvk_swiftshader.so", "vk_swiftshader_icd.json"}
HEX = re.compile(r"[0-9a-f]{64}\Z")
IMAGE = re.compile(r"(?:[a-zA-Z0-9][a-zA-Z0-9._:/-]*@)?sha256:[0-9a-f]{64}\Z")

def digest(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()

def verify_bundle(bundle, manifest_path):
    if bundle.is_symlink() or not bundle.is_dir():
        raise ValueError("bundle must be a real directory")
    bundle = bundle.resolve()
    manifest = json.loads(manifest_path.read_text())
    if set(manifest) != set(PINS) | {"files"}:
        raise ValueError("manifest fields do not match the pinned contract")
    for key, value in PINS.items():
        if manifest[key] != value:
            raise ValueError("source pin mismatch: " + key)
    if set(manifest["files"]) != FILES:
        raise ValueError("manifest must contain exactly the executable, software library and ICD")
    if {path.name for path in bundle.iterdir()} != FILES:
        raise ValueError("bundle contains missing or unpinned files")
    for name in FILES:
        expected = manifest["files"][name]
        path = bundle / name
        if not isinstance(expected, str) or not HEX.fullmatch(expected):
            raise ValueError("invalid SHA256: " + name)
        if path.is_symlink() or not path.is_file() or digest(path) != expected:
            raise ValueError("file type or hash mismatch: " + name)
    if not os.access(bundle / "apostate_dawn_software_check", os.X_OK):
        raise ValueError("test executable is not executable")
    icd = json.loads((bundle / "vk_swiftshader_icd.json").read_text())
    if icd.get("ICD", {}).get("library_path") not in {
        "libvk_swiftshader.so", "./libvk_swiftshader.so", "/provider/libvk_swiftshader.so"
    }:
        raise ValueError("ICD library_path escapes the verified provider")
    return bundle, manifest

def container_command(runtime, image, bundle, out, manifest, name):
    if runtime not in {"docker", "podman"}:
        raise ValueError("unsupported container runtime")
    if not IMAGE.fullmatch(image):
        raise ValueError("container image must be pinned by sha256 digest")
    if any(character in str(path) for path in [bundle, out] for character in [",", "\n", "\r"]):
        raise ValueError("container mount paths contain unsupported characters")
    # Do not inherit runtime flags, host environment, device mappings or image
    # entrypoints. The standalone binary independently checks no DRI exposure.
    return [runtime, "run", "--rm", "--pull=never", "--name", name,
        "--cidfile", str(out / "container.cid"), "--network=none", "--ipc=private",
        "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user=65534:65534", "--pids-limit=128", "--memory=1g", "--cpus=2",
        "--tmpfs=/tmp:rw,noexec,nosuid,size=64m",
        "--mount", f"type=bind,src={bundle},dst=/provider,readonly",
        "--mount", f"type=bind,src={out},dst=/results",
        "--entrypoint=/usr/bin/env", image, "-i", "PATH=/usr/bin:/bin",
        "VK_DRIVER_FILES=/provider/vk_swiftshader_icd.json",
        "VK_ICD_FILENAMES=/provider/vk_swiftshader_icd.json",
        "/provider/apostate_dawn_software_check", "--out=/results/result.json",
        "--provider-sha256=" + manifest["files"]["libvk_swiftshader.so"],
        "--icd-sha256=" + manifest["files"]["vk_swiftshader_icd.json"]]

def run(args):
    bundle, manifest = verify_bundle(args.bundle, args.manifest)
    if not IMAGE.fullmatch(args.image):
        raise ValueError("container image must be pinned by sha256 digest")
    if not 5 <= args.timeout <= 120:
        raise ValueError("timeout must be between5 and120 seconds")
    out = args.out.absolute()
    out.mkdir(parents=True, exist_ok=False)
    # Container nobody can write diagnostics; this directory is fresh and owns
    # only this test's receipts. No credentials are copied into it.
    out.chmod(0o777)
    name = "apostate-software-check-" + uuid.uuid4().hex
    command = container_command(args.runtime, args.image, bundle, out, manifest, name)
    (out / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    (out / "launch.json").write_text(json.dumps({"command": command, "diagnostic_only": True,
        "corpus_admission": False}, indent=2) + "\n")
    receipt = {"diagnostic_only": True, "corpus_admission": False, "started_unix": time.time(),
               "image": args.image, "container_name": name, "status": "starting"}
    process = None
    try:
        with (out / "process.log").open("w") as log:
            process = subprocess.Popen(command, stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=True)
            receipt["pid"] = process.pid
            try:
                receipt["exit_code"] = process.wait(timeout=args.timeout)
                receipt["status"] = "completed"
            except subprocess.TimeoutExpired:
                receipt["status"] = "timeout"
    finally:
        # Remove only the uniquely named container created for this invocation.
        # Do not trust a container-controlled cid file as an arbitrary cleanup ID.
        try:
            cleanup = subprocess.run([args.runtime, "rm", "--force", name],
                                     capture_output=True, text=True, timeout=15)
            receipt["owned_container_cleanup_exit_code"] = cleanup.returncode
        except (OSError, subprocess.TimeoutExpired) as error:
            receipt["cleanup_error"] = str(error)
        if process is not None and process.poll() is None:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                pass
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
                process.wait(timeout=3)
        receipt["finished_unix"] = time.time()
        result_path = out / "result.json"
        try:
            result = json.loads(result_path.read_text()) if result_path.is_file() else {}
            if not isinstance(result, dict):
                receipt["result_error"] = "result is not an object"
                result = {}
        except (OSError, ValueError) as error:
            receipt["result_error"] = str(error)
            result = {}
        required = ["provider_verified", "zero_initialization_passed", "compute_passed",
                    "texture_upload_readback_passed", "invalid_wgsl_rejected",
                    "above_adapter_limit_rejected", "passed"]
        receipt["passed"] = (receipt.get("status") == "completed" and
            receipt.get("exit_code") == 0 and not receipt.get("cleanup_error") and all(result.get(key) is True for key in required) and
            result.get("corpus_admission") is False and
            result.get("actual_vendor_id") == 0x1ae0 and result.get("actual_device_id") == 0xc0de)
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    print(json.dumps({"out": str(out), "passed": receipt["passed"]}))
    return 0 if receipt["passed"] else 1

def write_manifest(bundle, source, output, build_output=None):
    # Read-only pin verification against the explicitly supplied build checkout.
    # This is an operator build receipt, not a claim to reconstruct compilation.
    repos = {"chromium_revision": source, "dawn_revision": source / "third_party/dawn",
             "swiftshader_revision": source / "third_party/swiftshader"}
    for key, repo in repos.items():
        actual = subprocess.check_output(["git", "-C", str(repo), "rev-parse", "HEAD"], text=True).strip()
        if actual != PINS[key]:
            raise ValueError("build checkout pin mismatch: " + key)
    if output.exists():
        _, manifest = verify_bundle(bundle, output)
        if build_output and any(digest(build_output / name) != manifest["files"][name] for name in FILES):
            raise ValueError("existing bundle conflicts with build output")
        return 0
    if build_output and not bundle.exists():
        bundle.mkdir(parents=True, exist_ok=False)
        bundle.chmod(0o755)
        for name in FILES:
            original = build_output / name
            if original.is_symlink() or not original.is_file():
                raise ValueError("build artifact must be a regular file: " + name)
            shutil.copyfile(original, bundle / name)
            (bundle / name).chmod(0o444 if name.endswith(".json") else 0o555)
    manifest = dict(PINS, files={name: digest(bundle / name) for name in FILES})
    if build_output and any(digest(build_output / name) != manifest["files"][name] for name in FILES):
        raise ValueError("bundle conflicts with build output")
    # Keep the manifest outside the exact three-file executable bundle.
    if output.parent.resolve() == bundle.resolve():
        raise ValueError("manifest must be outside the executable bundle")
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        stream.write(json.dumps(manifest, indent=2) + "\n")
    try:
        verify_bundle(bundle, output)
    except Exception:
        output.unlink()
        raise
    return 0

def main():
    if len(sys.argv) > 1 and sys.argv[1] == "manifest":
        p = argparse.ArgumentParser(description="Verify source pins and record built software test hashes")
        p.add_argument("--bundle", type=Path, required=True)
        p.add_argument("--source-dir", type=Path, required=True)
        p.add_argument("--out", type=Path, required=True)
        p.add_argument("--from-output", type=Path, help="copy the exact three artifacts into a fresh bundle")
        args = p.parse_args(sys.argv[2:])
        return write_manifest(args.bundle, args.source_dir, args.out, args.from_output)
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--bundle", type=Path, required=True)
    p.add_argument("--manifest", type=Path, required=True)
    p.add_argument("--image", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--runtime", choices=["docker", "podman"], default="docker")
    p.add_argument("--timeout", type=float, default=45)
    return run(p.parse_args())
if __name__ == "__main__":
    raise SystemExit(main())
