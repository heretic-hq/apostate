#!/usr/bin/env python3
"""V3 gate: diff a capture against the reference device it claims to be.

This is the scoreboard. A patch is not correct because it compiles or because a
value looks plausible — it is correct when the browser emits what the reference
device emitted (docs/METHODOLOGY.md §1).

    python3 capture/derive/conform.py REFERENCE.json SUBJECT.json
    python3 capture/derive/conform.py REFERENCE.json SUBJECT.json --show-volatile

Exit status is 0 only when every non-volatile field matches and both captures
report the same full browser build. Exit 2 means the comparison is incomplete.

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
    # Only context state moves. baseLatency and outputLatency were excluded here
    # on a misreading of which field differed between two captures; measured
    # across five captures they are constant, and baseLatency encodes buffer
    # size over sample rate, so excluding it discarded real signal.
    ("audio.properties", "state"),
    ("screen.geometry", "outerWidth"),   # window size is the user's choice,
    ("screen.geometry", "outerHeight"),  # not a property of the device
    ("screen.geometry", "innerWidth"),
    ("screen.geometry", "innerHeight"),
    ("screen.geometry", "screenX"),
    ("screen.geometry", "screenY"),
    # Measured, not assumed: two captures of the same MacBook five hours apart
    # reported availHeight 1001 and 1003. The macOS dock inset moves with dock
    # state, so an exact match cannot be required. The inset must still be
    # plausible for the claimed OS — see coh.screen-avail-inset.
    ("screen.geometry", "availHeight"),
    ("screen.geometry", "availWidth"),
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


# Headers whose value describes the capture server rather than the browser.
# Their presence and position in the order are device signal; their contents are
# not, and comparing them makes two captures of one machine differ because they
# were taken against different ports.
ENVIRONMENT_HEADERS = {"host", "referer", "origin", "connection", "content-length",
                       "cookie", "if-none-match", "if-modified-since"}

# Client hints that report the size of the window rather than the device. These
# are the same information as screen.geometry's outerWidth/innerWidth, which are
# already excluded above because window size is the user's choice — a capture
# taken in a 1728-wide window cannot hold a browser launched at 800x600 to that
# number without turning a launch flag into a fingerprint defect. dpr is
# deliberately NOT in this set: pixel ratio is device identity, not window state.
WINDOW_STATE_HEADERS = {"viewport-width", "sec-ch-viewport-width",
                        "viewport-height", "sec-ch-viewport-height",
                        "width", "sec-ch-width"}


def normalise_headers(value):
    """Blank environment-dependent header values, keeping names and order."""
    if not isinstance(value, dict) or "headers" not in value:
        return value
    out = dict(value)
    out["headers"] = [
        [k, "<environment>" if k.lower() in ENVIRONMENT_HEADERS
            else "<window>" if k.lower() in WINDOW_STATE_HEADERS else v]
        for k, v in value["headers"]
    ]
    return out


def browser_version(capture):
    """Full browser version from the capture, or None."""
    probe = capture.get("probes", {}).get("navigator.userAgentData")
    if not (probe and probe.get("ok")):
        return None
    return (probe["value"].get("high") or {}).get("uaFullVersion")


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

    # A profile is only replayable by a binary of the same browser build.
    # Apostate deliberately does not spoof its own version — the binary really
    # is the Chromium it reports, and claiming otherwise would require behaving
    # like that version. So a reference captured from a different point release
    # differs in ways no patch should fix, and reporting those as failures
    # buries the ones that matter.
    ref_ver = browser_version(ref)
    sub_ver = browser_version(sub)
    version_mismatch = ref_ver and sub_ver and ref_ver != sub_ver
    build_comparable = bool(ref_ver and sub_ver and ref_ver == sub_ver)
    volatile_paths = {(pid, ".".join(rest) if isinstance(rest, tuple) else rest)
                      for pid, rest in VOLATILE_PATHS}

    print(f"reference : {ref['context'].get('label') or args.reference.stem}  {ref['context']['ua'][:64]}")
    print(f"subject   : {sub['context'].get('label') or args.subject.stem}  {sub['context']['ua'][:64]}")
    if version_mismatch:
        print(f"\n  NOTE: browser builds differ — reference {ref_ver}, subject {sub_ver}.")
        print("  Version-bearing fields will differ and no patch should change that:")
        print("  the binary really is the version it reports. Match the reference build")
        print("  to the build under test for a clean comparison.")
    if not ref_ver or not sub_ver:
        print("  NOTE: full browser version is missing; this is a diagnostic comparison.")
    if sub["context"].get("automation_suspected"):
        print(f"  note: subject reports automation signals: {sub['context'].get('automation_signals')}")
    print()

    rp, sp = ref["probes"], sub["probes"]
    passed, failed, skipped, errored = [], [], [], []
    version_derived = []

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

        rv, sv = r["value"], s["value"]
        if pid.startswith("headers.echo"):
            # Both the main-thread and worker echoes carry the capture server's
            # Host and Referer; normalising only the first left two captures of
            # one machine differing because they used different ports.
            rv, sv = normalise_headers(rv), normalise_headers(sv)

        mismatches = [
            m for m in diff(rv, sv)
            if (pid, ".".join(m[0])) not in volatile_paths
        ]

        # Separate the differences that exist only because the two builds are
        # different point releases. The test is exact rather than a blanket
        # exemption: a field is excused only when substituting the subject's
        # version string for the reference's makes the two values identical.
        # A field that merely *contains* a version but differs in some other
        # way still fails, so this cannot mask a real defect.
        if version_mismatch and mismatches:
            excused, real = [], []
            for m in mismatches:
                path, a, b = m
                if (isinstance(a, str) and isinstance(b, str)
                        and sub_ver in b and a == b.replace(sub_ver, ref_ver)):
                    excused.append(m)
                else:
                    real.append(m)
            if excused:
                version_derived.append((pid, excused))
            mismatches = real

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

    if version_derived:
        n = sum(len(m) for _, m in version_derived)
        print(f"version-derived, not counted as failures "
              f"({n} field(s) across {len(version_derived)} probe(s)):")
        print(f"  reference build {ref_ver}, subject build {sub_ver}. Each field below")
        print("  is identical once the version string is substituted.")
        for pid, ms in version_derived:
            locs = ", ".join(".".join(p) if p else "<value>" for p, _, _ in ms)
            print(f"  {pid}: {locs}")
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
    result_label = "probes conform" if build_comparable else "probes match (diagnostic)"
    print(f"{len(passed)}/{total} {result_label}    {len(failed)} failed    "
          f"{len(errored)} errored    {len(skipped)} volatile")

    if not build_comparable:
        print("INCOMPLETE: matching full browser builds are required for V3.")
        return 2
    return 0 if not failed and not errored else 1


if __name__ == "__main__":
    sys.exit(main())
