"""Focused HTTP tests for the opt-in font diagnostic delivery path."""
import hashlib
import http.client
import importlib.util
import json
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

SPEC = importlib.util.spec_from_file_location("capture_receiver", pathlib.Path(__file__).with_name("receive.py"))
receiver = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(receiver)


def diagnostic():
    return {"consent": {"accepted": True, "version": receiver.CONSENT_VERSION}, "label": "Windows i3",
            "result": {"diagnosticOnly": True, "corpusAdmission": False,
                       "diagnostic_only": True, "corpus_admission": False,
                       "page": "https://user:secret@example.test/check?token=private#fragment",
                       "userAgent": "test browser", "language": "en-US", "devicePixelRatio": 1,
                       "fontFacesBefore": {"size": 1, "status": "loaded", "truncated": False,
                                           "faces": [{"family": "Roboto", "weight": "400", "status": "loaded"}]},
                       "accessibleRules": {"fontFaces": [{
                           "stylesheet": "https://u:p@example.test/style.css?private=yes#anchor",
                           "descriptors": {"font-family": "Roboto"},
                           "sources": [{"kind": "url", "value": "https://u:p@example.test/font.woff2?key=secret#x"},
                                       {"kind": "url", "value": "data:font/woff2;base64,AAAA"},
                                       {"kind": "url", "value": "chrome-extension://private-id/font.woff2"},
                                       {"kind": "local", "value": "Roboto"}]}],
                           "inaccessible": 0, "visitedRules": 1, "truncated": False},
                       "metrics": [{"family": "Roboto", "size": 24, "sample": "HxpgÅ", "metrics": {"width": 60}}],
                       "isolatedLeading": [], "passed": True}}


class FontContextServerTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = pathlib.Path(self.tmp.name)
        self.captures = self.root / "captures"
        self.captures.mkdir()
        self.diagnostics = self.root / "diagnostics"
        handler = type("TestHandler", (receiver.Handler,), {"out_dir": self.captures})
        self.server = receiver.TLSCapableServer(("127.0.0.1", 0), handler)
        self.server.diagnostics_dir = self.diagnostics
        self.server.diagnostic_lock = threading.Lock()
        self.server.diagnostic_tokens = {}
        self.server.exit_after_capture = False
        self.origin = "http://127.0.0.1:" + str(self.server.server_port)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(3)
        self.tmp.cleanup()

    def request(self, method, path, payload=None, headers=None, raw=None):
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body=raw if raw is not None else (json.dumps(payload).encode() if payload is not None else None), headers=headers or {})
            response = connection.getresponse()
            return response.status, dict(response.getheaders()), response.read()
        finally:
            connection.close()

    def token(self):
        status, headers, body = self.request("GET", receiver.FONT_ROUTE)
        self.assertEqual(status, 200)
        return re.search(rb'content="([A-Za-z0-9_-]+)"', body.split(b'name="diagnostic-token"', 1)[1]).group(1).decode()

    def submit(self, data=None, token=None, **kwargs):
        headers = {"Origin": self.origin, "Content-Type": "application/json", "X-Diagnostic-Token": token or self.token()}
        headers.update(kwargs.pop("headers", {}))
        return self.request("POST", receiver.FONT_ROUTE + "/submit", diagnostic() if data is None else data, headers, **kwargs)

    def test_delivery_is_opt_in_and_does_not_change_collector(self):
        self.server.diagnostics_dir = None
        self.assertEqual(self.request("GET", receiver.FONT_ROUTE)[0], 404)
        self.assertEqual(self.request("POST", receiver.FONT_ROUTE + "/submit", diagnostic())[0], 404)
        status, headers, body = self.request("GET", "/collector.js")
        self.assertEqual(status, 200)
        self.assertEqual(hashlib.sha256(body).hexdigest(), "a19ad58d618bd22ddc3a7a35fee021be31e2915e59500d002b56b56a331bf3a5")
        self.assertEqual(headers["Accept-CH"], receiver.ACCEPT_CH)
        self.assertEqual(headers["Critical-CH"], receiver.ACCEPT_CH)

    def test_page_and_module_do_not_submit_or_store(self):
        self.token()
        status, headers, module = self.request("GET", receiver.FONT_ROUTE + "/probe.js")
        self.assertEqual(status, 200)
        self.assertTrue(module.startswith(b"export async function collectFontContextSupplement()"))
        self.assertNotIn(b"fetch(", module)
        self.assertNotIn(b"Accept-CH", str(headers).encode())
        for directive in ("font-src", "script-src", "connect-src"):
            self.assertNotIn(directive, headers["Content-Security-Policy"])
        self.assertFalse(self.diagnostics.exists())
        self.assertEqual(list(self.captures.iterdir()), [])

    def test_separate_storage_redaction_and_provenance(self):
        status, _, body = self.submit()
        self.assertEqual(status, 200)
        identifier = json.loads(body)["id"]
        path = self.diagnostics / (identifier + ".json")
        record = json.loads(path.read_text())
        self.assertTrue(record["diagnosticOnly"])
        self.assertFalse(record["corpusAdmission"])
        self.assertEqual(record["context"]["consent"]["version"], receiver.CONSENT_VERSION)
        self.assertEqual(record["context"]["source"]["fixture_sha256"], hashlib.sha256(receiver.FONT_FIXTURE.read_bytes()).hexdigest())
        self.assertEqual(record["context"]["source"]["measurement_module_sha256"], hashlib.sha256(receiver.font_context_module()).hexdigest())
        self.assertEqual(path.stat().st_mode & 0o777, 0o600)
        self.assertEqual(list(self.captures.iterdir()), [])
        self.assertEqual(record["result"]["page"], "https://example.test/check")
        rule = record["result"]["accessibleRules"]["fontFaces"][0]
        self.assertEqual(rule["stylesheet"], "https://example.test/style.css")
        self.assertEqual([x["value"] for x in rule["sources"]], ["https://example.test/font.woff2", "[non-http source omitted]", "[non-http source omitted]", "Roboto"])
        self.assertNotIn("secret", path.read_text())
        self.assertNotIn("private-id", path.read_text())

    def test_same_origin_token_and_expiry(self):
        token = self.token()
        for headers in [{"Origin": "https://elsewhere.test"}, {"Content-Type": "text/plain"}, {"X-Diagnostic-Token": "unknown"}]:
            self.assertEqual(self.submit(token=token, headers=headers)[0], 403)
        self.server.diagnostic_tokens[token]["issued"] = time.monotonic() - receiver.DIAGNOSTIC_TOKEN_TTL - 1
        self.assertEqual(self.submit(token=token)[0], 403)
        self.assertFalse(self.diagnostics.exists())

    def test_consent_scope_and_bounds(self):
        token = self.token()
        variants = []
        for consent in [{"accepted": False, "version": receiver.CONSENT_VERSION}, {"accepted": 1, "version": receiver.CONSENT_VERSION}, {"accepted": True, "version": "other"}]:
            data = diagnostic(); data["consent"] = consent; variants.append(data)
        data = diagnostic(); data["result"]["font_file"] = "base64"; variants.append(data)
        data = diagnostic(); data["result"]["metrics"] *= 141; variants.append(data)
        data = diagnostic(); data["result"]["metrics"][0]["metrics"]["width"] = "font bytes"; variants.append(data)
        data = diagnostic(); data["result"]["devicePixelRatio"] = float("inf"); variants.append(data)
        data = diagnostic(); data["result"]["fontFacesBefore"]["faces"][0]["family"] = "x" * 513; variants.append(data)
        for data in variants:
            with self.subTest(data=str(data)[:50]):
                self.assertEqual(self.submit(data, token)[0], 400)
        self.assertFalse(self.diagnostics.exists())
        self.assertEqual(self.submit(token=token, raw=b'{"bad":')[0], 400)
        self.assertEqual(self.submit(token=token, headers={"Content-Length": str(receiver.MAX_DIAGNOSTIC_BODY + 1)}, raw=b'{}')[0], 413)

    def test_response_retry_is_idempotent_and_payload_change_rejected(self):
        token = self.token()
        first = self.submit(token=token)
        second = self.submit(token=token)
        self.assertEqual(first[0], 200)
        self.assertEqual(first[2], second[2])
        self.assertEqual(len(list(self.diagnostics.glob('*.json'))), 1)
        changed = diagnostic(); changed["label"] = "different"
        self.assertEqual(self.submit(changed, token)[0], 409)

    def test_storage_failure_preserves_consent_for_retry(self):
        token = self.token()
        with patch.object(receiver.os, "open", side_effect=PermissionError("test")):
            self.assertEqual(self.submit(token=token)[0], 500)
        self.assertEqual(list(self.diagnostics.glob('*.json')), [])
        self.assertEqual(self.submit(token=token)[0], 200)

    def test_changed_measurement_source_requires_fresh_consent(self):
        token = self.token()
        changed = self.root / "changed-fixture.html"
        changed.write_bytes(receiver.FONT_FIXTURE.read_bytes() + b'\n')
        with patch.object(receiver, "FONT_FIXTURE", changed):
            self.assertEqual(self.submit(token=token)[0], 409)
        self.assertFalse(self.diagnostics.exists())

    def test_command_line_rejects_nested_diagnostic_storage(self):
        response = subprocess.run([sys.executable, str(pathlib.Path(receiver.__file__)),
                                   "--out", str(self.captures), "--diagnostics-out", str(self.captures / "nested")],
                                  capture_output=True, text=True, timeout=5)
        self.assertNotEqual(response.returncode, 0)
        self.assertIn("separate, non-nested", response.stderr)
        response = subprocess.run([sys.executable, str(pathlib.Path(receiver.__file__)),
                                   "--out", str(self.captures), "--diagnostics-out", str(receiver.COLLECTOR_DIR / "diagnostics-test")],
                                  capture_output=True, text=True, timeout=5)
        self.assertNotEqual(response.returncode, 0)
        self.assertIn("outside the publicly served", response.stderr)

    def test_diagnostic_does_not_trigger_capture_once(self):
        self.server.exit_after_capture = True
        self.assertEqual(self.submit()[0], 200)
        self.assertTrue(self.thread.is_alive())
        self.assertEqual(self.request("GET", "/echo")[0], 200)
        capture = {"context": {"label": "ordinary"}, "probes": {}, "repeat": {}}
        self.assertEqual(self.request("POST", "/submit", capture)[0], 200)
        self.thread.join(3)
        self.assertFalse(self.thread.is_alive())
        self.assertEqual(len(list(self.captures.glob('ordinary-*.json'))), 1)


if __name__ == '__main__':
    unittest.main()
