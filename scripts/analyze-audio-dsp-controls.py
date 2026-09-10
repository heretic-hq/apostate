#!/usr/bin/env python3
"""Validate and compare native audio diagnostic PCM, without tolerances.

Pass one helper result directory to inspect it, or two to compare matched
fixtures. --reference checks the original graph against an existing capture.
These checks do not admit diagnostic receipts as T0 or certify arbitrary DSP.
"""

import argparse
import base64
from collections import Counter
import hashlib
import json
import math
from pathlib import Path
import struct


def load_run(path):
    result_path = path / "result.json" if path.is_dir() else path
    result = json.loads(result_path.read_text())
    if result.get("diagnosticOnly") is not True or result.get("fixtureVersion") != 1:
        raise ValueError(f"unsupported diagnostic format: {result_path}")
    if not result.get("passed"):
        raise ValueError(f"fixture failed: {result_path}")
    cases = {}
    for item in result["cases"]:
        if item["id"] in cases:
            raise ValueError(f"duplicate case: {item['id']}")
        for channel in item["inputs"] + item["output"]:
            decode(channel)
        cases[item["id"]] = item
    fixture_hash = None
    configuration = result_path.parent / "configuration.json"
    if configuration.exists():
        fixture_hash = json.loads(configuration.read_text())["inputs"]["fixture"]["sha256"]
    return result, cases, fixture_hash


def decode(channel):
    if channel.get("encoding") != "float32-le":
        raise ValueError("PCM is not explicitly little-endian float32")
    raw = base64.b64decode(channel["base64"], validate=True)
    if len(raw) != channel["frames"] * 4:
        raise ValueError("PCM frame count does not match bytes")
    if hashlib.sha256(raw).hexdigest() != channel["sha256"]:
        raise ValueError("PCM checksum does not match bytes")
    samples = struct.unpack(f"<{channel['frames']}f", raw)
    if not all(math.isfinite(value) for value in samples):
        raise ValueError("nonfinite PCM samples")
    # Match the fixture's left-to-right binary64 accumulation. Python's sum()
    # can use compensated summation, which is a different numerical contract.
    absolute_sum = 0.0
    for value in samples:
        absolute_sum += abs(value)
    if absolute_sum != channel["sumAbs"]:
        raise ValueError("PCM absolute sum does not match samples")
    return raw, samples


def float_order(bits):
    return (~bits & 0xffffffff) if bits & 0x80000000 else bits | 0x80000000


def compare_channel(left, right):
    a_raw, a = decode(left)
    b_raw, b = decode(right)
    if len(a) != len(b):
        raise ValueError("different output lengths")
    a_bits = struct.unpack(f"<{len(a)}I", a_raw)
    b_bits = struct.unpack(f"<{len(b)}I", b_raw)
    indices = [i for i, (x, y) in enumerate(zip(a_bits, b_bits)) if x != y]
    ulps = [float_order(x) - float_order(y) for x, y in zip(a_bits, b_bits)]
    differences = [x - y for x, y in zip(a, b)]
    histogram = Counter(ulps)
    return {
        "exact": a_raw == b_raw, "frames": len(a), "different_samples": len(indices),
        "first_difference": indices[0] if indices else None,
        "max_absolute_error": max(map(abs, differences), default=0),
        "rms_error": math.sqrt(sum(value * value for value in differences) / len(a)) if a else 0,
        "max_ulp": max(map(abs, ulps), default=0),
        "ulp_counts": {str(limit): sum(abs(value) <= limit for value in ulps)
                       for limit in [0, 1, 2, 4, 8, 16, 64, 256]},
        "common_signed_ulp": histogram.most_common(12),
        "first_samples": [{"index": i, "left": a[i], "right": b[i], "ulp": ulps[i]}
                          for i in indices[:5]],
        "left_sha256": left["sha256"], "right_sha256": right["sha256"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("left", type=Path)
    parser.add_argument("right", type=Path, nargs="?")
    parser.add_argument("--reference", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    left, left_cases, left_fixture = load_run(args.left)
    report = {"diagnostic_only": True, "corpus_admission": False,
              "left_environment": left["environment"], "left_fixture_sha256": left_fixture,
              "left_checks": left["checks"], "left_cases": [
                  {"id": item["id"], "role": item["config"]["role"],
                   "input_sha256": [c["sha256"] for c in item["inputs"]],
                   "output_sha256": [c["sha256"] for c in item["output"]]}
                  for item in left_cases.values()]}
    if args.reference:
        reference = json.loads(args.reference.read_text())
        original = left_cases["original_chain"]["collectorComparison"]
        expected = reference["probes"]["audio.offline_render"]["value"]
        report["original_reference_comparison"] = {
            "capture": str(args.reference),
            "full_hash_equal": original["full_sha256"] == expected["full_sha256"],
            "slice_bytes_equal": original["slice_base64"] == expected["slice_base64"],
            "slice_sum_equal": original["slice_sum"] == expected["slice_sum"],
            "observed_full_sha256": original["full_sha256"],
            "expected_full_sha256": expected["full_sha256"],
        }
    if args.right:
        right, right_cases, right_fixture = load_run(args.right)
        if left_fixture != right_fixture or left_fixture is None:
            raise ValueError("comparison requires identical fixture checksums in helper receipts")
        left_version = left["environment"].get("userAgentData", {}).get("uaFullVersion")
        right_version = right["environment"].get("userAgentData", {}).get("uaFullVersion")
        if not left_version or left_version != right_version:
            raise ValueError("comparison requires matching full browser versions")
        if left_cases.keys() != right_cases.keys():
            raise ValueError("different diagnostic case sets")
        report["right_environment"] = right["environment"]
        report["comparisons"] = []
        for case_id, a in left_cases.items():
            b = right_cases[case_id]
            if a["config"] != b["config"]:
                raise ValueError(f"different graph configuration: {case_id}")
            if len(a["inputs"]) != len(b["inputs"]) or len(a["output"]) != len(b["output"]):
                raise ValueError(f"different channel counts: {case_id}")
            inputs_equal = all(decode(x)[0] == decode(y)[0] for x, y in zip(a["inputs"], b["inputs"]))
            if not inputs_equal:
                raise ValueError(f"different compressor input bytes: {case_id}")
            report["comparisons"].append({"id": case_id, "role": a["config"]["role"],
                "identical_pcm_inputs": inputs_equal if a["inputs"] else None,
                "output": [compare_channel(x, y) for x, y in zip(a["output"], b["output"])]})
    text = json.dumps(report, indent=2, allow_nan=False) + "\n"
    if args.out:
        args.out.write_text(text)
        print(args.out)
    else:
        print(text, end="")


if __name__ == "__main__":
    main()
