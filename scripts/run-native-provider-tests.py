#!/usr/bin/env python3
"""Execute exact native gates; test summaries must show every case ran once and passed.

Use the shell entry point to enter the existing pinned build container. This
module never builds targets, installs dependencies or selects a physical GPU.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import time

PINS = {".": "79460ebecaa5625e57a5fb679a735659e73dc687",
        "third_party/angle": "7df613367a1d4ca9aea9ece344d4580d32d132a9",
        "third_party/swiftshader": "5b0479bd2d15058aaa9eb490e364f920ff824a8c"}
OZONE_CASES = {
    "HeadlessWindowGeometryTest.FullscreenUsesDisplayBoundsAndRestores",
    "HeadlessWindowGeometryTest.MaximizeUsesWorkAreaBeforeFullscreen",
    "HeadlessWindowCrashTest.DestroyViaObserverInSetFullscreen",
    "HeadlessWindowCrashTest.DestroyViaObserverInMaximize",
    "HeadlessWindowCrashTest.DestroyViaObserverInMinimize",
    "HeadlessWindowCrashTest.DestroyViaObserverInRestore",
}
ANGLE_CASES = {"Basic", "DefaultSupportOnLinuxSwiftShader", "LinkAndDrawManyPrograms",
               "LinkProgramAndRecompileShader", "DestroyContextWhileLinkIsInProgress"}
CONFIGS = ("ES2_Vulkan_SwiftShader", "ES3_Vulkan_SwiftShader")


def sha256(path):
    result = hashlib.sha256()
    with path.open("rb") as source:
        for data in iter(lambda: source.read(1024 * 1024), b""):
            result.update(data)
    return result.hexdigest()


def require(condition, message):
    if not condition:
        raise ValueError(message)


def validate_ozone(data):
    require(isinstance(data, dict), "Ozone summary is not an object")
    iterations = data.get("per_iteration_data")
    require(isinstance(iterations, list) and len(iterations) == 1,
            "Ozone must report exactly one iteration")
    cases = iterations[0]
    require(set(cases) == OZONE_CASES, "Ozone executed case names differ from the six required tests")
    for name, attempts in cases.items():
        require(isinstance(attempts, list) and len(attempts) == 1,
                "Ozone must execute each case once without retry: " + name)
        require(attempts[0].get("status") == "SUCCESS", "Ozone did not pass: " + name)
    return {"executed": 6, "passed": 6, "cases": sorted(cases)}


def validate_angle(data, config):
    require(isinstance(data, dict), "ANGLE summary is not an object")
    require(config in CONFIGS, "Unknown ANGLE configuration")
    require(data.get("version") == 3 and data.get("interrupted") is False,
            "ANGLE result is missing, interrupted or not version3")
    expected = {"ParallelShaderCompileTest." + name + "/" + config for name in ANGLE_CASES}
    cases = data.get("tests", {})
    require(set(cases) == expected, "ANGLE must execute exactly five default-policy cases for " + config)
    require(data.get("num_failures_by_type") == {"PASS": 5}, "ANGLE pass/skip counts differ")
    for name, result in cases.items():
        require(result.get("actual") == "PASS" and result.get("expected") == "PASS" and
                not result.get("is_flaky") and not result.get("is_unexpected"),
                "ANGLE skipped, retried or failed: " + name)
        times = result.get("times")
        require(isinstance(times, list) and len(times) == 1 and
                isinstance(times[0], (int, float)) and times[0] >= 0,
                "ANGLE did not record one execution: " + name)
    return {"executed": 5, "passed": 5, "cases": sorted(cases)}


def clean_environment(build):
    environment = dict(os.environ)
    cleared = []
    exact = {"DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY", "XDG_RUNTIME_DIR", "EGL_PLATFORM",
             "LD_PRELOAD", "LD_AUDIT", "LD_LIBRARY_PATH"}
    for name in list(environment):
        if name in exact or name.startswith(("ANGLE_", "GTEST_", "VK_", "MESA_", "LIBGL_")):
            cleared.append(name)
            environment.pop(name)
    icd = str(build / "vk_swiftshader_icd.json")
    environment.update(VK_DRIVER_FILES=icd, VK_ICD_FILENAMES=icd)
    return environment, {"cleared_environment_names": sorted(cleared),
                         "VK_DRIVER_FILES": icd, "VK_ICD_FILENAMES": icd}


def commands(build, out, xvfb):
    ozone = [str(build / "ozone_unittests"), "--ozone-platform=headless",
             "--gtest_filter=" + ":".join(sorted(OZONE_CASES)),
             "--test-launcher-jobs=1", "--test-launcher-retry-limit=0",
             "--test-launcher-batch-limit=1",
             "--test-launcher-summary-output=" + str(out / "ozone/results.json")]
    result = [("ozone", ozone, 180, validate_ozone)]
    for config in CONFIGS:
        name = "angle-" + config
        command = [xvfb, "-a", "-s", "-screen 0 1280x720x24 -nolisten tcp",
            str(build / "apostate_parallel_shader_tests"), "--use-config=" + config,
            "--gtest_filter=ParallelShaderCompileTest.*/" + config,
            "--bot-mode", "--max-processes=1", "--batch-size=1",
            "--test-timeout=60", "--batch-timeout=90", "--flaky-retries=0",
            "--results-file=" + str(out / name / "results.json")]
        result.append((name, command, 480, lambda data, config=config: validate_angle(data, config)))
    return result


def stop_owned_group(process):
    for sig in (signal.SIGTERM, signal.SIGKILL):
        try:
            os.killpg(process.pid, sig)
        except ProcessLookupError:
            break
        if sig == signal.SIGTERM:
            try:
                process.wait(timeout=3)
            except subprocess.TimeoutExpired:
                pass
    try:
        process.wait(timeout=3)
    except subprocess.TimeoutExpired:
        pass


def run_gate(name, command, timeout, validator, cwd, out, environment):
    directory = out / name
    directory.mkdir()
    receipt = {"diagnostic_only": True, "corpus_admission": False, "command": command,
               "cwd": str(cwd), "timeout_seconds": timeout, "started_unix": time.time(),
               "passed": False}
    process = None
    try:
        with (directory / "test.log").open("w") as log:
            process = subprocess.Popen(command, cwd=cwd, env=environment,
                stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
            receipt["pid"] = process.pid
            receipt["process_group"] = process.pid
            receipt["returncode"] = process.wait(timeout=timeout)
        require(receipt["returncode"] == 0, "Test process returned nonzero")
        receipt["results"] = validator(json.loads((directory / "results.json").read_text()))
        receipt["passed"] = True
    except (OSError, ValueError, TypeError, KeyError, subprocess.TimeoutExpired) as error:
        receipt["error"] = str(error)
    finally:
        if process:
            stop_owned_group(process)
        receipt["finished_unix"] = time.time()
        (directory / "receipt.json").write_text(json.dumps(receipt, indent=2) + "\n")
    return receipt


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--build", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--image-id", required=True)
    args = parser.parse_args()
    require(re.fullmatch(r"sha256:[0-9a-f]{64}", args.image_id), "Pinned container image ID is required")
    require(os.environ.get("APOSTATE_BUILD_IMAGE_ID") == args.image_id, "Use the build-container entry point")
    for device in ["/dev/dri", "/dev/kfd", "/dev/dxg", "/dev/nvidiactl", "/dev/nvidia0", "/dev/vfio"]:
        require(not Path(device).exists(), "Physical GPU device path is exposed: " + device)
    source, build, out = args.source.resolve(), args.build.resolve(), args.out.absolute()
    require(build.is_relative_to(source / "out"), "Build output must belong to the supplied source")
    out.mkdir(parents=True, exist_ok=False)
    aggregate = {"diagnostic_only": True, "corpus_admission": False, "passed": False,
                 "image_id": args.image_id, "source": str(source), "build": str(build), "gates": []}
    try:
        for relative, expected in PINS.items():
            actual = subprocess.check_output(["git", "-C", str(source / relative), "rev-parse", "HEAD"], text=True).strip()
            require(actual == expected, "Source revision mismatch: " + relative)
        aggregate["source_pins"] = PINS
        icd = build / "vk_swiftshader_icd.json"
        provider = build / "libvk_swiftshader.so"
        library_path = json.loads(icd.read_text()).get("ICD", {}).get("library_path")
        require(isinstance(library_path, str) and (icd.parent / library_path).resolve() == provider.resolve(),
                "ICD does not select the built SwiftShader library")
        inputs = [build / "ozone_unittests", build / "apostate_parallel_shader_tests", icd, provider,
                  source / "third_party/angle/src/tests/angle_end2end_tests_expectations.txt"]
        for optional in [build / "libEGL.so", build / "libGLESv2.so"]:
            if optional.exists(): inputs.append(optional)
        aggregate["input_hashes"] = {str(path): sha256(path) for path in inputs}
        require(all(os.access(build / binary, os.X_OK) for binary in ["ozone_unittests", "apostate_parallel_shader_tests"]),
                "Built test executables are missing or not executable")
        xvfb = shutil.which("xvfb-run")
        require(xvfb is not None, "The pinned container must already contain xvfb-run")
        environment, receipt_env = clean_environment(build)
        aggregate["environment"] = receipt_env
        for name, command, timeout, validator in commands(build, out, xvfb):
            cwd = source / "third_party/angle" if name.startswith("angle-") else source
            aggregate["gates"].append(run_gate(name, command, timeout, validator, cwd, out, environment))
        aggregate["passed"] = len(aggregate["gates"]) == 3 and all(gate["passed"] for gate in aggregate["gates"])
        aggregate["expected_executed_total"] = 16
    except (OSError, ValueError, TypeError, KeyError, subprocess.CalledProcessError) as error:
        aggregate["preflight_error"] = str(error)
    finally:
        (out / "receipt.json").write_text(json.dumps(aggregate, indent=2) + "\n")
    print(json.dumps({"out": str(out), "passed": aggregate["passed"], "expected_tests": 16}))
    return 0 if aggregate["passed"] else 1

if __name__ == "__main__":
    raise SystemExit(main())
