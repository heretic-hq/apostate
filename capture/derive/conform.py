#!/usr/bin/env python3
"""V3 gate: diff a capture against the reference device it claims to be.

This is the scoreboard. A patch is not correct because it compiles or because a
value looks plausible — it is correct when the browser emits what the reference
device emitted (docs/METHODOLOGY.md §1).

    python3 capture/derive/conform.py REFERENCE.json SUBJECT.json
    python3 capture/derive/conform.py REFERENCE.json SUBJECT.json --show-volatile

Exit status is 0 only when every non-volatile field matches.

On volatility: some fields legitimately move between reads on one machine, and
calling those failures would bury the real ones. Rather than maintaining a
hand-written ignore list, volatility is *measured* — the reference capture reads
every deterministic probe twice, and anything that disagreed with itself on real
hardware cannot be required to agree across browsers. Fields listed in
ALWAYS_VOLATILE are the ones whose variance is definitional rather than
observed.
"""

import argparse
import json
import pathlib
import sys

# Values that describe the moment of measurement rather than the device.
# Everything else earns its exemption by being measured as unstable.
ALWAYS_VOLATILE = {
    "battery",                      # charge level moves continuously
    "memory.heap",                  # heap occupancy depends on what has run
    "network.connection",           # link estimate, re-sampled constantly
    "storage.estimate",             # usage grows as the page runs
    "timing.resolution",            # a timing measurement of a timing facility
}

# Paths within an otherwise-stable probe that track device state, not identity.
VOLATILE_PATHS = {
    ("audio.properties", "baseLatency"),
    ("audio.properties", "sampleRate"),
    ("audio.properties", "outputLatency"),
    ("audio.properties", "state"),
    ("screen.geometry", "outerWidth"),   # window size is the user's choice,
    ("screen.geometry", "outerHeight"),  # not a property of the device
    ("screen.geometry", "innerWidth"),
    ("screen.geometry", "innerHeight"),
    ("screen.geometry", "screenX"),
    ("screen.geometry", "screenY"),
}


def measured_volatility(capture):
    """Probes that disagreed with themselves within one session.

    Measured rather than assumed: if a field moved on real hardware between two
    reads, requiring it to match across browsers would be requiring something
    the hardware itself does not do.
    """
    unstable = set()
    probes, repeat = capture.get("probes", {}), capture.get("repeat", {})
    for pid, second in repeat.items():
        first = probes.get(pid)
        if not (first and first.get("ok") and second.get("ok")):
            continue
        if json.dumps(first["value"], sort_keys=True) != json.dumps(second["value"], sort_keys=True):
            unstable.add(pid)
    return unstable


def diff(a, b, path=()):
    """Yield (path, reference, subject) for every leaf that differs."""
    if type(a) is not type(b) and not (isinstance(a, (int, float)) and isinstance(b, (int, float))):
        yield path, a, b
        return
    if isinstance(a, dict):
        for key in sorted(set(a) | set(b)):
            if key not in a:
                yield path + (key,), "<absent>", b[key]
            elif key not in b:
                yield path + (key,), a[key], "<absent>"
            else:
                yield from diff(a[key], b[key], path + (key,))
    elif isinstance(a, list):
        if len(a) != len(b):
            yield path + ("<length>",), len(a), len(b)
            return
        for i, (x, y) in enumerate(zip(a, b)):
            yield from diff(x, y, path + (str(i),))
    elif a != b:
        yield path, a, b


def truncate(v, n=68):
    s = json.dumps(v) if not isinstance(v, str) else v
    return s if len(s) <= n else s[:n - 1] + "…"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference", type=pathlib.Path, help="T0 capture of the device being claimed")
    ap.add_argument("subject", type=pathlib.Path, help="capture taken from Apostate")
    ap.add_argument("--show-volatile", action="store_true", help="also list fields excluded as volatile")
    ap.add_argument("--max-per-probe", type=int, default=6, help="mismatch lines shown per probe")
    args = ap.parse_args()

    ref = json.loads(args.reference.read_text())
    sub = json.loads(args.subject.read_text())

    if ref["context"]["collector_sha256"] != sub["context"]["collector_sha256"]:
        print("REFUSED: captures were taken with different collector versions.")
        print(f"  reference {ref['context']['collector_sha256'][:16]}")
        print(f"  subject   {sub['context']['collector_sha256'][:16]}")
        print("They measured different things and are not comparable.")
        return 2

    volatile = ALWAYS_VOLATILE | measured_volatility(ref) | measured_volatility(sub)
    volatile_paths = {(pid, ".".join(rest) if isinstance(rest, tuple) else rest)
                      for pid, rest in VOLATILE_PATHS}

    print(f"reference : {ref['context'].get('label')}  {ref['context']['ua'][:64]}")
    print(f"subject   : {sub['context'].get('label')}  {sub['context']['ua'][:64]}")
    if sub["context"].get("automation_suspected"):
        print(f"  note: subject reports automation signals: {sub['context'].get('automation_signals')}")
    print()

    rp, sp = ref["probes"], sub["probes"]
    passed, failed, skipped, errored = [], [], [], []

    for pid in sorted(rp):
        if pid in volatile:
            skipped.append(pid)
            continue
        r, s = rp[pid], sp.get(pid)
        if s is None:
            errored.append((pid, "absent from subject capture"))
            continue
        if not r.get("ok"):
            skipped.append(pid)   # nothing to hold the subject to
            continue
        if not s.get("ok"):
            errored.append((pid, f"subject probe failed: {s.get('error')}"))
            continue

        mismatches = [
            m for m in diff(r["value"], s["value"])
            if (pid, ".".join(m[0])) not in volatile_paths
        ]
        if mismatches:
            failed.append((pid, mismatches))
        else:
            passed.append(pid)

    for pid, mismatches in failed:
        print(f"FAIL  {pid}   ({len(mismatches)} field(s) differ)")
        for p, a, b in mismatches[:args.max_per_probe]:
            loc = ".".join(p) if p else "<value>"
            print(f"        {loc}")
            print(f"          reference: {truncate(a)}")
            print(f"          subject  : {truncate(b)}")
        if len(mismatches) > args.max_per_probe:
            print(f"        … {len(mismatches) - args.max_per_probe} more")
        print()

    for pid, why in errored:
        print(f"ERROR {pid}: {why}")
    if errored:
        print()

    if args.show_volatile and skipped:
        print("excluded as volatile (measured unstable, or state rather than identity):")
        for pid in sorted(skipped):
            print(f"  {pid}")
        print()

    total = len(passed) + len(failed) + len(errored)
    print(f"{len(passed)}/{total} probes conform    {len(failed)} failed    "
          f"{len(errored)} errored    {len(skipped)} volatile")

    return 0 if not failed and not errored else 1


if __name__ == "__main__":
    sys.exit(main())
