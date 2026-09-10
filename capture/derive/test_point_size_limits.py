"""Lossless point-range derivation and unsupported-range controls."""

import contextlib
import copy
import io
import json
from pathlib import Path
import unittest

import to_profile


def gl(pair):
    return {"parameters": {"ALIASED_POINT_SIZE_RANGE": pair}}


class PointSizeTests(unittest.TestCase):
    def test_integral_endpoints_are_preserved_for_both_references(self):
        root = Path(__file__).resolve().parents[2]
        for filename, expected in (("unlabelled-20260910T140818Z.json", 1024),
                                   ("m4-max-chrome-20260908T163229Z.json", 511)):
            capture = json.loads((root / "resources/fingerprints/raw" / filename).read_text())
            limits = to_profile.derive_gl_limits(to_profile.probe(capture, "webgl1"),
                                                 to_profile.probe(capture, "webgl2"))
            self.assertEqual(limits["ALIASED_POINT_SIZE_RANGE_MIN"], 1)
            self.assertEqual(limits["ALIASED_POINT_SIZE_RANGE_MAX"], expected)

    def test_integral_floats_convert_without_losing_information(self):
        self.assertEqual(to_profile.derive_gl_limits(gl([1.0, 511.0]), {}),
                         {"ALIASED_POINT_SIZE_RANGE_MIN": 1, "ALIASED_POINT_SIZE_RANGE_MAX": 511})

    def test_fractional_range_stays_unrepresented_and_input_unchanged(self):
        source = gl([1.5, 511.75])
        original = copy.deepcopy(source)
        with contextlib.redirect_stderr(io.StringIO()) as warnings:
            self.assertEqual(to_profile.derive_gl_limits(source, {}), {})
        self.assertIn("stays inherited", warnings.getvalue())
        self.assertEqual(source, original)

    def test_invalid_or_conflicting_ranges_are_rejected(self):
        for value in ([2, 1], [0, 511], [True, 511], [1, float("nan")], [1], "1,511"):
            with self.subTest(value=value), self.assertRaises(ValueError):
                to_profile.derive_gl_limits(gl(value), {})
        with self.assertRaisesRegex(ValueError, "disagree"):
            to_profile.derive_gl_limits(gl([1, 511]), gl([1, 1024]))

    def test_absent_point_range_does_not_add_constraints(self):
        self.assertEqual(to_profile.derive_gl_limits({"parameters": {"MAX_TEXTURE_SIZE": 8192}}, {}),
                         {"MAX_TEXTURE_SIZE": 8192})


if __name__ == "__main__":
    unittest.main()
