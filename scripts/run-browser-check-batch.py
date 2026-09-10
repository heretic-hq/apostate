#!/usr/bin/env python3
"""Run an explicit sequence of owned-browser controls and preserve every result."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]

def main():
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--browser", type=Path, required=True)
    p.add_argument("--plan", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    a = p.parse_args()
    cases = json.loads(a.plan.read_text())
    allowed = {"name", "fixture", "profile", "fontconfig", "parameters", "angle_backend",
               "grant_display_controls", "timeout"}
    assert isinstance(cases, list) and 0 < len(cases) <= 50
    assert len({case["name"] for case in cases}) == len(cases)
    for case in cases:
        assert set(case) <= allowed and {"name", "fixture"} <= set(case)
        assert re.fullmatch(r"[a-z0-9][a-z0-9_-]{0,79}", case["name"])
    a.out.mkdir(parents=True, exist_ok=False)
    (a.out / "plan.json").write_bytes(a.plan.read_bytes())
    receipt = {"diagnostic_only": True, "corpus_admission": False,
               "plan_sha256": hashlib.sha256(a.plan.read_bytes()).hexdigest(), "cases": []}
    for case in cases:
        command = [sys.executable, str(ROOT / "scripts/run-browser-check.py"),
                   "--browser", str(a.browser), "--fixture", case["fixture"],
                   "--out", str(a.out / case["name"])]
        for key in ("profile", "fontconfig", "parameters", "angle_backend", "timeout"):
            if key in case: command += ["--" + key.replace("_", "-"), str(case[key])]
        if case.get("grant_display_controls") is True: command += ["--grant-display-controls"]
        with (a.out / (case["name"] + ".log")).open("w") as log:
            result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
        row = {"name": case["name"], "returncode": result.returncode, "command": command}
        receipt["cases"].append(row)
        (a.out / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
        print(json.dumps({"name": case["name"], "returncode": result.returncode}), flush=True)
    return int(any(row["returncode"] for row in receipt["cases"]))

if __name__ == "__main__":
    raise SystemExit(main())
