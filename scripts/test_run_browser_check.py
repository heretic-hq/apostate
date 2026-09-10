"""Diagnostic runner protocol tests using a synthetic process, never Chromium."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request


SCRIPT = Path(__file__).with_name("run-browser-check.py")
spec = importlib.util.spec_from_file_location("browser_check", SCRIPT)
browser_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser_check)

FAKE_CHILD = '''#!/usr/bin/env python3
import json,sys,time,urllib.request,urllib.parse
from pathlib import Path
url=sys.argv[-1]
parts=urllib.parse.urlsplit(url)
base=urllib.parse.urlunsplit((parts.scheme,parts.netloc,"","",""))
query=urllib.parse.urlencode({"token":urllib.parse.parse_qs(parts.query)["token"][0]})
parameters=json.load(urllib.request.urlopen(base+"/parameters?"+query))
result={"passed":True,"synthetic_process":True,"parameters":parameters,
        "has_profile":any(a.startswith("--apostate-profile=") for a in sys.argv)}
userdata=Path(next(a.split("=",1)[1] for a in sys.argv if a.startswith("--user-data-dir=")))
prefs=userdata/"Default/Preferences"
result["preferences"]=json.loads(prefs.read_text()) if prefs.exists() else None
request=urllib.request.Request(base+"/result?"+query,data=json.dumps(result).encode(),
    headers={"Content-Type":"application/json","Origin":base})
urllib.request.urlopen(request).read()
time.sleep(60)
'''


class RunnerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.child = self.root / "synthetic-browser"
        self.child.write_text(FAKE_CHILD)
        self.child.chmod(0o700)
        self.fixture = self.root / "fixture.html"
        self.fixture.write_text("<!doctype html><title>synthetic protocol fixture</title>")
        self.out = self.root / "receipts"

    def invoke(self, *extra):
        return subprocess.run([sys.executable, str(SCRIPT), "--browser", str(self.child),
                               "--fixture", str(self.fixture), "--out", str(self.out),
                               *extra], capture_output=True, text=True, timeout=15)

    def test_synthetic_result_parameters_receipts_and_owned_cleanup(self):
        profile = self.root / "profile.json"
        profile.write_text('{"gl_limits":{"MAX_UNIFORM_BLOCK_SIZE":65536}}')
        params = self.root / "parameters.json"
        params.write_text('{"additional":"test"}')
        sentinel = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"],
                                    start_new_session=True)
        try:
            run = self.invoke("--profile", str(profile), "--parameters", str(params),
                              "--angle-backend", "gl", "--timeout", "5")
            self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
            result = json.loads((self.out / "result.json").read_text())
            self.assertTrue(result["synthetic_process"])
            self.assertTrue(result["has_profile"])
            self.assertEqual(result["parameters"]["expected_limits"]["MAX_UNIFORM_BLOCK_SIZE"], 65536)
            self.assertEqual(result["parameters"]["additional"], "test")
            receipt = json.loads((self.out / "receipt.json").read_text())
            self.assertEqual(receipt["status"], "completed")
            self.assertTrue(receipt["userdata_removed"])
            self.assertFalse(Path(receipt["user_data_dir"]).exists())
            launch = json.loads((self.out / "launch.json").read_text())
            self.assertIn("--use-angle=gl", launch["command"])
            self.assertFalse(any("remote-debugging" in arg for arg in launch["command"]))
            self.assertTrue((self.out / "browser.log").exists())
            self.assertIsNone(sentinel.poll(), "unrelated process was killed")
        finally:
            sentinel.terminate()
            sentinel.wait(timeout=3)

    def test_timeout_cleans_temporary_userdata(self):
        self.child.write_text("#!/usr/bin/env python3\nimport time\ntime.sleep(60)\n")
        result = self.invoke("--timeout", "0.2")
        self.assertEqual(result.returncode, 2)
        receipt = json.loads((self.out / "receipt.json").read_text())
        self.assertEqual(receipt["status"], "failed")
        self.assertIn("TimeoutError", receipt["error"])
        self.assertTrue(receipt["userdata_removed"])

    def test_audio_fft_control_preserves_gpu_option(self):
        run = self.invoke("--angle-backend", "default", "--enable-gpu",
                          "--disable-web-audio-rust-fft", "--timeout", "5")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        launch = json.loads((self.out / "launch.json").read_text())
        self.assertIn("--enable-gpu", launch["command"])
        self.assertIn("--disable-blink-features=WebAudioRustFft", launch["command"])
        configuration = json.loads((self.out / "configuration.json").read_text())
        self.assertTrue(configuration["enable_gpu"])
        self.assertTrue(configuration["disable_web_audio_rust_fft"])
        self.assertFalse(any("remote-debugging" in arg for arg in launch["command"]))

    def test_display_permissions_are_limited_to_diagnostic_origin(self):
        run = self.invoke("--grant-display-controls", "--timeout", "5")
        self.assertEqual(run.returncode, 0, run.stdout + run.stderr)
        result = json.loads((self.out / "result.json").read_text())
        exceptions = result["preferences"]["profile"]["content_settings"]["exceptions"]
        self.assertEqual(set(exceptions), {"window_placement", "popups", "automatic_fullscreen"})
        for rules in exceptions.values():
            self.assertEqual(len(rules), 1)
            pattern, value = next(iter(rules.items()))
            self.assertRegex(pattern, r"^http://127\.0\.0\.1:[0-9]+,\*$")
            self.assertEqual(value, {"setting": 1})
        receipt = json.loads((self.out / "receipt.json").read_text())
        self.assertTrue(receipt["userdata_removed"])

    def test_existing_output_refused_without_modification(self):
        self.out.mkdir()
        saved = self.out / "saved"
        saved.write_text("keep")
        result = self.invoke()
        self.assertEqual(result.returncode, 2)
        self.assertEqual(list(self.out.iterdir()), [saved])
        self.assertEqual(saved.read_text(), "keep")

    def test_invalid_profile_never_launches_process(self):
        profile = self.root / "profile.json"
        profile.write_text('{"not_a_profile_field":true}')
        result = self.invoke("--profile", str(profile))
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.out / "launch.json").exists())
        self.assertIn("profile failed validation", (self.out / "receipt.json").read_text())
        self.assertTrue((self.out / "configuration.json").exists())

    def test_nonfinite_parameters_refused(self):
        params = self.root / "parameters.json"
        params.write_text('{"bad":NaN}')
        result = self.invoke("--parameters", str(params))
        self.assertEqual(result.returncode, 2)
        self.assertFalse((self.out / "launch.json").exists())

    def test_backend_arguments_match_v3(self):
        self.assertEqual(browser_check.backend_args("default"), [])
        self.assertEqual(browser_check.backend_args("swiftshader"),
                         ["--use-gl=angle", "--use-angle=swiftshader", "--enable-unsafe-swiftshader"])
        for backend in browser_check.BACKENDS[2:]:
            self.assertEqual(browser_check.backend_args(backend), ["--use-gl=angle", f"--use-angle={backend}"])

    def test_server_rejects_unauthorized_invalid_and_duplicate_results(self):
        self.out.mkdir()
        server = browser_check.ResultServer(b"test", {}, "test-token", self.out)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        self.addCleanup(server.server_close)
        self.addCleanup(server.shutdown)

        def post(token, body):
            request = urllib.request.Request(server.origin + "/result?token=" + token, data=body,
                headers={"Origin": server.origin, "Content-Type": "application/json"})
            try:
                with urllib.request.urlopen(request) as response:
                    return response.status
            except urllib.error.HTTPError as error:
                code = error.code
                error.close()
                return code

        self.assertEqual(post("wrong", b'{}'), 403)
        self.assertEqual(post("test-token", b'{"bad":NaN}'), 400)
        self.assertEqual(post("test-token", b'[]'), 400)
        self.assertEqual(post("test-token", b'{"passed":true}'), 200)
        original = (self.out / "result.json").read_bytes()
        self.assertEqual(post("test-token", b'{"passed":false}'), 409)
        self.assertEqual((self.out / "result.json").read_bytes(), original)


if __name__ == "__main__":
    unittest.main()
