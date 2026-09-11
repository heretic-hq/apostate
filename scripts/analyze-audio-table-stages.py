#!/usr/bin/env python3
"""Locate the first differing stage in two frozen standalone audio runs."""
import argparse
from collections import defaultdict
import hashlib
import json
import math
from pathlib import Path
import struct


STAGES = ["input", "real", "imag", "twiddles", "prepared", "postfft", "ifft", "normalizer", "normalized"]


def load(root):
    receipt = json.loads((root / "run-receipt.json").read_text())
    if receipt.get("status") != "completed" or receipt.get("diagnostic_only") is not True:
        raise ValueError("standalone run is not a completed diagnostic")
    files = {}
    for name, record in receipt["stage_files"].items():
        data = (root / "stages" / name).read_bytes()
        if len(data) != record["bytes"] or hashlib.sha256(data).hexdigest() != record["sha256"]:
            raise ValueError(f"corrupt stage buffer: {root}/{name}")
        files[name] = data
    return receipt, files


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--portable-neon-experiment", action="store_true",
                        help="compare explicitly declared native and portable variants of one frozen baseline")
    args = parser.parse_args()
    left, a = load(args.left)
    right, b = load(args.right)
    if args.portable_neon_experiment:
        manifests = [json.loads((root / "source-manifest.json").read_text())
                     for root in [args.left, args.right]]
        for root, receipt in [(args.left, left), (args.right, right)]:
            actual = hashlib.sha256((root / "source-manifest.json").read_bytes()).hexdigest()
            if actual != receipt["source_manifest_sha256"]:
                raise ValueError("source manifest does not match run receipt")
        if (manifests[0].get("experiment") != "native-neon-complex-controls" or
                manifests[1].get("experiment") != "portable-neon-software-lanes" or
                not manifests[0].get("baseline_source_manifest_sha256") or
                manifests[0]["baseline_source_manifest_sha256"] != manifests[1].get("baseline_source_manifest_sha256") or
                manifests[0].get("experiment_parameters") != manifests[1].get("experiment_parameters")):
            raise ValueError("unmatched native/portable experiment declarations")
    elif left["source_manifest_sha256"] != right["source_manifest_sha256"]:
        raise ValueError("different prepared sources")
    if left["cargo_lock_sha256"] != right["cargo_lock_sha256"]:
        raise ValueError("different dependency locks")
    if a.keys() != b.keys():
        raise ValueError("different stage sets")
    counts = defaultdict(lambda: {"total_buffers": 0, "different_buffers": 0})
    mismatches = []
    for name, first in a.items():
        second = b[name]
        if len(first) != len(second):
            raise ValueError(f"different stage sizes: {name}")
        case, filename = name.split("/")
        stage = filename.split(".")[-2]
        counts[(case, stage)]["total_buffers"] += 1
        if first != second:
            counts[(case, stage)]["different_buffers"] += 1
            size = len(first) // 4
            aa, bb = struct.unpack(f"<{size}f", first), struct.unpack(f"<{size}f", second)
            bits_a, bits_b = struct.unpack(f"<{size}I", first), struct.unpack(f"<{size}I", second)
            indices = [i for i in range(size) if bits_a[i] != bits_b[i]]
            finite_errors = [abs(x-y) for x,y in zip(aa,bb) if math.isfinite(x) and math.isfinite(y)]
            def display(value, bits):
                return value if math.isfinite(value) else f"{value}:0x{bits:08x}"
            mismatches.append({"buffer": name, "different_samples": len(indices),
                               "first_index": indices[0], "frames": size,
                               "max_absolute_error": max(finite_errors, default=None),
                               "nonfinite_left": sum(not math.isfinite(x) for x in aa),
                               "nonfinite_right": sum(not math.isfinite(x) for x in bb),
                               "first_left": display(aa[indices[0]], bits_a[indices[0]]),
                               "first_right": display(bb[indices[0]], bits_b[indices[0]])})
    summary = [{"case": case, "stage": stage, **count}
               for (case, stage), count in sorted(counts.items(), key=lambda item:
                                                    (item[0][0], STAGES.index(item[0][1])))]
    report = {"diagnostic_only": True, "not_v1": True,
              "portable_neon_experiment": args.portable_neon_experiment,
              "source_manifest_sha256": left["source_manifest_sha256"],
              "right_source_manifest_sha256": right["source_manifest_sha256"],
              "cargo_lock_sha256": left["cargo_lock_sha256"],
              "left_toolchain": {k: left[k] for k in ["machine", "rustc", "cxx"]},
              "right_toolchain": {k: right[k] for k in ["machine", "rustc", "cxx"]},
              "summary": summary, "mismatches": mismatches}
    args.out.write_text(json.dumps(report, indent=2, allow_nan=False) + "\n")
    print(args.out)


if __name__ == "__main__":
    main()
