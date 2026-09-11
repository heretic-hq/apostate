"""WebGPU adapter identity derivation from T0 captures."""

import copy
import json
from pathlib import Path
import unittest

import to_profile


def webgpu_capture():
    root = Path(__file__).resolve().parents[2]
    out = {}
    for filename in ("unlabelled-20260910T140818Z.json",
                     "m4-max-chrome-20260908T163229Z.json"):
        capture = json.loads((root / "resources/fingerprints/raw" / filename).read_text())
        out[filename] = to_profile.probe(capture, "webgpu")
    return out


class WebGPUDeriveTests(unittest.TestCase):
    def test_intel_adapter_preserves_features_info_and_limits(self):
        caps = webgpu_capture()
        derived = to_profile.derive_webgpu(caps["unlabelled-20260910T140818Z.json"])
        self.assertEqual(len(derived["features"]), 18)
        self.assertIn("shader-f16", derived["features"])
        self.assertNotIn("subgroups", derived["features"])
        self.assertEqual(derived["info"]["vendor"], "intel")
        self.assertEqual(derived["info"]["architecture"], "gen-9")
        self.assertEqual(derived["info"]["subgroup_min_size"], 16)
        self.assertEqual(derived["info"]["subgroup_max_size"], 16)
        self.assertEqual(derived["limits"]["maxBufferSize"], 2147483648)
        self.assertEqual(derived["limits"]["maxVertexAttributes"], 30)

    def test_apple_adapter_preserves_metal_identity(self):
        caps = webgpu_capture()
        derived = to_profile.derive_webgpu(caps["m4-max-chrome-20260908T163229Z.json"])
        self.assertEqual(len(derived["features"]), 23)
        self.assertIn("subgroups", derived["features"])
        self.assertIn("texture-compression-etc2", derived["features"])
        self.assertEqual(derived["info"]["vendor"], "apple")
        self.assertEqual(derived["info"]["architecture"], "metal-3")
        self.assertEqual(derived["info"]["subgroup_min_size"], 32)

    def test_absent_probe_derives_nothing(self):
        self.assertEqual(to_profile.derive_webgpu(None), {})
        self.assertEqual(to_profile.derive_webgpu({"adapters": {}}), {})

    def test_disagreeing_adapters_are_rejected(self):
        good = {"features": ["a"], "info": {"vendor": "v", "architecture": "a",
                                            "subgroupMinSize": 1, "subgroupMaxSize": 1},
                "limits": {"maxX": 1}}
        other = copy.deepcopy(good)
        other["limits"] = {"maxX": 2}
        with self.assertRaisesRegex(ValueError, "disagree"):
            to_profile.derive_webgpu({"adapters": {"high-performance": good,
                                                  "low-power": other}})

    def test_malformed_adapter_is_rejected_and_input_unchanged(self):
        base = {"features": ["a"], "info": {"vendor": "v", "architecture": "a",
                                            "subgroupMinSize": 1, "subgroupMaxSize": 1},
                "limits": {"maxX": 1}}
        cases = []
        bad = copy.deepcopy(base)
        bad["features"] = []
        cases.append(bad)
        bad = copy.deepcopy(base)
        bad["info"] = {"vendor": "", "architecture": "a",
                       "subgroupMinSize": 1, "subgroupMaxSize": 1}
        cases.append(bad)
        bad = copy.deepcopy(base)
        bad["limits"] = {"maxX": -1}
        cases.append(bad)
        bad = copy.deepcopy(base)
        bad["limits"] = {"maxX": True}
        cases.append(bad)
        for case in cases:
            original = copy.deepcopy(case)
            with self.subTest(case=case):
                with self.assertRaises(ValueError):
                    to_profile.derive_webgpu({"adapters": {"high-performance": case}})
                self.assertEqual(case, original)


if __name__ == "__main__":
    unittest.main()
