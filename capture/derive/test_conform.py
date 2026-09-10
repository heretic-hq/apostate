"""Regression checks for the V3 comparison gate."""

import contextlib
import copy
import io
import json
import pathlib
import tempfile
import unittest
from unittest.mock import patch

import conform


def capture(version="152.0.7977.83"):
    probes = {"example": {"ok": True, "value": 7}}
    if version is not None:
        probes["navigator.userAgentData"] = {
            "ok": True, "value": {"high": {"uaFullVersion": version}}}
    return {
        "context": {"collector_sha256": "a" * 64, "ua": "Chrome", "label": "test"},
        "probes": probes,
    }


class ConformTests(unittest.TestCase):
    def compare(self, reference, subject):
        with tempfile.TemporaryDirectory() as directory:
            paths = [pathlib.Path(directory) / name for name in ("ref.json", "sub.json")]
            for path, data in zip(paths, (reference, subject)):
                path.write_text(json.dumps(data))
            output = io.StringIO()
            with patch("sys.argv", ["conform.py", *map(str, paths)]), contextlib.redirect_stdout(output):
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

    def test_collector_mismatch_is_refused(self):
        subject = capture()
        subject["context"]["collector_sha256"] = "b" * 64
        status, output = self.compare(capture(), subject)
        self.assertEqual(status, 2)
        self.assertIn("REFUSED", output)

    def test_failed_subject_probe_stays_error(self):
        subject = copy.deepcopy(capture())
        subject["probes"]["example"] = {"ok": False, "error": "permission denied"}
        status, output = self.compare(capture(), subject)
        self.assertEqual(status, 1)
        self.assertIn("ERROR example", output)


if __name__ == "__main__":
    unittest.main()
