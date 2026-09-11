#!/usr/bin/env python3
"""Compare fresh browser contexts with and without a provisioned extension.

Each run has no external network or GPU devices. Only authored localhost
diagnostics execute; no wallet methods, accounts, or user browser profiles.
"""
import argparse
import json
from pathlib import Path
import re
import subprocess
import uuid

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--image", required=True)
    p.add_argument("--extension", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    if not re.fullmatch(r"sha256:[0-9a-f]{64}", a.image): p.error("pinned image required")
    extension = a.extension.resolve(strict=True)
    if not extension.is_relative_to(ROOT): p.error("extension must be provisioned inside the private project directory")
    out = a.out.resolve(); out.mkdir(parents=True, exist_ok=False)
    receipt = {"diagnostic_only": True, "corpus_admission": False, "runs": []}
    for name in ["baseline", "extension"]:
        container = "apostate-extension-control-" + uuid.uuid4().hex
        command = ["docker", "run", "--rm", "--init", "--name", container,
            "--user=0:0", "--network=none", "--ipc=private", "--cap-drop=ALL",
            "--security-opt=no-new-privileges", "--pids-limit=512", "--memory=4g", "--cpus=2",
            "--shm-size=256m", "--read-only", "--tmpfs=/tmp:rw,nosuid,size=512m",
            "--mount", f"type=bind,src={ROOT},dst={ROOT},readonly",
            "--mount", f"type=bind,src={out},dst=/results",
            "--entrypoint=/usr/bin/env", a.image, "-i", "PATH=/usr/bin:/bin", "XDG_CACHE_HOME=/tmp/cache",
            "python3", str(ROOT / "scripts/run-browser-check.py"),
            "--browser", str(ROOT / ".workspace/src/out/linux-x64/chrome"),
            "--fixture", str(ROOT / "scripts/fixtures/extension-context-control.html"),
            "--angle-backend", "default", "--out", "/results/" + name, "--timeout", "45"]
        if name == "extension": command += ["--extension-dir", str(extension)]
        result = {"name": name, "command": command}
        try:
            with (out / (name + ".log")).open("w") as log:
                result["returncode"] = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT,
                                                        timeout=75).returncode
        finally:
            cleanup = subprocess.run(["docker", "rm", "--force", container],
                                     capture_output=True, text=True, timeout=15)
            result["owned_container_cleanup_returncode"] = cleanup.returncode
            receipt["runs"].append(result)
            (out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        if result["returncode"]: return result["returncode"]
    return 0

if __name__ == "__main__":
    raise SystemExit(main())
