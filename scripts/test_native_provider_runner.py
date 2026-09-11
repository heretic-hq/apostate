#!/usr/bin/env python3
"""Parse-only native gate tests. No executable, X server or container is started."""
import copy
import importlib.util
import os
from pathlib import Path
import unittest
from unittest import mock

spec = importlib.util.spec_from_file_location("native_gates", Path(__file__).with_name("run-native-provider-tests.py"))
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)

class GateTests(unittest.TestCase):
    def ozone(self):
        return {"per_iteration_data": [{name: [{"status": "SUCCESS"}] for name in runner.OZONE_CASES}]}
    def angle(self, config):
        return {"version": 3, "interrupted": False, "num_failures_by_type": {"PASS": 5},
            "tests": {"ParallelShaderCompileTest." + name + "/" + config:
                {"actual": "PASS", "expected": "PASS", "times": [0.001]} for name in runner.ANGLE_CASES}}
    def test_exact_six_ozone_cases(self):
        self.assertEqual(runner.validate_ozone(self.ozone())["executed"], 6)
    def test_missing_skip_retry_and_extra_ozone_cases_fail(self):
        for defect in ["missing", "skip", "retry", "extra"]:
            data = self.ozone(); cases = data["per_iteration_data"][0]; first = next(iter(cases))
            if defect == "missing": cases.pop(first)
            if defect == "skip": cases[first][0]["status"] = "SKIPPED"
            if defect == "retry": cases[first].append({"status": "SUCCESS"})
            if defect == "extra": cases["Unexpected.Test"] = [{"status": "SUCCESS"}]
            with self.assertRaises(ValueError): runner.validate_ozone(data)
    def test_both_exact_default_angle_configs(self):
        for config in runner.CONFIGS:
            self.assertEqual(runner.validate_angle(self.angle(config), config)["executed"], 5)
    def test_explicit_parallel_override_cannot_count(self):
        config = runner.CONFIGS[0]; data = self.angle(config)
        name = next(iter(data["tests"]))
        data["tests"][name + "_EnableParallelCompileAndLink"] = data["tests"].pop(name)
        with self.assertRaises(ValueError): runner.validate_angle(data, config)
    def test_skip_flake_missing_time_interruption_and_count_fail(self):
        config = runner.CONFIGS[0]
        for defect in ["skip", "flake", "no_time", "interrupted", "wrong_count", "missing"]:
            data = self.angle(config); name = next(iter(data["tests"])); case = data["tests"][name]
            if defect == "skip": case["actual"] = case["expected"] = "SKIP"
            if defect == "flake": case["actual"] = "FAIL PASS"
            if defect == "no_time": case["times"] = []
            if defect == "interrupted": data["interrupted"] = True
            if defect == "wrong_count": data["num_failures_by_type"] = {"PASS": 4, "SKIP": 1}
            if defect == "missing": data["tests"].pop(name)
            with self.assertRaises(ValueError): runner.validate_angle(data, config)
    def test_environment_discards_provider_overrides(self):
        with mock.patch.dict(os.environ, {"ANGLE_FEATURE_OVERRIDES_ENABLED": "enableParallelCompileAndLink",
                "ANGLE_DEFAULT_PLATFORM": "vulkan", "DISPLAY": ":0", "VK_INSTANCE_LAYERS": "inherited",
                "GTEST_FILTER": "wrong", "EGL_PLATFORM": "surfaceless"}, clear=True):
            env, receipt = runner.clean_environment(Path("/build"))
        self.assertEqual(env, {"VK_DRIVER_FILES": "/build/vk_swiftshader_icd.json",
                               "VK_ICD_FILENAMES": "/build/vk_swiftshader_icd.json"})
        self.assertIn("ANGLE_FEATURE_OVERRIDES_ENABLED", receipt["cleared_environment_names"])
    def test_exact_commands_and_native_result_formats(self):
        plans = runner.commands(Path("/build"), Path("/out"), "/usr/bin/xvfb-run")
        self.assertEqual(len(plans), 3)
        self.assertTrue(any(arg.startswith("--test-launcher-summary-output=") for arg in plans[0][1]))
        for plan, config in zip(plans[1:], runner.CONFIGS):
            command = plan[1]
            self.assertIn("/build/apostate_parallel_shader_tests", command)
            self.assertIn("--use-config=" + config, command)
            self.assertIn("--gtest_filter=ParallelShaderCompileTest.*/" + config, command)
            self.assertIn("--flaky-retries=0", command)
            self.assertIn("--batch-size=1", command)
            self.assertTrue(any(arg.startswith("--results-file=") for arg in command))
            self.assertFalse(any(arg.startswith(("--use-gl", "--headless", "--gtest_output")) for arg in command))

if __name__ == "__main__": unittest.main()
