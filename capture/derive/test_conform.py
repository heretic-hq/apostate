"""Regression checks for the V3 comparison gate."""

import contextlib
import copy
import hashlib
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import conform


def capture(version="152.0.7977.83", collector="a" * 64):
    probes = {"example": {"ok": True, "value": 7}}
    if version is not None:
        probes["navigator.userAgentData"] = {
            "ok": True, "value": {"high": {"uaFullVersion": version}}}
    return {
        "context": {"collector_sha256": collector, "ua": "Chrome", "label": "test"},
        "probes": probes,
    }


# Two synthetic collector generations that share the `example` probe and differ
# on `moved`, plus one generation the matrix does not know at all. Synthetic
# rather than the real corpus so these stay tests of the comparison rule and
# not of whichever captures happen to be admitted.
def matrix_document():
    def generation(digest, probes):
        return {"capture_version": 2, "collector_sha256": digest,
                "commit": digest[:7], "committed": "2026-09-08T00:00:00+00:00",
                "subject": "synthetic", "probes": {
                    pid: {"deterministic": True, "helpers": [],
                          "source_sha256": source}
                    for pid, source in probes.items()}}
    return {
        "schema": "apostate/corpus/collector-probe-matrix/1",
        "generations": {
            "a" * 64: generation("a" * 64, {"example": "1" * 64, "moved": "2" * 64,
                                            "navigator.userAgentData": "3" * 64}),
            "b" * 64: generation("b" * 64, {"example": "1" * 64, "moved": "9" * 64,
                                            "navigator.userAgentData": "3" * 64}),
        },
        "pairs": {
            "aaaaaaaa->bbbbbbbb": {"comparable": ["example", "navigator.userAgentData"],
                                   "implementation_differs": ["moved"],
                                   "only_in_a": [], "only_in_b": []},
            "bbbbbbbb->aaaaaaaa": {"comparable": ["example", "navigator.userAgentData"],
                                   "implementation_differs": ["moved"],
                                   "only_in_a": [], "only_in_b": []},
        },
        "unmatrixable": {"c" * 64: {"captures": ["synthetic.json"],
                                    "reason": "locally modified, never committed"}},
    }


class ConformTests(unittest.TestCase):
    def compare(self, reference, subject, tier="physical-full", tiers=True):
        with tempfile.TemporaryDirectory() as directory:
            root = pathlib.Path(directory)
            paths = [root / name for name in ("ref.json", "sub.json")]
            for path, data in zip(paths, (reference, subject)):
                path.write_text(json.dumps(data))
            entry = {
                "sha256": hashlib.sha256(paths[0].read_bytes()).hexdigest(),
                "tier": tier,
                "device": "synthetic",
                "justification": "synthetic fixture",
                "refuted_by": "nothing; it is a fixture",
                "reason_class": "our-own-output",
            }
            name = "ref.json"
            if not tiers:
                # A register that exists but does not mention this capture at
                # all: neither its digest nor its name is in it.
                name, entry["sha256"] = "other.json", "0" * 64
            register = root / "tiers.json"
            register.write_text(json.dumps({"captures": {name: entry}}))
            matrix = root / "matrix.json"
            matrix.write_text(json.dumps(matrix_document()))
            output = io.StringIO()
            argv = ["conform.py", *map(str, paths),
                    "--tiers", str(register), "--matrix", str(matrix)]
            with patch("sys.argv", argv), contextlib.redirect_stdout(output):
                result = conform.main()
            return result, output.getvalue()

    def test_matching_build_and_values_pass(self):
        self.assertEqual(self.compare(capture(), capture())[0], 0)

    def test_matching_build_with_value_difference_fails(self):
        subject = capture()
        subject["probes"]["example"]["value"] = 8
        self.assertEqual(self.compare(capture(), subject)[0], 1)

    def test_version_only_difference_is_incomplete(self):
        status, output = self.compare(capture(), capture("152.0.7977.82"))
        self.assertEqual(status, 2)
        self.assertIn("probes match (diagnostic)", output)
        self.assertIn("INCOMPLETE", output)

    def test_missing_version_is_incomplete(self):
        for subject in (capture(), capture(None)):
            self.assertEqual(self.compare(capture(None), subject)[0], 2)

    def test_cross_generation_compares_the_probes_both_collectors_share(self):
        # The old rule refused this pair outright, which stranded 18 of 26
        # admitted captures. `example` is implementation-identical across the
        # two generations, so it is compared and its difference is a finding.
        subject = capture(collector="b" * 64)
        subject["probes"]["example"]["value"] = 8
        status, output = self.compare(capture(), subject)
        self.assertEqual(status, 1)
        self.assertIn("FAIL  example", output)

    def test_probe_whose_implementation_differs_is_reported_not_compared(self):
        reference, subject = capture(), capture(collector="b" * 64)
        reference["probes"]["moved"] = {"ok": True, "value": "measured one way"}
        subject["probes"]["moved"] = {"ok": True, "value": "measured another"}
        status, output = self.compare(reference, subject)
        self.assertEqual(status, 0)
        self.assertIn("implementation-differs: moved", output)
        self.assertIn("1 not comparable", output)
        self.assertNotIn("FAIL  moved", output)

    def test_collector_absent_from_the_matrix_is_refused(self):
        status, output = self.compare(capture(), capture(collector="d" * 64))
        self.assertEqual(status, 2)
        self.assertIn("REFUSED", output)

    def test_unmatrixable_collector_is_refused_with_its_recorded_reason(self):
        status, output = self.compare(capture(), capture(collector="c" * 64))
        self.assertEqual(status, 2)
        self.assertIn("unmatrixable", output)
        self.assertIn("locally modified, never committed", output)

    def test_reference_tiered_not_a_reference_is_refused(self):
        status, output = self.compare(capture(), capture(), tier="not-a-reference")
        self.assertEqual(status, 2)
        self.assertIn("REFUSED", output)
        self.assertIn("not-a-reference", output)

    def test_untiered_reference_is_refused(self):
        status, output = self.compare(capture(), capture(), tiers=False)
        self.assertEqual(status, 2)
        self.assertIn("has no entry in", output)

    def test_failed_subject_probe_stays_error(self):
        subject = copy.deepcopy(capture())
        subject["probes"]["example"] = {"ok": False, "error": "permission denied"}
        status, output = self.compare(capture(), subject)
        self.assertEqual(status, 1)
        self.assertIn("ERROR example", output)

    def test_invalid_ice_on_both_sides_is_error(self):
        reference = capture()
        reference["probes"]["webrtc.ice"] = {
            "ok": True, "value": {"candidates": [{"foundation": "invalid"}]}}
        status, output = self.compare(reference, reference)
        self.assertEqual(status, 1)
        self.assertIn("ERROR webrtc.ice", output)

    def test_render_surface_contradicting_its_own_repeat_read_fails(self):
        # coh.render-determinism. The field diff itself passes here: both sides
        # report the same first read, and only the subject's SECOND read of a
        # render surface differs. Before DETERMINISM_REQUIRED that made the
        # probe volatile and skipped it, so the run reported conformance.
        reference, subject = capture(), capture()
        reference["probes"]["canvas.2d"] = {"ok": True, "value": {"pixels_sha256": "a"}}
        subject["probes"]["canvas.2d"] = {"ok": True, "value": {"pixels_sha256": "a"}}
        subject["repeat"] = {"canvas.2d": {"ok": True, "value": {"pixels_sha256": "b"}}}
        status, output = self.compare(reference, subject)
        self.assertEqual(status, 1)
        self.assertIn("coh.render-determinism", output)
        self.assertIn("1 non-deterministic", output)

    def test_non_render_probe_still_earns_the_volatility_exemption(self):
        # The escape hatch is not removed, only withdrawn from the five render
        # surfaces: a probe that really does move between reads on real
        # hardware must still not hold the subject to a value the hardware
        # does not reproduce.
        reference, subject = capture(), capture()
        for one in (reference, subject):
            one["probes"]["speech.voices"] = {"ok": True, "value": 1}
            one["repeat"] = {"speech.voices": {"ok": True, "value": 2}}
        subject["probes"]["speech.voices"]["value"] = 99
        self.assertEqual(self.compare(reference, subject)[0], 0)


class LedgerClaimTests(unittest.TestCase):
    """The ledger claim that these five probes enforce coh.render-determinism.

    ledger/coherence.jsonl names five `v3-probe` sites for that edge, and the
    schema's assignment rule permits that kind only where a V3 run decides the
    edge. Here, it does so only because DETERMINISM_REQUIRED withdraws the
    volatility exemption from exactly those five probes. That dependency runs
    across two files and check-schema-wiring.py deliberately never reads this
    one, so without this test the rule is prose in two places and a control in
    neither: deleting DETERMINISM_REQUIRED would leave the ledger asserting an
    enforcement that had stopped existing, which is the same shape of stale
    claim the row was opened about.
    """

    def sites(self):
        ledger = pathlib.Path(__file__).resolve().parents[2] / "ledger" / "coherence.jsonl"
        for line in ledger.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            if row.get("id") == "coh.render-determinism":
                return {entry["site"].split(":", 1)[1]
                        for entry in row["enforced_by"] if entry["kind"] == "v3-probe"}
        self.fail("ledger/coherence.jsonl has no coh.render-determinism row")

    def test_cited_probes_are_exactly_the_probes_held_to_their_repeat_read(self):
        self.assertEqual(self.sites(), conform.DETERMINISM_REQUIRED)


class IceTests(unittest.TestCase):
    def sample(self, foundations=("15", "20", "15")):
        return {"count": len(foundations), "gatheringState": "complete", "sdpShape": 5,
                "candidates": [
                    {"foundation": foundation, "priority": 100 + index,
                     "protocol": "udp", "type": "host", "component": "rtp",
                     "addressClass": "mdns", "portClass": "ephemeral",
                     "relatedPort": None, "tcpType": None}
                    for index, foundation in enumerate(foundations)]}

    def test_session_rename_preserves_groups_without_mutating_input(self):
        original = self.sample()
        before = copy.deepcopy(original)
        self.assertEqual(conform.normalise_ice(original),
                         conform.normalise_ice(self.sample(("200", "300", "200"))))
        self.assertEqual(original, before)

    def test_split_and_merged_groups_still_differ(self):
        for foundations in (("15", "15", "15"), ("15", "20", "30")):
            self.assertNotEqual(conform.normalise_ice(self.sample()),
                                conform.normalise_ice(self.sample(foundations)))

    def test_invalid_foundations_are_rejected(self):
        for value in (None, "", "-1", "01", "4294967296", 15, "abc"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                conform.normalise_ice(self.sample((value,)))
        sample = self.sample()
        del sample["candidates"][0]["foundation"]
        with self.assertRaises(ValueError):
            conform.normalise_ice(sample)

    def test_other_candidate_fields_and_order_still_differ(self):
        expected = conform.normalise_ice(self.sample())
        for field in self.sample()["candidates"][0]:
            if field == "foundation":
                continue
            sample = self.sample()
            sample["candidates"][0][field] = "changed"
            self.assertNotEqual(expected, conform.normalise_ice(sample), field)
        sample = self.sample()
        sample["candidates"].reverse()
        self.assertNotEqual(expected, conform.normalise_ice(sample))

    def test_top_level_state_and_count_still_differ(self):
        expected = conform.normalise_ice(self.sample())
        for field in ("count", "gatheringState", "sdpShape"):
            sample = self.sample()
            sample[field] = "changed"
            self.assertNotEqual(expected, conform.normalise_ice(sample), field)

    def test_empty_candidates_and_uint32_boundaries(self):
        self.assertEqual(conform.normalise_ice(self.sample(())), self.sample(()))
        self.assertEqual(conform.normalise_ice(self.sample(("0", "4294967295"))),
                         conform.normalise_ice(self.sample(("10", "20"))))


if __name__ == "__main__":
    unittest.main()
