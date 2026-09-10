#!/usr/bin/env python3
"""Merge shard proposals from ledger/inbox/ into the ledger.

Workers never write the ledger directly (docs/METHODOLOGY.md §7). They emit
proposals here and this merges them, so one process is responsible for
consistency between rows that were produced independently.

Dry run by default — it reports what would change and why. Nothing is written
without --apply, and nothing is ever silently overwritten.

    python3 scripts/merge-inbox.py            # report
    python3 scripts/merge-inbox.py --apply    # merge
"""

import argparse
import collections
import json
import pathlib
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
INBOX = ROOT / "ledger" / "inbox"

TARGETS = {
    "surfaces": ROOT / "ledger" / "surfaces.jsonl",
    "emitters": ROOT / "ledger" / "emitters.jsonl",
    "coherence_proposals": ROOT / "ledger" / "coherence.jsonl",
}


def load_jsonl(path):
    if not path.exists():
        return []
    return [json.loads(l) for l in path.read_text().splitlines() if l.strip()]


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(r) + "\n" for r in rows))


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--apply", action="store_true", help="write the merge instead of reporting it")
    ap.add_argument("--proposal", action="append", type=pathlib.Path,
                    help="read only this reviewed proposal envelope; repeatable")
    args = ap.parse_args()

    proposals = args.proposal or sorted(p for p in INBOX.glob("*.json"))
    if not proposals:
        print("no proposals in ledger/inbox/")
        return 0

    existing = {k: load_jsonl(v) for k, v in TARGETS.items()}
    existing_ids = {k: {r["id"] for r in v} for k, v in existing.items()}

    incoming = collections.defaultdict(list)   # kind -> [(shard, row)]
    problems = []

    for path in proposals:
        try:
            doc = json.loads(path.read_text())
        except json.JSONDecodeError as e:
            problems.append(f"{path.name}: invalid JSON: {e}")
            continue
        if not isinstance(doc, dict):
            problems.append(f"{path.name}: expected an object with surfaces/emitters keys")
            continue
        unknown = set(doc) - set(TARGETS)
        if unknown:
            problems.append(f"{path.name}: unknown key(s) {sorted(unknown)}")
        for kind in TARGETS:
            for row in doc.get(kind, []):
                if not isinstance(row, dict) or "id" not in row:
                    problems.append(f"{path.name}: a {kind} row has no id")
                    continue
                incoming[kind].append((path.stem, row))

    # Two shards proposing the same id is the failure mode this tool exists to
    # catch: it means either duplicated work or a genuine disagreement, and both
    # need a person rather than a last-writer-wins merge.
    for kind, entries in incoming.items():
        seen = collections.defaultdict(list)
        for shard, row in entries:
            seen[row["id"]].append(shard)
        for rid, shards in seen.items():
            if len(shards) > 1:
                problems.append(f"{kind}: '{rid}' proposed by multiple shards: {', '.join(shards)}")

    # Collisions with rows already in the ledger.
    collisions = []
    for kind, entries in incoming.items():
        for shard, row in entries:
            if row["id"] in existing_ids[kind]:
                collisions.append((kind, row["id"], shard))

    print(f"proposals: {', '.join(p.name for p in proposals)}\n")
    for kind, entries in sorted(incoming.items()):
        new = [e for e in entries if e[1]["id"] not in existing_ids[kind]]
        print(f"  {kind:20s} {len(entries):3d} proposed, {len(new):3d} new")

    if collisions:
        print(f"\n  {len(collisions)} row(s) already exist in the ledger and will NOT be overwritten:")
        for kind, rid, shard in collisions:
            print(f"    {kind}: {rid}  (from {shard})")

    if problems:
        print(f"\n{len(problems)} problem(s) — nothing merged:")
        for p in problems:
            print(f"  {p}")
        return 1

    if not args.apply:
        print("\ndry run. re-run with --apply to merge.")
        return 0

    merged_counts = {}
    for kind, target in TARGETS.items():
        rows = existing[kind]
        added = [row for shard, row in incoming.get(kind, []) if row["id"] not in existing_ids[kind]]
        if added:
            write_jsonl(target, rows + added)
        merged_counts[kind] = len(added)

    print("\nmerged: " + ", ".join(f"{v} {k}" for k, v in merged_counts.items() if v))

    # The merge is only real if the ledger still validates afterwards.
    print("\nrevalidating:")
    result = subprocess.run([sys.executable, str(ROOT / "scripts" / "validate-ledger.py")])
    if result.returncode != 0:
        print("\nVALIDATION FAILED after merge — the ledger is now inconsistent. Fix before committing.")
        return 1

    for path in proposals:
        path.unlink()
    print(f"\nconsumed {len(proposals)} proposal file(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
