#!/usr/bin/env python3
"""Compare ordered primitive controls and test an observed ARM NaN hypothesis.

The FMA order here is the order emitted in this compiled control, not a claim
that every compiler invocation preserves source-intrinsic operand ordering.
This analyzer does not alter DSP output or constitute a production NaN fix.
"""
import argparse
from collections import Counter
import csv
import hashlib
import json
from pathlib import Path


def nan(bits): return (bits & 0x7fffffff) > 0x7f800000
def signaling(bits): return nan(bits) and not (bits & 0x00400000)
def flushed(bits, mode):
    return bits & 0x80000000 if mode == "ftz" and (bits & 0x7fffffff) < 0x00800000 else bits


def predicted_nan(row):
    order = "cba" if row["operation"] == "fma" else "ab"
    for name in order:
        if signaling(row[name]): return row[name] | 0x00400000
    if row["operation"] == "fma":
        a = flushed(row["a"], row["mode"]) & 0x7fffffff
        b = flushed(row["b"], row["mode"]) & 0x7fffffff
        if (a == 0 and b == 0x7f800000) or (b == 0 and a == 0x7f800000):
            return 0x7fc00000
    for name in order:
        if nan(row[name]): return row[name] | 0x00400000
    return 0x7fc00000


def load(root):
    receipt = json.loads((root / "receipt.json").read_text())
    raw = (root / "results.csv").read_bytes()
    if receipt.get("status") != "completed" or receipt.get("diagnostic_only") is not True:
        raise ValueError("control is not a completed diagnostic")
    if hashlib.sha256(raw).hexdigest() != receipt["result_sha256"]:
        raise ValueError("CSV checksum mismatch")
    rows = []
    for row in csv.DictReader(line for line in raw.decode().splitlines() if not line.startswith("#")):
        for name in ["a", "b", "c", "result"]: row[name] = int(row[name], 16)
        rows.append(row)
    return receipt, rows


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("arm", type=Path)
    ap.add_argument("x86", type=Path)
    ap.add_argument("--out", type=Path, required=True)
    args = ap.parse_args()
    a_receipt, a = load(args.arm)
    b_receipt, b = load(args.x86)
    if a_receipt["source_sha256"] != b_receipt["source_sha256"] or len(a) != len(b):
        raise ValueError("different sources or case counts")
    differences = []
    for left, right in zip(a, b):
        if {k:v for k,v in left.items() if k != "result"} != {k:v for k,v in right.items() if k != "result"}:
            raise ValueError("different input case ordering")
        if left["result"] != right["result"]:
            differences.append({"mode": left["mode"], "operation": left["operation"],
                                "lane": left.get("lane"),
                                **{k:f"0x{left[k]:08x}" for k in ["a", "b", "c"]},
                                "arm_result": f"0x{left['result']:08x}",
                                "x86_result": f"0x{right['result']:08x}"})
    nan_rows = [row for row in a if row["operation"] != "neg" and nan(row["result"])]
    errors = [row for row in nan_rows if predicted_nan(row) != row["result"]]
    report = {"diagnostic_only": True, "production_nan_fix": False,
              "source_sha256": a_receipt["source_sha256"], "cases_per_host": len(a),
              "different_results": len(differences),
              "differences_by_operation": dict(Counter(row["operation"] for row in differences)),
              "non_nan_result_differences": sum(x["result"] != y["result"] and not nan(x["result"]) and not nan(y["result"])
                                                   for x,y in zip(a,b)),
              "arm_nan_hypothesis_cases": len(nan_rows), "arm_nan_hypothesis_errors": len(errors),
              "negation_sign_flip_errors": sum(row["result"] != row["a"] ^ 0x80000000 for row in a if row["operation"] == "neg"),
              "hypothesis": "Signaling NaNs first; binary a then b. Observed FMA c then b then a; invalid zero-times-infinity precedes a quiet addend. Generated invalid NaN is positive canonical. Negation flips only the sign bit.",
              "caveat": "ARM control disassembly emits fmla(c,b,a). Compiler multiplication-operand commutation must be checked before treating this as a source-intrinsic ordering rule.",
              "differences": differences}
    args.out.write_text(json.dumps(report, indent=2) + "\n")
    print(args.out)


if __name__ == "__main__": main()
