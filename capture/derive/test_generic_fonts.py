"""Generic font preferences need native identity and full measured controls."""
import copy
import json
from pathlib import Path
import unittest

import jsonschema
import to_profile

ROOT = Path(__file__).resolve().parents[2]


class GenericFontTests(unittest.TestCase):
    def setUp(self):
        self.capture = {"probes": {
            "fonts.detected": {"ok": True, "value": {"detected": ["Times"]}},
            "fonts.metrics": {"ok": True, "value": {
                "Times": {"width": 100, "actualBoundingBoxRight": 99, "__resolved": "40px Times"},
                "serif": {"width": 100, "actualBoundingBoxRight": 99, "__resolved": "40px serif"}
            }}
        }}

    def test_full_control_matches_without_changing_capture(self):
        before = copy.deepcopy(self.capture)
        self.assertEqual(to_profile.derive_generic_fonts(self.capture, "macOS"), {"serif": "Times"})
        self.assertEqual(self.capture, before)

    def test_equal_width_is_insufficient(self):
        self.capture["probes"]["fonts.metrics"]["value"]["Times"]["actualBoundingBoxRight"] = 98
        self.assertEqual(to_profile.derive_generic_fonts(self.capture, "macOS"), {})

    def test_missing_face_and_unknown_platform_remain_inherited(self):
        self.assertEqual(to_profile.derive_generic_fonts(self.capture, "unknown"), {})
        self.capture["probes"]["fonts.detected"]["value"]["detected"] = []
        self.assertEqual(to_profile.derive_generic_fonts(self.capture, "macOS"), {})

    def test_reference_maps_validate_and_do_not_invent_math_or_standard(self):
        schema = json.loads((ROOT / "config/profile.schema.json").read_text())
        for filename, platform, serif, sans, mono in [
            ("m4-max-chrome-20260908T163229Z.json", "macOS", "Times", "Helvetica", "Menlo"),
            ("unlabelled-20260910T140818Z.json", "Windows", "Times New Roman", "Arial", "Consolas")
        ]:
            capture = json.loads((ROOT / "resources/fingerprints/raw" / filename).read_text())
            families = to_profile.derive_generic_fonts(capture, platform)
            self.assertEqual([families[k] for k in ["serif", "sans-serif", "monospace"]],
                             [serif, sans, mono])
            self.assertNotIn("math", families)
            self.assertNotIn("standard", families)
            jsonschema.Draft202012Validator(schema).validate({"fonts": {"generic_family_map": families}})


if __name__ == "__main__":
    unittest.main()
