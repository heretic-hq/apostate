#!/usr/bin/env python3
"""What a capture IS, and which of two captures' probes may be compared.

Two checked-in artifacts, one reader:

  corpus/capture-tiers.json          what each capture is, per capture, explicit
  corpus/collector-probe-matrix.json which probes two collectors measure alike

They answer different questions and neither is derivable from the other. A TIER
is a claim about the world — is this machine a desk or a hypervisor — and no
probe can settle it, so it is recorded with the measurement that would refute
it. COMPARABILITY is not a claim at all: it is the digest of one probe's
implementation at two revisions of the collector, extracted from git by
scripts/build-collector-matrix.py.

Both fail closed. A capture with no tier entry is refused, never trusted; a
collector generation with no matrix entry is refused, never compared. That
direction is the whole point: the defect this module exists to prevent was an
`accepted` admission record for a headless, automation-suspected capture, minted
by calling the record writer directly and believed by everything downstream
because nothing re-derived it.

Consumers load this by path (scripts/decompose-capture.py,
capture/derive/conform.py, scripts/build-anchors.py) rather than as a package,
which is how the rest of this tree shares a policy owner.
"""

import hashlib
import json
import pathlib

CORPUS = pathlib.Path(__file__).resolve().parent
REPO = CORPUS.parent
TIERS_PATH = CORPUS / "capture-tiers.json"
MATRIX_PATH = CORPUS / "collector-probe-matrix.json"

PHYSICAL_FULL = "physical-full"
PHYSICAL_GPU_ONLY = "physical-gpu-only"
NOT_A_REFERENCE = "not-a-reference"
TIER_VALUES = (PHYSICAL_FULL, PHYSICAL_GPU_ONLY, NOT_A_REFERENCE)


class ProvenanceError(Exception):
    """A capture or a collector generation is not admissible for this use."""


def _load(path, what):
    try:
        raw = pathlib.Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise ProvenanceError("cannot read %s (%s): %s" % (path, what, exc))
    try:
        document = json.loads(raw)
    except ValueError as exc:
        raise ProvenanceError("invalid JSON in %s: %s" % (path, exc))
    if not isinstance(document, dict):
        raise ProvenanceError("%s must be a JSON object" % path)
    return document


def _rel_path(path):
    """Repo-relative when the path is inside the tree, absolute when it is not.

    Every message from this module names the file a reader has to open, so the
    name has to be the one they would type.
    """
    try:
        return str(pathlib.Path(path).relative_to(REPO))
    except ValueError:
        return str(path)


def digest_of(path):
    """The lookup key for a capture: the sha256 of its bytes.

    Keyed on content rather than on filename so that moving or copying a
    capture cannot change what it is, and editing one byte of it cannot keep
    the tier it was granted.
    """
    return hashlib.sha256(pathlib.Path(path).read_bytes()).hexdigest()


class Tiers:
    """corpus/capture-tiers.json, indexed by capture digest."""

    def __init__(self, document, path):
        self.path = pathlib.Path(path)
        self.document = document
        captures = document.get("captures")
        if not isinstance(captures, dict) or not captures:
            raise ProvenanceError("%s has no captures" % path)
        self.by_digest = {}
        self.by_name = {}
        for name, entry in captures.items():
            if not isinstance(entry, dict):
                raise ProvenanceError("%s: entry for %s is not an object"
                                      % (path, name))
            tier = entry.get("tier")
            if tier not in TIER_VALUES:
                raise ProvenanceError("%s: %s has tier %r, not one of %s"
                                      % (path, name, tier, ", ".join(TIER_VALUES)))
            for field in ("sha256", "justification", "refuted_by", "device"):
                value = entry.get(field)
                if not isinstance(value, str) or not value.strip():
                    raise ProvenanceError("%s: %s is missing %s"
                                          % (path, name, field))
            digest = entry["sha256"].lower()
            if len(digest) != 64 or digest.strip("0123456789abcdef"):
                raise ProvenanceError("%s: %s has a malformed sha256" % (path, name))
            if digest in self.by_digest:
                raise ProvenanceError(
                    "%s: %s and %s carry the same sha256; one capture cannot "
                    "hold two tiers" % (path, self.by_digest[digest]["name"], name))
            record = dict(entry, name=name)
            self.by_digest[digest] = record
            self.by_name.setdefault(name, record)

    def lookup(self, capture_path):
        """The entry for a capture file, or raise. Absence is a refusal."""
        capture_path = pathlib.Path(capture_path)
        digest = digest_of(capture_path)
        entry = self.by_digest.get(digest)
        if entry is not None:
            return entry
        named = self.by_name.get(capture_path.name)
        if named is not None:
            raise ProvenanceError(
                "%s is recorded in %s against sha256 %s but these bytes are "
                "%s; the capture was edited or replaced, so its tier no longer "
                "applies to it"
                % (capture_path.name, self._rel(), named["sha256"][:16],
                   digest[:16]))
        raise ProvenanceError(
            "%s (sha256 %s) has no entry in %s. A capture's tier is a claim "
            "about the world that cannot be derived, so an unrecorded capture "
            "is refused rather than assumed trustworthy: add an entry naming "
            "what it is, the measurement that supports it and what would refute "
            "it" % (capture_path.name, digest[:16], self._rel()))

    def _rel(self):
        return _rel_path(self.path)


def load_tiers(path=None):
    path = pathlib.Path(path or TIERS_PATH)
    return Tiers(_load(path, "capture tiers"), path)


def reference_refusal(entry):
    """Why *entry*'s capture may not be the reference side of a comparison.

    A conformance run asks "does our browser match this device?". If the
    reference is our own output the question answers itself, so the only tier
    that is refused here is the one that means exactly that.
    """
    if entry["tier"] == NOT_A_REFERENCE:
        return ("reference is tiered %s (%s): %s"
                % (NOT_A_REFERENCE, entry.get("reason_class", "no reason class"),
                   entry["justification"]))
    return None


def anchor_exception_refusal(entry, backend):
    """Why *entry*'s capture may not back a *backend* anchor without an
    accepted admission record, or None if this file allows exactly that.

    The allowance is narrow by construction. It has to be written down per
    capture, it names the one backend it covers, and the caller supplies the
    backend it MEASURED — so an exception granted for a software rasteriser
    cannot be spent on a hardware anchor. This exists because the alternative
    in use was worse: a laundered `accepted` admission record, which reads as
    an ordinary admission and is believed without re-derivation.
    """
    exception = entry.get("anchor_exception")
    if not isinstance(exception, dict):
        return ("%s records no anchor exception for this capture"
                % _rel_path(TIERS_PATH))
    allowed = exception.get("backend")
    if allowed != backend:
        return ("%s records an anchor exception for backend %r, but the "
                "measured backend is %r"
                % (_rel_path(TIERS_PATH), allowed, backend))
    return None


class Matrix:
    """corpus/collector-probe-matrix.json: per-probe comparability, measured."""

    def __init__(self, document, path):
        self.path = pathlib.Path(path)
        self.document = document
        self.generations = document.get("generations")
        self.pairs = document.get("pairs")
        self.unmatrixable = document.get("unmatrixable") or {}
        if not isinstance(self.generations, dict) or not self.generations:
            raise ProvenanceError("%s has no generations" % path)
        if not isinstance(self.pairs, dict):
            raise ProvenanceError("%s has no pairs" % path)
        self.by_prefix = {}
        for digest in self.generations:
            prefix = digest[:8]
            if prefix in self.by_prefix:
                raise ProvenanceError(
                    "%s: collectors %s and %s share the 8-character prefix the "
                    "pair keys use" % (path, self.by_prefix[prefix], digest))
            self.by_prefix[prefix] = digest

    def _rel(self):
        return _rel_path(self.path)

    def generation(self, collector_sha256):
        """The generation record for a collector digest, or raise."""
        if not isinstance(collector_sha256, str):
            raise ProvenanceError("collector_sha256 is missing from the capture")
        entry = self.generations.get(collector_sha256)
        if entry is not None:
            return entry
        stranded = self.unmatrixable.get(collector_sha256)
        if stranded:
            raise ProvenanceError(
                "collector %s is recorded in %s as unmatrixable: %s"
                % (collector_sha256[:8], self._rel(), stranded.get("reason")))
        raise ProvenanceError(
            "collector %s is not in %s. Its source is not in git history of "
            "capture/collector/collector.js, so no probe of a capture taken "
            "with it can be shown comparable to another generation's; recover "
            "the revision and regenerate the matrix, or record why it cannot be"
            % (collector_sha256[:8], self._rel()))

    def verdicts(self, a_collector, b_collector):
        """-> {probe id: verdict} over the union of both generations' probes.

        Verdicts: `comparable` (the two implementations are byte-identical,
        helpers included), `implementation-differs` (both measure it, not the
        same way), `absent-from-reference` / `absent-from-subject` (one
        generation does not have the probe at all).
        """
        left = self.generation(a_collector)
        right = self.generation(b_collector)
        if a_collector == b_collector:
            return {pid: "comparable" for pid in left["probes"]}
        key = "%s->%s" % (a_collector[:8], b_collector[:8])
        pair = self.pairs.get(key)
        if pair is None:
            raise ProvenanceError(
                "%s has no pair entry %s; regenerate it with "
                "scripts/build-collector-matrix.py" % (self._rel(), key))
        out = {}
        for pid in pair.get("comparable", ()):
            out[pid] = "comparable"
        for pid in pair.get("implementation_differs", ()):
            out[pid] = "implementation-differs"
        for pid in pair.get("only_in_a", ()):
            out[pid] = "absent-from-subject"
        for pid in pair.get("only_in_b", ()):
            out[pid] = "absent-from-reference"
        missing = (set(left["probes"]) | set(right["probes"])) - set(out)
        if missing:
            raise ProvenanceError(
                "%s pair %s does not classify %s; regenerate it"
                % (self._rel(), key, ", ".join(sorted(missing))))
        return out


def load_matrix(path=None):
    path = pathlib.Path(path or MATRIX_PATH)
    return Matrix(_load(path, "collector probe matrix"), path)
