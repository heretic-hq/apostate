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

On collector generations: this used to refuse outright when the two captures
carried different `context.collector_sha256`, which stranded 18 of 26 admitted
captures. The rule was far stronger than the evidence — a revision that
rewrites one probe and leaves thirty-five untouched has not changed what those
thirty-five measure — so comparability is now decided PER PROBE from
corpus/collector-probe-matrix.json, which digests each probe's implementation
plus the helpers it calls at every revision of the collector reachable from
git. Probes whose implementation is byte-identical are compared; the rest are
SKIPPED AND REPORTED, never silently compared. A collector with no entry in the
matrix still refuses the whole comparison: fail closed.

What that evidence does and does not cover: it proves the probe itself was not
rewritten. It does not prove that a probe ADDED later cannot perturb an older
one through timing, GPU memory or permission state, which only an A/B run of
both collectors on one host would settle. The report says so, and the one
double capture we hold of a single device across two generations (the Sri Lanka
Windows box on 6b9f3004 and a19ad58d) is the empirical check on it.

The reference side must also be a real device: a capture tiered
`not-a-reference` in corpus/capture-tiers.json is our own browser's output, and
holding ourselves to it proves only that we agree with ourselves.
"""

import argparse
import importlib.util
import json
import pathlib
import re
import sys

REPO = pathlib.Path(__file__).resolve().parent.parent.parent
PROVENANCE_MODULE = REPO / "corpus" / "provenance.py"

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

# Render surfaces that may never earn a volatility exemption by disagreeing
# with themselves. coh.render-determinism, severity fatal: real hardware draws
# the same scene the same way twice, so a surface whose second read differs has
# lost determinism, and per-call noise violates the axiom by construction.
#
# This set exists because the escape hatch below used to swallow exactly that.
# A probe that disagreed with its own repeat read was added to the volatile set
# and then skipped, so a fork that perturbed its canvas produced a CLEANER
# report the more it drifted: the noisy surfaces excused themselves and the
# remaining comparison passed. The divergence is now a reported failure on
# whichever side produced it, which is what makes the row's claim falsifiable.
DETERMINISM_REQUIRED = {
    "canvas.2d",
    "webgl1",
    "webgl2",
    "audio.offline_render",
    "clientrects",
}


def measured_volatility(capture):
    """Probes that disagreed with themselves within one session.

    Measured rather than assumed: if a field moved on real hardware between two
    reads, requiring it to match across browsers would be requiring something
    the hardware itself does not do. The render surfaces are the exception and
    are handled by determinism_failures() instead — for them, disagreeing with
    the second read is the defect rather than the excuse.
    """
    unstable = set()
    probes, repeat = capture.get("probes", {}), capture.get("repeat", {})
    for pid, second in repeat.items():
        first = probes.get(pid)
        if not (first and first.get("ok") and second.get("ok")):
            continue
        if json.dumps(first["value"], sort_keys=True) != json.dumps(second["value"], sort_keys=True):
            if pid in DETERMINISM_REQUIRED:
                continue
            unstable.add(pid)
    return unstable


def determinism_failures(capture):
    """-> [(probe id, [(path, first, repeat)])] for every render surface that
    did not reproduce its own first read.

    The second read is a genuine recomputation rather than a re-read of a
    retained result: the collector runs the whole probe again, which for these
    five means a new canvas element, a new WebGL context with recompiled
    shaders, a new OfflineAudioContext and a freshly built and detached DOM
    subtree. So a surface that perturbs per call has two independent chances to
    show it, and one that does not is measured as deterministic rather than
    assumed to be.
    """
    out = []
    probes, repeat = capture.get("probes", {}), capture.get("repeat", {})
    for pid in sorted(DETERMINISM_REQUIRED):
        first, second = probes.get(pid), repeat.get(pid)
        if not (first and second and first.get("ok") and second.get("ok")):
            continue
        mismatches = list(diff(first["value"], second["value"]))
        if mismatches:
            out.append((pid, mismatches))
    return out


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


def normalise_ice(value):
    """Preserve candidate groups while removing session-specific foundation IDs.

    WebRTC 6f37672d358475cd17544121a12494da454d85fb, api/candidate.cc:567-595,
    hashes the base IP and ICE tiebreaker into a decimal CRC32 foundation.
    RFC 8445 section 5.1.1.3 assigns meaning to equality between foundations,
    not to their numeric identities across sessions.
    """
    if not isinstance(value, dict) or not isinstance(value.get("candidates"), list):
        raise ValueError("missing ICE candidate list")
    groups, candidates = {}, []
    for index, candidate in enumerate(value["candidates"]):
        foundation = candidate.get("foundation") if isinstance(candidate, dict) else None
        if (not isinstance(foundation, str)
                or not re.fullmatch(r"0|[1-9][0-9]{0,9}", foundation)
                or int(foundation) > 0xffffffff):
            raise ValueError(f"candidate {index} has an invalid decimal uint32 foundation")
        if foundation not in groups:
            groups[foundation] = f"<foundation-{len(groups)}>"
        candidates.append({**candidate, "foundation": groups[foundation]})
    return {**value, "candidates": candidates}


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


def load_provenance():
    """corpus/provenance.py reads the tier register and the probe matrix."""
    spec = importlib.util.spec_from_file_location(
        "apostate_provenance", PROVENANCE_MODULE)
    if spec is None or spec.loader is None:
        raise RuntimeError("cannot load %s" % PROVENANCE_MODULE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("reference", type=pathlib.Path, help="T0 capture of the device being claimed")
    ap.add_argument("subject", type=pathlib.Path, help="capture taken from Apostate")
    ap.add_argument("--show-volatile", action="store_true", help="also list fields excluded as volatile")
    ap.add_argument("--max-per-probe", type=int, default=6, help="mismatch lines shown per probe")
    ap.add_argument("--tiers", type=pathlib.Path,
                    help="capture tier register (default corpus/capture-tiers.json)")
    ap.add_argument("--matrix", type=pathlib.Path,
                    help="per-probe collector matrix (default corpus/collector-probe-matrix.json)")
    args = ap.parse_args()

    ref = json.loads(args.reference.read_text())
    sub = json.loads(args.subject.read_text())

    provenance = load_provenance()

    # The reference has to be a real device. Our own browser's output is tiered
    # not-a-reference, and a capture nobody has recorded at all is refused
    # rather than assumed: a tier is a claim about the world and absence of a
    # claim is not a weak claim, it is none.
    try:
        reference_tier = provenance.load_tiers(args.tiers).lookup(args.reference)
    except provenance.ProvenanceError as exc:
        print("REFUSED: %s" % exc)
        return 2
    refusal = provenance.reference_refusal(reference_tier)
    if refusal:
        print("REFUSED: %s" % refusal)
        return 2

    # Per-probe comparability. Identical collectors need no matrix: one
    # instrument measured both sides, which is the strongest evidence there is.
    # Different collectors are compared only where the matrix says the probe's
    # implementation is byte-identical, helpers included.
    ref_collector = ref["context"]["collector_sha256"]
    sub_collector = sub["context"]["collector_sha256"]
    verdicts = None
    if ref_collector != sub_collector:
        try:
            verdicts = provenance.load_matrix(args.matrix).verdicts(
                ref_collector, sub_collector)
        except provenance.ProvenanceError as exc:
            print("REFUSED: %s" % exc)
            print("Captures taken by different collectors are compared per "
                  "probe, so a generation whose source cannot be read cannot "
                  "be compared at all.")
            return 2

    volatile = ALWAYS_VOLATILE | measured_volatility(ref) | measured_volatility(sub)

    # coh.render-determinism, decided rather than excused. A render surface
    # that disagrees with its own second read is a defect on whichever side
    # produced it, so it is reported before the cross-capture comparison: the
    # comparison's premise is that each side reproduces itself.
    determinism = [(side, pid, mismatches)
                   for side, capture in (("reference", ref), ("subject", sub))
                   for pid, mismatches in determinism_failures(capture)]

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
    print(f"reference tier: {reference_tier['tier']}  ({reference_tier['device']})")
    if verdicts is None:
        print(f"collector : {ref_collector[:8]} on both sides; every probe comparable")
    else:
        print(f"collector : reference {ref_collector[:8]}, subject {sub_collector[:8]}; "
              "per-probe comparability from corpus/collector-probe-matrix.json")
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
    incomparable = []
    version_derived = []

    for pid in sorted(rp):
        if verdicts is not None:
            verdict = verdicts.get(pid, "not-in-matrix")
            if verdict != "comparable":
                # Reported, never quietly folded in with the volatile set: the
                # reason this probe is not being checked is a property of the
                # instrument, and a reader has to be able to see which claims
                # the run did not test.
                incomparable.append((pid, verdict))
                continue
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
        if pid == "webrtc.ice":
            try:
                rv, sv = normalise_ice(rv), normalise_ice(sv)
            except ValueError as exc:
                errored.append((pid, str(exc)))
                continue

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

    for side, pid, mismatches in determinism:
        print(f"FAIL  {pid}   ({len(mismatches)} field(s) differ between the {side}'s "
              f"own two reads; coh.render-determinism)")
        for p, a, b in mismatches[:args.max_per_probe]:
            loc = ".".join(p) if p else "<value>"
            print(f"        {loc}")
            print(f"          first  : {truncate(a)}")
            print(f"          repeat : {truncate(b)}")
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

    if incomparable:
        by_verdict = {}
        for pid, verdict in incomparable:
            by_verdict.setdefault(verdict, []).append(pid)
        print(f"not compared — the two collectors do not measure these the same "
              f"way ({len(incomparable)} probe(s)):")
        for verdict, pids in sorted(by_verdict.items()):
            print(f"  {verdict}: {', '.join(sorted(pids))}")
        print(f"  reference collector {ref_collector[:8]}, subject "
              f"{sub_collector[:8]}. Verdicts come from the per-probe source "
              f"digests in corpus/collector-probe-matrix.json, which cover each")
        print("  probe's own implementation and the helpers it calls. They do not")
        print("  cover a later probe perturbing an earlier one through timing,")
        print("  GPU memory or permission state; only an A/B run of both")
        print("  collectors on one host would settle that.")
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
          f"{len(errored)} errored    {len(skipped)} volatile    "
          f"{len(incomparable)} not comparable    "
          f"{len(determinism)} non-deterministic")

    if not build_comparable:
        print("INCOMPLETE: matching full browser builds are required for V3.")
        return 2
    return 0 if not failed and not errored and not determinism else 1


if __name__ == "__main__":
    sys.exit(main())
