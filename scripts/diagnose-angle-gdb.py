#!/usr/bin/env python3
"""Diagnose one software ANGLE startup crash with an existing host debugger.

This is a diagnostic runtime, not a conformance gate: host debugger libraries
are mounted read-only and their exact hashes are recorded. No GPU is mapped.
"""
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--loader-only", action="store_true")
    p.add_argument("--extension", type=Path,
                   help="diagnose fresh headless Chromium extension startup instead of ANGLE")
    p.add_argument("--emoji-font", type=Path,
                   help="diagnose the standalone Fontations emoji fixture")
    a = p.parse_args()
    assert sum(bool(x) for x in (a.loader_only, a.extension, a.emoji_font)) <= 1
    assert re.fullmatch(r"sha256:[0-9a-f]{64}", a.image)
    gdb = Path(shutil.which("gdb") or "").resolve()
    assert gdb.is_file()
    out = a.out.resolve()
    out.mkdir(parents=True, exist_ok=False)
    dependencies = subprocess.check_output(["ldd", str(gdb)], text=True)
    files = {gdb}
    for line in dependencies.splitlines():
        for word in line.split():
            if word.startswith("/") and Path(word).is_file(): files.add(Path(word))
    hashes = {str(f): hashlib.sha256(f.read_bytes()).hexdigest() for f in sorted(files)}
    build = ROOT / ".workspace/src/out/linux-x64"
    container_name = "apostate-angle-debug-" + uuid.uuid4().hex
    command = ["docker", "run", "--rm", "--init", "--name", container_name, "--user=0:0", "--network=none", "--cap-drop=ALL",
               "--security-opt=no-new-privileges", "--pids-limit=128", "--memory=2g",
               "--cpus=2", "--mount", f"type=bind,src={ROOT},dst={ROOT},readonly"]
    for f in sorted(files): command += ["--mount", f"type=bind,src={f},dst={f},readonly"]
    if a.emoji_font:
        font = a.emoji_font.resolve(strict=True)
        assert font.is_file()
        hashes[str(font)] = hashlib.sha256(font.read_bytes()).hexdigest()
        command += ["--mount", f"type=bind,src={font},dst=/input/font.ttc,readonly"]
    command += ["--entrypoint=/usr/bin/env", a.image, "-i", "PATH=/usr/bin:/bin",
                "DEBUGINFOD_URLS=", f"VK_DRIVER_FILES={build}/vk_swiftshader_icd.json",
                f"VK_ICD_FILENAMES={build}/vk_swiftshader_icd.json"]
    if not a.loader_only and a.extension is None and a.emoji_font is None:
        command += ["xvfb-run", "-a", "-e", "/dev/stderr", "-s", "-screen 0 1280x720x24 -nolisten tcp"]
    command += [str(gdb),
                "--batch", "-nx", "-ex", "set pagination off", "-ex", "set startup-with-shell off",
                "-ex", "run", "-ex", "thread apply all bt", "-ex", "x/8i $pc-16", "--args"]
    if a.emoji_font:
        command += [str(build / "fontations_emoji_control"), "--font-file=/input/font.ttc",
                    "--font-index=0", "--output-dir=/tmp/emoji-debug"]
    elif a.extension is not None:
        extension = a.extension.resolve(strict=True)
        assert extension.is_relative_to(ROOT)
        command += [str(build / "chrome"), "--headless=new", "--no-sandbox",
                    "--disable-gpu-sandbox", "--no-first-run", "--no-default-browser-check",
                    "--user-data-dir=/tmp/apostate-extension-gdb-profile",
                    "--load-extension=" + str(extension), "about:blank"]
    elif a.loader_only:
        code = ("import ctypes; lib=ctypes.CDLL(" + repr(str(build / "libEGL_test.so")) + "); "
                "lib.eglGetProcAddress.argtypes=[ctypes.c_char_p]; "
                "lib.eglGetProcAddress.restype=ctypes.c_void_p; print(lib.eglGetProcAddress(b'eglGetDisplay'))")
        loader = out / "load-egl.py"
        loader.write_text(code + "\n")
        command += ["/usr/bin/python3", str(loader)]
    else:
        command += [str(build / "angle_end2end_tests"), "--use-config=ES2_Vulkan_SwiftShader",
                    "--gtest_filter=ParallelShaderCompileTest.DefaultSupportOnLinuxSwiftShader/ES2_Vulkan_SwiftShader"]
    receipt = {"diagnostic_only": True, "corpus_admission": False,
               "host_debugger_files": hashes, "command": command}
    try:
        with (out / "gdb.log").open("w") as log:
            receipt["returncode"] = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                                     timeout=60).returncode
    finally:
        cleanup = subprocess.run(["docker", "rm", "--force", container_name],
                                 capture_output=True, text=True, timeout=15)
        receipt["owned_container_cleanup_returncode"] = cleanup.returncode
        (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")

if __name__ == "__main__":
    main()
