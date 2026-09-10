"""Measured display derivation and schema checks; no browser emulation."""

import contextlib
import copy
import io
import json
import math
import pathlib
import unittest

import jsonschema
import to_profile


ROOT = pathlib.Path(__file__).resolve().parents[2]
SCHEMA = json.loads((ROOT / "config/profile.schema.json").read_text())


def reference(name):
    return json.loads((ROOT / "resources/fingerprints/raw" / name).read_text())


class ScreenDisplayTests(unittest.TestCase):
    def setUp(self):
        self.i3 = reference("unlabelled-20260910T140818Z.json")
        self.mac = reference("m4-max-chrome-20260908T163229Z.json")

    def derive(self, capture):
        return to_profile.derive_screen_displays(capture)

    def test_windows_partial_origins_are_not_invented(self):
        first, second = self.derive(self.i3)
        self.assertEqual([first["label"], second["label"]], ["VA270A Series", "VZ229"])
        self.assertTrue(first["is_primary"])
        self.assertFalse(second["is_primary"])
        self.assertEqual([first["left"], second["left"]], [0, 1920])
        self.assertNotIn("avail_left", first)
        self.assertNotIn("avail_top", first)
        self.assertEqual((second["avail_left"], second["avail_top"]), (1920, 0))
        self.assertEqual((second["width"], second["avail_height"]), (1920, 1032))

    def test_mac_measured_dip_workarea_and_metadata(self):
        screen, = self.derive(self.mac)
        self.assertEqual((screen["width"], screen["height"]), (1728, 1117))
        self.assertEqual((screen["avail_top"], screen["avail_height"]), (33, 998))
        self.assertEqual(screen["height"] - screen["avail_top"] - screen["avail_height"], 86)
        self.assertEqual(screen["device_pixel_ratio"], 2)
        self.assertEqual(screen["color_depth"], 30)
        self.assertTrue(screen["is_internal"])
        self.assertEqual(screen["label"], "Built-in Retina Display")

    def test_derivation_preserves_inputs_and_gl_fields(self):
        before = copy.deepcopy(self.i3)
        with contextlib.redirect_stderr(io.StringIO()) as warnings:
            result = to_profile.build(self.i3)
        self.assertEqual(self.i3, before)
        self.assertIn("remain inherited", warnings.getvalue())
        self.assertIn("displays", result["screen"])
        self.assertEqual(result["gl_limits"], to_profile.derive_gl_limits(
            to_profile.probe(self.i3, "webgl1"), to_profile.probe(self.i3, "webgl2")))
        jsonschema.Draft202012Validator(SCHEMA).validate(result)

    def test_no_details_keeps_legacy_profile(self):
        self.i3["probes"].pop("screen.details")
        self.assertIsNone(self.derive(self.i3))
        with contextlib.redirect_stderr(io.StringIO()):
            self.assertNotIn("displays", to_profile.build(self.i3)["screen"])

    def test_ambiguous_current_display_does_not_spread_workarea_origin(self):
        detail = self.i3["probes"]["screen.details"]["value"]
        detail.pop("currentIsPrimary")
        detail["screens"][1]["left"] = 0
        self.i3["probes"]["screen.geometry"]["value"]["availLeft"] = 0
        self.assertTrue(all("avail_left" not in d and "avail_top" not in d
                            for d in self.derive(self.i3)))

    def test_explicit_other_display_workarea_is_retained(self):
        self.i3["probes"]["screen.details"]["value"]["screens"][0].update(
            availLeft=0, availTop=48)
        first, second = self.derive(self.i3)
        self.assertEqual(first["avail_top"], 48)
        self.assertEqual(second["avail_top"], 0)

    def test_conflicting_explicit_current_origin_rejected(self):
        self.mac["probes"]["screen.details"]["value"]["screens"][0]["availTop"] = 0
        with self.assertRaisesRegex(ValueError, "origins disagree"):
            self.derive(self.mac)

    def test_invalid_geometry_and_metadata_rejected(self):
        cases = [("width", 0), ("width", "1920"), ("availHeight", 99999),
                 ("devicePixelRatio", float("nan")), ("devicePixelRatio", 0),
                 ("devicePixelRatio", True), ("colorDepth", 16),
                 ("isPrimary", 1), ("isInternal", "true"), ("availTop", -1)]
        for key, value in cases:
            capture = copy.deepcopy(self.i3)
            capture["probes"]["screen.details"]["value"]["screens"][0][key] = value
            with self.subTest(key=key, value=value), self.assertRaises(ValueError):
                self.derive(capture)

    def test_count_and_primary_contradictions_rejected(self):
        for change in ("count", "primary", "extended"):
            capture = copy.deepcopy(self.i3)
            detail = capture["probes"]["screen.details"]["value"]
            if change == "count":
                detail["screenCount"] = 1
            elif change == "primary":
                detail["screens"][1]["isPrimary"] = True
            else:
                capture["probes"]["screen.geometry"]["value"]["isExtended"] = False
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.derive(capture)

    def test_schema_requires_exactly_one_primary_and_all_measured_fields(self):
        valid = {"screen": {"displays": self.derive(self.i3)}}
        validator = jsonschema.Draft202012Validator(SCHEMA)
        validator.validate(valid)
        for primary in (False, True):
            bad = copy.deepcopy(valid)
            for display in bad["screen"]["displays"]:
                display["is_primary"] = primary
            self.assertTrue(list(validator.iter_errors(bad)))
        bad = copy.deepcopy(valid)
        del bad["screen"]["displays"][0]["label"]
        self.assertTrue(list(validator.iter_errors(bad)))

    def test_native_unit_contract_for_measured_and_fractional_desktop_scales(self):
        # The pinned native helper inverse-scales sizes/insets and keeps origins.
        # This verifies expected pixel inputs, not native execution of that code.
        self.assertEqual(math.floor(1728 * 2), 3456)
        self.assertEqual(math.floor(1117 * 2), 2234)
        self.assertEqual((math.floor(33 * 2), math.floor(86 * 2)), (66, 172))
        for ratio in (1, 1.25, 1.5, 1.75, 2, 3):
            for dip in (0, 1, 33, 48, 86, 998, 1117, 1728, 1920):
                self.assertEqual(math.ceil(math.floor(dip * ratio) / ratio), dip)


if __name__ == "__main__":
    unittest.main()
