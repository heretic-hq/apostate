#!/usr/bin/env python3
"""Coherence gate: decide the invariants that no field diff can decide.

conform.py answers "is this capture the device it claims to be" by comparing
field against field. That question cannot express "can these fields be true at
the same time", and most real detections are a broken relation rather than a
wrong value. This is the instrument for the relations, and it is named in
ledger/coherence.jsonl's `enforced_by` as `capture/derive/coherence.py:<edge>`.

Why a separate instrument rather than more collector probes. Three of these
edges are relations over window position and size, and conform.py deliberately
excludes screenX, screenY, outerWidth, outerHeight, availWidth and availHeight
from comparison because window geometry is the user's choice rather than the
device's identity. A capture taken in a 1728-wide window has to conform against
a reference taken in an 800x600 one. So however faithfully those operands are
recorded, a diff against a reference cannot decide a relation over them: the
decision has to be made inside one running browser, against no reference at
all. Adding probes to the collector would also change the collector's identity
and strand every reference capture taken by the previous one — see
capture/README.md, "Collector generations".

Four modes, because each edge is decided by the instrument its own statement
names, and a mode that cannot decide an edge says so instead of passing it:

    live     launch a browser at capture/coherence/probe.html and decide the
             single-binary edges against it
    result   the same decision from a probe result already collected
    capture  decide coh.render-determinism from one collector capture, by
             comparing its first and repeat reads of every render surface
    pair     decide coh.audio-render-independent-of-audio-hardware from two
             probe results taken under profiles that differ in the claimed
             audio hardware

Outcomes, and what each means for the exit status:

    PASS           the relation was evaluated and holds
    FAIL           the relation was evaluated and does not hold      -> exit 1
    ERROR          the operands are missing or unreadable            -> exit 1
    INCONCLUSIVE   the right inputs were given and the run did not
                   exercise the invariant                            -> exit 2
    SKIP           this mode cannot decide this edge by construction

INCONCLUSIVE exists because of the failure mode it prevents. Two runs of stock
Chrome agree on every audio render hash, and calling that a pass would be a
check that passes on correct and broken input alike: the statement is about two
profiles that differ in the claimed audio hardware, and two runs that claim the
same hardware have not varied the thing the invariant is about. A gate that
cannot tell those apart is worse than no gate.

Usage:
    capture/derive/coherence.py live --browser out/Release/chrome [-- --window-size=1200,800]
    capture/derive/coherence.py result /tmp/probe.json
    capture/derive/coherence.py capture resources/fingerprints/raw/<name>.json
    capture/derive/coherence.py pair /tmp/a.json /tmp/b.json
"""

import argparse
import contextlib
import http.server
import json
import os
import pathlib
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time

HERE = pathlib.Path(__file__).resolve().parent
PROBE = HERE.parent / "coherence" / "probe.html"

sys.path.insert(0, str(HERE))
# One definition of "these two values differ", shared with the V3 differ, so
# the two tools cannot disagree about what a difference is.
from conform import diff, truncate  # noqa: E402

# The render surfaces coh.render-determinism names, and the capture probe that
# records each. Both reads of each are already in every capture: the collector
# runs the whole probe a second time, which for these five means a new canvas
# element, a new WebGL context with recompiled shaders, a new
# OfflineAudioContext and a freshly built and detached DOM subtree — so the
# second read is an independent recomputation and can genuinely differ, rather
# than a re-read of a buffer that was already rasterised.
RENDER_SURFACES = (
    ("canvas.2d", "canvas.2d"),
    ("webgl.readpixels", "webgl1"),
    ("webgl.readpixels", "webgl2"),
    ("audio.offline-render", "audio.offline_render"),
    ("layout.client-rects", "clientrects"),
)

PASS, FAIL, ERROR, INCONCLUSIVE, SKIP = "PASS", "FAIL", "ERROR", "INCONCLUSIVE", "SKIP"


class Result:
    """One edge's outcome, its one-line summary, and the numbers behind it."""

    __slots__ = ("outcome", "summary", "detail")

    def __init__(self, outcome, summary, detail=()):
        self.outcome = outcome
        self.summary = summary
        self.detail = list(detail)


CHECKS = {}


def check(edge, modes, decide):
    """Register the check that decides *edge*.

    scripts/check-schema-wiring.py resolves every
    `capture/derive/coherence.py:<edge id>` enforcement site against these
    registrations, and requires the site to name the row's own edge. Deleting a
    registration therefore makes the ledger row fail rather than leaving its
    claim standing.
    """
    CHECKS[edge] = (modes, decide)


# ---------------------------------------------------------------------------
# Reading operands out of a probe result
# ---------------------------------------------------------------------------


def reads_of(result, edge):
    """-> ([value, ...], error). Both reads of one edge's operands."""
    entry = (result.get("edges") or {}).get(edge)
    if entry is None:
        return [], f"the probe result carries no {edge} measurement"
    if not entry.get("ok"):
        return [], f"the probe could not measure it: {entry.get('error')}"
    values = [entry.get("value")]
    if entry.get("repeat") is not None:
        values.append(entry["repeat"])
    return values, None


def number(value, where):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{where} is {value!r}, not a number")
    return value


def css_px(value, where):
    """'37px' -> 37.0. A used value that is not in px is not comparable."""
    if not isinstance(value, str) or not value.endswith("px"):
        raise ValueError(f"{where} is {value!r}, not a px length")
    return float(value[:-2])


def signed(value):
    """'+380', '-1700', '+0.015625'. Full precision, because a sub-pixel
    overflow is the interesting kind and %+d would print it as +0."""
    return "+%s" % value if value >= 0 else str(value)


# ---------------------------------------------------------------------------
# coh.window-within-avail-rect
# ---------------------------------------------------------------------------


def window_within_avail_rect(result, profile=None):
    values, error = reads_of(result, "coh.window-within-avail-rect")
    if error:
        return Result(ERROR, error)
    if not result.get("context", {}).get("headed"):
        # outerWidth is 0 and there is no work area, so every clause would hold
        # arithmetically while measuring nothing. The row's requirement is about
        # a window that exists.
        return Result(INCONCLUSIVE, "the browser reported no window; a headed run is required")

    detail, broken, moved = [], [], False
    previous = None
    for index, value in enumerate(values):
        try:
            g = {k: number(value.get(k), k) for k in (
                "screenX", "screenY", "outerWidth", "outerHeight",
                "availLeft", "availTop", "availWidth", "availHeight")}
        except (AttributeError, ValueError) as exc:
            return Result(ERROR, f"read {index + 1}: {exc}")
        right, bottom = g["screenX"] + g["outerWidth"], g["screenY"] + g["outerHeight"]
        clauses = {
            "screenX >= availLeft": g["screenX"] >= g["availLeft"],
            "screenY >= availTop": g["screenY"] >= g["availTop"],
            "screenX + outerWidth <= availLeft + availWidth":
                right <= g["availLeft"] + g["availWidth"],
            "screenY + outerHeight <= availTop + availHeight":
                bottom <= g["availTop"] + g["availHeight"],
        }
        # "Negative screen coordinates are impossible for a single-display
        # profile" is true of one display and false of two, where a panel left
        # of or above the primary gives its neighbours negative origins.
        # isExtended separates the cases; the clause is evaluated where it
        # applies and reported as untested where it does not.
        extended = value.get("isExtended")
        if extended is False:
            clauses["screenX >= 0 and screenY >= 0"] = g["screenX"] >= 0 and g["screenY"] >= 0
        failed = sorted(k for k, ok in clauses.items() if not ok)
        signature = tuple(sorted(clauses.items()))
        if previous is not None and signature != previous:
            moved = True
        previous = signature
        if failed:
            broken.extend(failed)
            detail.append(f"read {index + 1}: " + ", ".join(failed))
            # %s rather than a float format throughout: these values are the
            # evidence, and %g would round 1060.015625 to 1060 in a report
            # whose whole subject is exactness.
            detail.append(
                "  window   screenX %s screenY %s outerWidth %s outerHeight %s"
                % (g["screenX"], g["screenY"], g["outerWidth"], g["outerHeight"]))
            detail.append(
                "  work area availLeft %s availTop %s availWidth %s availHeight %s"
                % (g["availLeft"], g["availTop"], g["availWidth"], g["availHeight"]))
            detail.append(
                "  screen   %sx%s  isExtended %s"
                % (number(value.get("screenWidth"), "screenWidth"),
                   number(value.get("screenHeight"), "screenHeight"), extended))
            detail.append(
                "  overflow (positive is outside) left %s top %s right %s bottom %s"
                % tuple(signed(v) for v in (
                    g["availLeft"] - g["screenX"], g["availTop"] - g["screenY"],
                    right - (g["availLeft"] + g["availWidth"]),
                    bottom - (g["availTop"] + g["availHeight"]))))
    if broken:
        return Result(FAIL, "%d clause(s) violated across %d read(s)"
                      % (len(broken), len(values)), detail)
    if moved:
        return Result(FAIL, "the relation held on one read and not the other", detail)
    extended = values[0].get("isExtended")
    return Result(PASS, "window rect inside the work area on %d read(s)%s"
                  % (len(values),
                     "" if extended is False else
                     "; origin sign untested, isExtended is %r" % (extended,)))


check("coh.window-within-avail-rect", ("result",), window_within_avail_rect)


# ---------------------------------------------------------------------------
# coh.layout-integer-positions
# ---------------------------------------------------------------------------


def layout_integer_positions(result, profile=None):
    values, error = reads_of(result, "coh.layout-integer-positions")
    if error:
        return Result(ERROR, error)
    detail, broken = [], 0
    for index, value in enumerate(values):
        for sample in value.get("samples") or ():
            sid = sample.get("id")
            try:
                declared = {k: number(sample["declared"][k], f"{sid}.declared.{k}")
                            for k in ("left", "top")}
                computed = {k: css_px(sample["computed"][k], f"{sid}.computed.{k}")
                            for k in ("left", "top")}
                rect = {k: number(sample["rect"][k], f"{sid}.rect.{k}")
                        for k in ("x", "y")}
            except (KeyError, TypeError, ValueError) as exc:
                return Result(ERROR, f"read {index + 1}: {exc}")
            pairs = (("left", "x"), ("top", "y"))
            bad = [(side, axis) for side, axis in pairs
                   if computed[side] != declared[side] or rect[axis] != computed[side]]
            if not bad:
                continue
            broken += 1
            detail.append(f"read {index + 1}: {sid}")
            for side, axis in pairs:
                # 1/64 px is LayoutUnit's quantum, so a delta expressed in them
                # names the artifact directly rather than as a long decimal:
                # the differential behind this row read -1/64 and -1/32.
                delta = rect[axis] - declared[side]
                detail.append(
                    "  %-4s declared %s  computed %s  rect %s  delta %+d/64 px"
                    % (side, declared[side], computed[side], rect[axis], round(delta * 64)))
    if broken:
        return Result(FAIL, "%d sample(s) disagree with their declaration" % broken, detail)
    count = len((values[0].get("samples") or ()))
    return Result(PASS, "%d integer-positioned sample(s) exact on %d read(s)"
                  % (count, len(values)))


check("coh.layout-integer-positions", ("result",), layout_integer_positions)


# ---------------------------------------------------------------------------
# coh.resolved-timezone-in-supported-set
# ---------------------------------------------------------------------------


def same_offset_candidates(zone, supported, limit=3):
    """Supported zones whose current UTC offset matches *zone*'s.

    What a profile should have named instead, when it named something ICU
    accepts and the enumeration does not carry. Reported as unavailable rather
    than guessed when the host's tz database does not know the zone.
    """
    try:
        import datetime
        import zoneinfo
    except ImportError:
        return None
    now = datetime.datetime.now(datetime.timezone.utc)
    try:
        want = zoneinfo.ZoneInfo(zone).utcoffset(now)
    except Exception:
        return None
    out = []
    for candidate in supported:
        try:
            if zoneinfo.ZoneInfo(candidate).utcoffset(now) == want:
                out.append(candidate)
        except Exception:
            continue
        if len(out) >= limit:
            break
    return out


def location_shaped(zone):
    """Does this id look like the Region/City ids the enumeration carries?

    ICU's enumeration is exactly the canonical LOCATION zones: measured on
    stock Chrome 151 its 418 entries all contain a slash and none begins with
    Etc/. So a zone of that shape which the enumeration does not carry means
    resolvedOptions and supportedValuesOf disagree about one ICU, which is an
    incoherence; a zone outside that shape (UTC, Etc/GMT-7) is a canonical id
    the enumeration omits by construction, which is not.
    """
    return "/" in zone and not zone.startswith("Etc/")


def resolved_timezone_in_supported_set(result, profile=None):
    values, error = reads_of(result, "coh.resolved-timezone-in-supported-set")
    if error:
        return Result(ERROR, error)
    detail, broken, notes, off_location = [], [], [], []
    for index, value in enumerate(values):
        zone, supported = value.get("timezone"), value.get("supported")
        if not isinstance(zone, str) or not isinstance(supported, list) or not supported:
            return Result(ERROR, f"read {index + 1}: no zone or no enumeration to check it against")
        worker = value.get("worker") or {}
        realms = (("document", zone, value.get("canonical"), zone in supported),
                  ("worker", worker.get("timezone"), worker.get("canonical"),
                   worker.get("in_supported_set")))
        for realm, name, canonical, inside in realms:
            if name is None or canonical is None or inside is None:
                broken.append(realm)
                detail.append("read %d: the %s realm did not report a zone, its canonical "
                              "form and its membership: %s"
                              % (index + 1, realm, worker.get("error") or "operand missing"))
                continue
            # The decisive clause. resolvedOptions canonicalises whatever zone
            # was adopted, so a canonical id resolves to itself and an alias
            # does not. A change that writes a profile's string into the
            # reported value without canonicalising it is visible here and
            # nowhere else in the browser.
            if canonical != name:
                broken.append(realm)
                detail.append("read %d: the %s realm reports %r, which ICU canonicalises to "
                              "%r — the reported value is an alias, so it and the profile "
                              "field have diverged"
                              % (index + 1, realm, name, canonical))
                continue
            if inside:
                continue
            if location_shaped(name):
                broken.append(realm)
                detail.append("read %d: the %s realm resolved %r, a location zone the "
                              "%d-entry enumeration does not carry, so resolvedOptions and "
                              "supportedValuesOf disagree about one ICU"
                              % (index + 1, realm, name, len(supported)))
                candidates = same_offset_candidates(name, supported)
                if candidates is None:
                    detail.append("  no same-offset candidates: this host's tz database "
                                  "does not know %r either" % (name,))
                elif candidates:
                    detail.append("  same current offset, and enumerated: "
                                  + ", ".join(candidates))
            else:
                # UTC and the Etc/ family: canonical, and omitted from the
                # enumeration by ICU's own filter. The browser is coherent; a
                # PROFILE naming one of these is a different problem, and only
                # a profile-bearing run can decide that.
                off_location.append((realm, name))
        if worker.get("timezone") is not None and worker["timezone"] != zone:
            # A different edge's business (coh.timezone-across-contexts, which
            # patch 0011 enforces by setting the ICU default in every process).
            # Recorded because this check is the one that noticed, and not
            # failed here because both zones can be canonical and disagree.
            notes.append("the worker realm resolved %r against the document's %r; that is "
                         "coh.timezone-across-contexts, not this edge"
                         % (worker["timezone"], zone))

    first = values[0]
    supported, zone = first["supported"], first["timezone"]
    if profile is not None:
        claimed = ((profile.get("locale") or {}).get("timezone"))
        if claimed is None:
            notes.append("the profile declares no locale.timezone, so the claim half of this "
                         "edge has nothing to compare")
        else:
            if claimed != zone:
                broken.append("profile")
                detail.append("the profile claims locale.timezone %r and the browser reports "
                              "%r: the silent divergence this row exists to catch"
                              % (claimed, zone))
            if claimed not in supported:
                broken.append("profile")
                detail.append("the profile claims locale.timezone %r, which is not one of the "
                              "%d canonical location zones a real machine reports"
                              % (claimed, len(supported)))
    if broken:
        return Result(FAIL, "%d realm or claim read(s) violate the relation" % len(broken),
                      detail + ["note: " + n for n in notes])
    if off_location:
        names = sorted({name for _, name in off_location})
        return Result(PASS,
                      "%s is canonical in every realm and outside the %d-entry location set "
                      "by ICU's own filter, which is coherent for a host configured that way. "
                      "A profile must still name a location zone: pass --profile to decide it."
                      % (", ".join(names), len(supported)),
                      ["note: " + n for n in notes])
    return Result(PASS, "%s is canonical and in the %d-entry enumeration, in the document and "
                        "worker realms%s"
                  % (zone, len(supported),
                     "" if profile is None else ", and is the zone the profile claims"),
                  ["note: " + n for n in notes])


check("coh.resolved-timezone-in-supported-set", ("result",), resolved_timezone_in_supported_set)


# ---------------------------------------------------------------------------
# coh.audio-render-independent-of-audio-hardware
# ---------------------------------------------------------------------------


def audio_clauses(result, label):
    """-> (hardware, renders, failures). The half one run can decide.

    The offline render's rate, length and channel count are its constructor
    arguments and nothing on that path reads a device, so a run where they come
    back changed has the coupling this edge forbids — and one run is enough to
    say so. What one run cannot do is confirm the edge: that needs a second run
    whose claimed hardware differs.
    """
    values, error = reads_of(result, "coh.audio-render-independent-of-audio-hardware")
    if error:
        return None, None, [f"{label}: {error}"]
    hardware, failures = values[0].get("hardware") or {}, []
    renders = {}
    for index, value in enumerate(values):
        for render in value.get("renders") or ():
            want, got = render.get("requested") or {}, render.get("reported") or {}
            key = (want.get("channels"), want.get("rate"))
            for field, asked in (("sampleRate", want.get("rate")),
                                 ("length", want.get("length")),
                                 ("numberOfChannels", want.get("channels"))):
                if got.get(field) != asked:
                    failures.append(
                        "%s: the render asked for %r at %r Hz came back with %s %r, not %r"
                        % (label, want.get("channels"), want.get("rate"),
                           field, got.get(field), asked))
            if not (isinstance(render.get("slice_sum"), (int, float))
                    and render["slice_sum"] > 0):
                failures.append(
                    "%s: the render at %r Hz is silent (slice_sum %r); a zeroed buffer "
                    "hashes identically everywhere and would make any comparison vacuous"
                    % (label, want.get("rate"), render.get("slice_sum")))
            if index == 0:
                renders[key] = render
            elif renders.get(key, {}).get("sha256") != render.get("sha256"):
                failures.append(
                    "%s: the render at %r Hz did not repeat within the session (%s then %s)"
                    % (label, want.get("rate"),
                       truncate(renders.get(key, {}).get("sha256"), 20),
                       truncate(render.get("sha256"), 20)))
    return hardware, renders, failures


def audio_independent_single(result, profile=None):
    hardware, renders, failures = audio_clauses(result, "run")
    if failures and renders is None:
        return Result(ERROR, failures[0])
    if failures:
        return Result(FAIL, "%d constructor argument(s) or render(s) came back coupled "
                            "to the hardware claim" % len(failures), failures)
    claim = "sampleRate %r maxChannelCount %r" % (
        hardware.get("sampleRate"), hardware.get("maxChannelCount"))
    off = [r for (_, rate), r in renders.items() if rate != hardware.get("sampleRate")]
    return Result(SKIP,
                  "one run cannot decide this: %d render(s) honoured their constructor "
                  "arguments, %d of them at a rate the claimed hardware (%s) does not run "
                  "at. The statement compares two profiles, so run `pair`."
                  % (len(renders), len(off), claim))


def audio_independent_pair(first, second):
    hardware_a, renders_a, failures = audio_clauses(first, "A")
    hardware_b, renders_b, more = audio_clauses(second, "B")
    failures = failures + more
    if renders_a is None or renders_b is None:
        return Result(ERROR, failures[0])
    if failures:
        # Decisive whatever the builds are: a render that did not honour its
        # own constructor arguments refutes the edge on one run.
        return Result(FAIL, "%d constructor argument(s) or render(s) came back coupled "
                            "to the hardware claim" % len(failures), failures)

    # An audio render digest is a property of the build that produced it, so two
    # runs of DIFFERENT browsers differ for a reason this edge says nothing
    # about, and comparing them would report a coupling that is really a version
    # change. conform.py refuses a cross-build comparison for the same reason;
    # this is that refusal in the shape this gate uses.
    ua_a = (first.get("context") or {}).get("ua")
    ua_b = (second.get("context") or {}).get("ua")
    if ua_a != ua_b:
        return Result(INCONCLUSIVE,
                      "the two runs are different browsers, so their render digests are not "
                      "comparable and any difference would be the build rather than the "
                      "hardware claim",
                      ["A %s" % ua_a, "B %s" % ua_b])

    claim_a = (hardware_a.get("sampleRate"), hardware_a.get("maxChannelCount"))
    claim_b = (hardware_b.get("sampleRate"), hardware_b.get("maxChannelCount"))
    shape = ["A claims sampleRate %r maxChannelCount %r" % claim_a,
             "B claims sampleRate %r maxChannelCount %r" % claim_b]
    if claim_a == claim_b:
        # Refusing to call this a pass is the point. The statement is about two
        # profiles that differ in the claimed audio hardware; two runs that
        # claim the same hardware have not varied the thing being tested, and
        # their agreement is evidence of nothing.
        return Result(INCONCLUSIVE,
                      "both runs claim the same audio hardware, so agreeing render hashes "
                      "are not evidence for this edge", shape)

    differing = []
    for key in sorted(set(renders_a) | set(renders_b)):
        a, b = renders_a.get(key), renders_b.get(key)
        if a is None or b is None:
            differing.append("the render %r is present in only one run" % (key,))
            continue
        if a.get("sha256") != b.get("sha256"):
            differing.append("%r channel(s) at %r Hz: A %s  B %s"
                             % (key[0], key[1], a.get("sha256"), b.get("sha256")))
    if differing:
        return Result(FAIL, "%d render(s) changed with the audio hardware claim"
                      % len(differing), shape + differing)
    return Result(PASS, "%d render(s) byte-identical across two differing audio hardware "
                        "claims" % len(renders_a), shape)


check("coh.audio-render-independent-of-audio-hardware", ("result", "pair"),
      audio_independent_single)


# ---------------------------------------------------------------------------
# coh.render-determinism
# ---------------------------------------------------------------------------


def render_determinism(capture, profile=None):
    probes, repeat = capture.get("probes") or {}, capture.get("repeat") or {}
    detail, broken, missing = [], [], []
    measured = 0
    for surface, pid in RENDER_SURFACES:
        first, second = probes.get(pid), repeat.get(pid)
        if first is None or second is None:
            missing.append("%s (%s) was not read twice" % (surface, pid))
            continue
        if not first.get("ok") or not second.get("ok"):
            missing.append("%s (%s) did not complete: %s"
                           % (surface, pid, first.get("error") or second.get("error")))
            continue
        measured += 1
        mismatches = list(diff(first["value"], second["value"]))
        if not mismatches:
            continue
        broken.append(surface)
        detail.append("%s (%s): %d field(s) differ between the two reads"
                      % (surface, pid, len(mismatches)))
        for path, a, b in mismatches[:6]:
            detail.append("  %s" % (".".join(path) if path else "<value>"))
            detail.append("    first  : %s" % truncate(a))
            detail.append("    repeat : %s" % truncate(b))
        if len(mismatches) > 6:
            detail.append("  … %d more" % (len(mismatches) - 6))
    if broken:
        return Result(FAIL, "%d render surface(s) did not repeat within the session"
                      % len(broken), detail)
    if missing and not measured:
        return Result(ERROR, "no render surface was read twice", missing)
    if missing:
        # A surface the capture could not measure is not a broken invariant: a
        # host with no GPU reports webgl1 as failed, and calling that a lost
        # determinism would blame the browser for the host. The inputs were
        # right and the run did not exercise everything, which is what
        # INCONCLUSIVE is for.
        return Result(INCONCLUSIVE,
                      "%d of %d render surface(s) could not be compared; the other %d "
                      "repeated exactly"
                      % (len(missing), len(RENDER_SURFACES), measured), missing)
    return Result(PASS, "%d render surface(s) byte-identical across the session's two reads"
                  % measured)


check("coh.render-determinism", ("capture",), render_determinism)


# ---------------------------------------------------------------------------
# The runner
# ---------------------------------------------------------------------------


class Receiver(http.server.BaseHTTPRequestHandler):
    """Serves the probe and keeps the one result it POSTs back."""

    page = b""
    received = None

    def log_message(self, fmt, *args):
        pass

    def do_GET(self):
        if self.path.startswith("/favicon.ico"):
            self.send_response(204)
            self.end_headers()
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(self.page)))
        self.end_headers()
        self.wfile.write(self.page)

    def do_POST(self):
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length)
        try:
            Receiver.received = json.loads(body)
            code, payload = 200, b'{"ok":true}'
        except ValueError as exc:
            Receiver.received = {"__parse_error": str(exc)}
            code, payload = 400, b'{"ok":false}'
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)


def collect(browser, extra, timeout, xvfb, screen, label):
    """Launch *browser* at the probe page and return the result it POSTs.

    Headed, always. Every clause of coh.window-within-avail-rect is about a
    window that is really on a screen, and a headless browser reports
    outerWidth 0 against an empty work area — which satisfies the arithmetic
    while measuring nothing. On Linux that means a virtual display, which is
    also the configuration scripts/run-v3.sh launches, so the gate tests what
    ships rather than a headless approximation of it.
    """
    Receiver.page = PROBE.read_bytes()
    Receiver.received = None
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Receiver)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    port = server.server_address[1]
    url = "http://127.0.0.1:%d/?label=%s" % (port, label)

    profile = pathlib.Path(tempfile.mkdtemp(prefix="apostate-coherence-"))
    argv = []
    if xvfb:
        if not shutil.which("xvfb-run"):
            raise SystemExit("xvfb-run is missing; install it or pass --no-xvfb")
        argv += ["xvfb-run", "-a", "--server-args=-screen 0 %s" % screen]
    argv += [browser, "--no-first-run", "--no-default-browser-check",
             "--user-data-dir=%s" % profile]
    if os.name == "posix" and hasattr(os, "geteuid") and os.geteuid() == 0:
        # Unavoidable as root and itself a deviation from a normal launch, the
        # same note capture/collect-unattended.py carries.
        argv.append("--no-sandbox")
    argv += list(extra) + [url]

    print("launching  %s" % " ".join(argv))
    log = profile.parent / (profile.name + ".log")
    with log.open("wb") as sink:
        # A process group, killed by the pid captured here. Never a pattern
        # match: a pattern broad enough to match the browser is also broad
        # enough to match this script, which has cost this project a shell.
        child = subprocess.Popen(argv, stdout=sink, stderr=subprocess.STDOUT,
                                 stdin=subprocess.DEVNULL, start_new_session=True)
    deadline = time.monotonic() + timeout
    try:
        while Receiver.received is None and time.monotonic() < deadline:
            if child.poll() is not None and Receiver.received is None:
                time.sleep(0.5)
                break
            time.sleep(0.25)
    finally:
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(child.pid), signal.SIGTERM)
        with contextlib.suppress(subprocess.TimeoutExpired):
            child.wait(timeout=10)
        with contextlib.suppress(ProcessLookupError, PermissionError):
            os.killpg(os.getpgid(child.pid), signal.SIGKILL)
        server.shutdown()
        shutil.rmtree(profile, ignore_errors=True)

    if Receiver.received is None:
        tail = log.read_text(errors="replace").splitlines()[-12:] if log.exists() else []
        log.unlink(missing_ok=True)
        raise SystemExit("no result arrived within %gs; browser output:\n  %s"
                         % (timeout, "\n  ".join(tail) or "<none>"))
    log.unlink(missing_ok=True)
    return Receiver.received


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def report(results, context):
    if context:
        print("subject   : %s" % (context.get("label") or "<unlabelled>"))
        print("            %s" % (context.get("ua") or "<no user agent>"))
        print("            headed %s   dpr %s   secure context %s"
              % (context.get("headed"), context.get("device_pixel_ratio"),
                 context.get("secure_context")))
        if context.get("automation_signals"):
            print("            note: automation signals present: %s"
                  % ", ".join(context["automation_signals"]))
        print()
    width = max(len(edge) for edge in results)
    for edge, result in results.items():
        print("%-12s %-*s  %s" % (result.outcome, width, edge, result.summary))
        for line in result.detail:
            print("        %s" % line)
    tally = {}
    for result in results.values():
        tally[result.outcome] = tally.get(result.outcome, 0) + 1
    print()
    print("  ".join("%d %s" % (tally[k], k.lower()) for k in
                    (PASS, FAIL, ERROR, INCONCLUSIVE, SKIP) if k in tally))
    if tally.get(FAIL) or tally.get(ERROR):
        return 1
    return 2 if tally.get(INCONCLUSIVE) else 0


def run_mode(mode, payload, context, profile=None):
    results = {}
    for edge, (modes, decide) in CHECKS.items():
        if mode not in modes:
            results[edge] = Result(SKIP, "not decidable from a %s; needs %s"
                                   % (mode, " or ".join(modes)))
            continue
        if mode == "pair" and edge == "coh.audio-render-independent-of-audio-hardware":
            results[edge] = audio_independent_pair(*payload)
        else:
            results[edge] = decide(payload, profile)
    return report(results, context)


def load(path):
    try:
        return json.loads(pathlib.Path(path).read_text())
    except (OSError, ValueError) as exc:
        raise SystemExit("cannot read %s: %s" % (path, exc))


def main() -> int:
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest="mode", required=True)

    live = sub.add_parser("live", help="launch a browser at the probe and decide")
    live.add_argument("--browser", required=True, help="browser binary to test")
    live.add_argument("--out", type=pathlib.Path, help="write the probe result here")
    live.add_argument("--label", default="coherence", help="label recorded in the result")
    live.add_argument("--timeout", type=float, default=180.0, help="seconds to wait")
    live.add_argument("--screen", default="1920x1080x24", help="Xvfb screen geometry")
    live.add_argument("--no-xvfb", action="store_true",
                      help="do not wrap the browser in xvfb-run (required on macOS)")
    live.add_argument("--profile", type=pathlib.Path,
                      help="the device profile the browser was launched with; decides the "
                           "claim half of coh.resolved-timezone-in-supported-set")
    live.add_argument("extra", nargs="*", metavar="-- FLAG",
                      help="extra browser flags, e.g. -- --window-size=1200,800")

    res = sub.add_parser("result", help="decide from a probe result already collected")
    res.add_argument("result", type=pathlib.Path)
    res.add_argument("--profile", type=pathlib.Path,
                      help="the device profile the browser was launched with")

    cap = sub.add_parser("capture", help="decide coh.render-determinism from one capture")
    cap.add_argument("capture", type=pathlib.Path)

    pair = sub.add_parser("pair", help="decide the two-profile edge from two probe results")
    pair.add_argument("first", type=pathlib.Path)
    pair.add_argument("second", type=pathlib.Path)

    args = ap.parse_args()

    if args.mode == "live":
        xvfb = not args.no_xvfb and sys.platform.startswith("linux")
        result = collect(args.browser, args.extra, args.timeout, xvfb, args.screen, args.label)
        if args.out:
            args.out.write_text(json.dumps(result, indent=1, sort_keys=True) + "\n")
            print("result    %s" % args.out)
        print()
        profile = load(args.profile) if args.profile else None
        return run_mode("result", result, result.get("context"), profile)

    if args.mode == "result":
        result = load(args.result)
        profile = load(args.profile) if args.profile else None
        return run_mode("result", result, result.get("context"), profile)

    if args.mode == "capture":
        capture = load(args.capture)
        return run_mode("capture", capture, {
            "label": (capture.get("context") or {}).get("label"),
            "ua": (capture.get("context") or {}).get("ua"),
            "headed": (capture.get("context") or {}).get("headed"),
            "device_pixel_ratio": (capture.get("context") or {}).get("device_pixel_ratio"),
            "secure_context": (capture.get("context") or {}).get("secure_context"),
        })

    first, second = load(args.first), load(args.second)
    print("A         : %s" % ((first.get("context") or {}).get("label") or args.first))
    print("B         : %s" % ((second.get("context") or {}).get("label") or args.second))
    print()
    return run_mode("pair", (first, second), None)


if __name__ == "__main__":
    sys.exit(main())
