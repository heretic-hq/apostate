#!/usr/bin/env python3
"""Local receiver for device captures.

Serves the collector page and writes submitted captures to disk. Deliberately
plain: no framework, no dependencies, and it binds to the LAN so a phone or a
second machine can reach it.

    python3 capture/server/receive.py --out resources/fingerprints/raw

The /echo endpoint reflects the request headers back to the page. Header order,
casing and the Client Hints the browser volunteered are not readable from
JavaScript, so the only way to capture them is to look at what actually arrived.
"""

import argparse
import datetime
import hashlib
import http.server
import json
import math
import os
import pathlib
import re
import secrets
import socket
import ssl
import sys
import threading
import time
from urllib.parse import urlsplit, urlunsplit

COLLECTOR_DIR = pathlib.Path(__file__).resolve().parent.parent / "collector"
MAX_BODY = 64 * 1024 * 1024  # captures carry raw PNG and audio payloads
DIAGNOSTIC_DIR = pathlib.Path(__file__).resolve().parent.parent / "diagnostics"
FONT_FIXTURE = pathlib.Path(__file__).resolve().parents[2] / "scripts/fixtures/font-context-supplement.html"
RECEIVER_SHA256 = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()
FONT_ROUTE = "/diagnostics/font-context"
MAX_DIAGNOSTIC_BODY = 512 * 1024
CONSENT_VERSION = "font-context-v1"
DIAGNOSTIC_TOKEN_TTL = 30 * 60


def font_context_module() -> bytes:
    """Reuse the tested fixture's measurements, with explicit invocation only."""
    source = FONT_FIXTURE.read_text()
    declarations = source.split("const limit=", 1)[1].split("(async()=>{", 1)[0]
    body = source.split("(async()=>{", 1)[1].split(
        "  document.querySelector('#out').textContent", 1)[0]
    if "result.fontFacesBefore=faceSnapshot()" not in body or "result.passed=" not in body:
        raise ValueError("font fixture entry point changed")
    return ("export async function collectFontContextSupplement(){\nconst limit=" +
            declarations + body + "\nreturn result;\n}\n").encode()


def diagnostic_url(value):
    """Server-side defense for the same URL fields redacted by the fixture."""
    if not isinstance(value, str) or len(value) > 2048:
        raise ValueError("invalid source URL")
    try:
        parsed = urlsplit(value)
        if parsed.scheme not in ("http", "https") or not parsed.hostname:
            return "[non-http source omitted]"
        host = parsed.hostname
        if ":" in host:
            host = "[" + host + "]"
        if parsed.port:
            host += ":" + str(parsed.port)
        return urlunsplit((parsed.scheme, host, parsed.path, "", ""))
    except ValueError:
        return "[unparseable source omitted]"


def validate_font_diagnostic(payload):
    """Accept the bounded authored result, not arbitrary files or capture blobs."""
    if not isinstance(payload, dict) or set(payload) != {"consent", "label", "result"}:
        raise ValueError("expected consent, label and result")
    consent = payload["consent"]
    if (not isinstance(consent, dict) or set(consent) != {"accepted", "version"}
            or consent["accepted"] is not True or consent["version"] != CONSENT_VERSION):
        raise ValueError("explicit consent required")
    label = payload["label"]
    if not isinstance(label, str) or len(label) > 80:
        raise ValueError("device label must be at most 80 characters")
    result = payload["result"]
    allowed = {"diagnosticOnly", "corpusAdmission", "diagnostic_only", "corpus_admission",
               "page", "userAgent", "devicePixelRatio", "language", "fontFacesBefore",
               "fontFacesAfter", "accessibleRules", "bodyStyle", "sanitizerChecksPassed",
               "originalClientrects", "twoLineClientrects", "isolatedLeading", "metrics",
               "mechanics", "passed", "errorName"}
    if not isinstance(result, dict) or set(result) - allowed:
        raise ValueError("unexpected diagnostic fields")
    if (result.get("diagnosticOnly") is not True or result.get("corpusAdmission") is not False
            or result.get("diagnostic_only") is not True
            or result.get("corpus_admission") is not False):
        raise ValueError("diagnostic flags required")
    for key in ("passed", "sanitizerChecksPassed"):
        if key in result and type(result[key]) is not bool:
            raise ValueError("invalid diagnostic status")
    for key in ("userAgent", "language", "errorName"):
        if key in result and (not isinstance(result[key], str) or len(result[key]) > 1024):
            raise ValueError("invalid browser metadata")
    if "devicePixelRatio" in result and type(result["devicePixelRatio"]) not in (int, float):
        raise ValueError("invalid display scale")
    mechanics = result.get("mechanics", {})
    if (not isinstance(mechanics, dict) or set(mechanics) - {"originalChildCount", "twoLineChildCount",
            "isolatedControls", "metricRows", "skippedMetricRows"}
            or any(v is not None and type(v) is not int for v in mechanics.values())):
        raise ValueError("invalid mechanics report")
    # Per-field limits below refine a recursive size/type ceiling. No binary
    # attachments, raw CSS, HTTP headers or privileged font-access data is accepted.
    def bounded(value, depth=0):
        if depth > 9:
            raise ValueError("diagnostic nesting limit")
        if isinstance(value, dict):
            if len(value) > 32 or any(not isinstance(k, str) or len(k) > 64 for k in value):
                raise ValueError("diagnostic object limit")
            for child in value.values():
                bounded(child, depth + 1)
        elif isinstance(value, list):
            if len(value) > 256:
                raise ValueError("diagnostic array limit")
            for child in value:
                bounded(child, depth + 1)
        elif isinstance(value, str):
            if len(value) > 2048:
                raise ValueError("diagnostic text limit")
        elif isinstance(value, float) and not math.isfinite(value):
            raise ValueError("non-finite diagnostic value")
        elif value is not None and not isinstance(value, (bool, int, float)):
            raise ValueError("diagnostic value type")
    bounded(result)
    for key in ("fontFacesBefore", "fontFacesAfter"):
        snapshot = result.get(key, {})
        if (not isinstance(snapshot, dict) or set(snapshot) - {"size", "status", "faces", "truncated"}
                or not isinstance(snapshot.get("faces", []), list)):
            raise ValueError("invalid FontFace snapshot")
        if ("size" in snapshot and type(snapshot["size"]) is not int
                or "status" in snapshot and not isinstance(snapshot["status"], str)
                or "truncated" in snapshot and type(snapshot["truncated"]) is not bool):
            raise ValueError("invalid FontFace snapshot metadata")
        for face in snapshot.get("faces", []):
            face_keys = {"family", "style", "weight", "stretch", "status", "unicodeRange",
                         "variationSettings", "featureSettings", "display", "ascentOverride",
                         "descentOverride", "lineGapOverride"}
            if not isinstance(face, dict) or set(face) - face_keys:
                raise ValueError("invalid FontFace descriptors")
            if any(not isinstance(v, str) or len(v) > 512 for v in face.values()):
                raise ValueError("invalid FontFace descriptor text")
    if not isinstance(result.get("metrics", []), list) or len(result.get("metrics", [])) > 140:
        raise ValueError("metric row limit")
    for row in result.get("metrics", []):
        if not isinstance(row, dict) or set(row) - {"family", "size", "sample", "requestedFont", "metrics", "skipped"}:
            raise ValueError("invalid metric row")
        if (any(k in row and (not isinstance(row[k], str) or len(row[k]) > 512)
                for k in ("family", "sample", "requestedFont", "skipped"))
                or "size" in row and type(row["size"]) not in (int, float)):
            raise ValueError("invalid metric input")
        values = row.get("metrics", {})
        metric_keys = {"width", "actualBoundingBoxLeft", "actualBoundingBoxRight",
                       "actualBoundingBoxAscent", "actualBoundingBoxDescent",
                       "fontBoundingBoxAscent", "fontBoundingBoxDescent", "hangingBaseline",
                       "ideographicBaseline"}
        if not isinstance(values, dict) or set(values) - metric_keys:
            raise ValueError("invalid metric values")
        if any(type(v) not in (int, float) for v in values.values()):
            raise ValueError("metrics must be numeric")
    if not isinstance(result.get("isolatedLeading", []), list) or len(result.get("isolatedLeading", [])) > 8:
        raise ValueError("leading row limit")
    def numeric(value):
        if type(value) not in (int, float):
            raise ValueError("layout values must be numeric")
    def style(value):
        keys = {"font", "fontFamily", "fontSize", "fontStyle", "fontWeight", "fontStretch",
                "lineHeight", "verticalAlign", "display", "marginTop", "marginBottom",
                "paddingTop", "paddingBottom", "borderTopWidth", "borderBottomWidth",
                "writingMode", "transform", "zoom"}
        if (not isinstance(value, dict) or set(value) - keys
                or any(not isinstance(v, str) or len(v) > 512 for v in value.values())):
            raise ValueError("invalid computed style")
    def rect(value):
        if not isinstance(value, dict) or set(value) != {"x", "y", "width", "height", "top", "right", "bottom", "left"}:
            raise ValueError("invalid rectangle")
        for number in value.values():
            numeric(number)
    def node(value):
        if not isinstance(value, dict) or set(value) != {"bounds", "clientRects", "style"}:
            raise ValueError("invalid layout node")
        rect(value["bounds"])
        style(value["style"])
        if not isinstance(value["clientRects"], list) or len(value["clientRects"]) > 16:
            raise ValueError("client rectangle limit")
        for item in value["clientRects"]:
            rect(item)
    def baselines(value):
        if not isinstance(value, list) or len(value) > 2:
            raise ValueError("baseline limit")
        for number in value:
            numeric(number)
    if "bodyStyle" in result:
        style(result["bodyStyle"])
    for key in ("originalClientrects", "twoLineClientrects"):
        if key not in result:
            continue
        value = result[key]
        if isinstance(value, dict) and set(value) == {"skipped"} and isinstance(value["skipped"], str):
            continue
        if not isinstance(value, dict) or set(value) != {"host", "children", "baselines", "baselineAdvance"}:
            raise ValueError("invalid clientrect control")
        node(value["host"])
        if not isinstance(value["children"], list) or len(value["children"]) > 8:
            raise ValueError("clientrect child limit")
        for item in value["children"]:
            node(item)
        baselines(value["baselines"])
        if value["baselineAdvance"] is not None:
            numeric(value["baselineAdvance"])
    for value in result.get("isolatedLeading", []):
        allowed_leading = {"family", "size", "sample", "host", "first", "second", "baselines",
                           "baselineAdvance", "advanceMinusFontBox", "skipped"}
        if not isinstance(value, dict) or set(value) - allowed_leading:
            raise ValueError("invalid leading control")
        if any(k in value and (not isinstance(value[k], str) or len(value[k]) > 512)
               for k in ("family", "sample", "skipped")):
            raise ValueError("invalid leading input")
        for key in ("host", "first", "second"):
            if key in value:
                node(value[key])
        if "baselines" in value:
            baselines(value["baselines"])
        for key in ("size", "baselineAdvance", "advanceMinusFontBox"):
            if key in value:
                numeric(value[key])
    if "page" in result:
        result["page"] = diagnostic_url(result["page"])
    rules = result.get("accessibleRules", {})
    if not isinstance(rules, dict) or set(rules) - {"fontFaces", "inaccessible", "visitedRules", "truncated"}:
        raise ValueError("invalid stylesheet report")
    if not isinstance(rules.get("fontFaces", []), list):
        raise ValueError("invalid font rules")
    if (any(k in rules and type(rules[k]) is not int for k in ("inaccessible", "visitedRules"))
            or "truncated" in rules and type(rules["truncated"]) is not bool):
        raise ValueError("invalid stylesheet counters")
    for rule in rules.get("fontFaces", []):
        if not isinstance(rule, dict) or set(rule) != {"stylesheet", "descriptors", "sources"}:
            raise ValueError("invalid font rule")
        rule["stylesheet"] = diagnostic_url(rule["stylesheet"])
        descriptors = rule["descriptors"]
        descriptor_keys = {"font-family", "font-style", "font-weight", "font-stretch", "font-display",
                           "unicode-range", "font-feature-settings", "font-variation-settings",
                           "ascent-override", "descent-override", "line-gap-override"}
        if (not isinstance(descriptors, dict) or set(descriptors) - descriptor_keys
                or any(not isinstance(v, str) or len(v) > 512 for v in descriptors.values())):
            raise ValueError("invalid font rule descriptors")
        if not isinstance(rule["sources"], list) or len(rule["sources"]) > 32:
            raise ValueError("source descriptor limit")
        for source in rule["sources"]:
            if not isinstance(source, dict) or set(source) != {"kind", "value"}:
                raise ValueError("invalid source descriptor")
            if source["kind"] == "url":
                source["value"] = diagnostic_url(source["value"])
            elif source["kind"] != "local" or not isinstance(source["value"], str) or len(source["value"]) > 256:
                raise ValueError("invalid local source")
    return label.strip(), result

# Ask for every Client Hint the browser is willing to volunteer, so the echo
# probe records the high-entropy set rather than the default low-entropy one.
ACCEPT_CH = ", ".join([
    "Sec-CH-UA", "Sec-CH-UA-Arch", "Sec-CH-UA-Bitness", "Sec-CH-UA-Full-Version",
    "Sec-CH-UA-Full-Version-List", "Sec-CH-UA-Mobile", "Sec-CH-UA-Model",
    "Sec-CH-UA-Platform", "Sec-CH-UA-Platform-Version", "Sec-CH-UA-WoW64",
    "Sec-CH-UA-Form-Factors", "Device-Memory", "DPR", "Viewport-Width", "Width",
])


def slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (text or "").lower()).strip("-") or "unlabelled"


def lan_ip() -> str:
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("192.0.2.1", 1))  # TEST-NET-1; no packets are sent
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


class TLSCapableServer(http.server.ThreadingHTTPServer):
    """Threading server that terminates TLS in the worker, not on accept.

    Wrapping the *listening* socket looks equivalent and is not: accept() then
    performs the handshake on the main thread, so a single client that connects
    and never sends a ClientHello stops the server accepting anything, forever.
    A port scanner did exactly that and the listen queue filled while systemd
    still reported the unit active — listening, alive, and deaf.

    So accept stays plain and the handshake happens in the connection's own
    thread under a timeout. A stalled peer now costs one thread instead of the
    service.
    """

    ssl_ctx = None
    daemon_threads = True
    # The default of 5 is what let a handful of stuck connections fill the queue.
    request_queue_size = 128
    # A peer that opens a socket and says nothing must not hold a worker.
    handshake_timeout = 20

    def finish_request(self, request, client_address):
        if self.ssl_ctx is not None:
            try:
                request.settimeout(self.handshake_timeout)
                request = self.ssl_ctx.wrap_socket(request, server_side=True)
            except (OSError, ssl.SSLError):
                # Scanners, probes and clients with no shared cipher all land
                # here. None of them is an event worth logging or dying for.
                try:
                    request.close()
                except OSError:
                    pass
                return
        super().finish_request(request, client_address)

    def handle_error(self, request, client_address):
        # A broken connection is not a server fault and must not reach stderr as
        # a traceback; the useful output is the per-capture summary.
        pass


class Handler(http.server.SimpleHTTPRequestHandler):
    out_dir: pathlib.Path

    def __init__(self, *a, **kw):
        super().__init__(*a, directory=str(COLLECTOR_DIR), **kw)

    def log_message(self, fmt, *args):
        pass  # the summary printed per capture is the useful output

    def _json(self, code: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def end_headers(self):
        if self.path.split("?")[0].startswith(FONT_ROUTE):
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            # Do not impose font/script/connect source restrictions here: those
            # could change the page-provided FontFace context being diagnosed.
            self.send_header("Content-Security-Policy", "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'")
        else:
            self.send_header("Accept-CH", ACCEPT_CH)
            self.send_header("Critical-CH", ACCEPT_CH)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _diagnostic_asset(self, body, content_type):
        self.send_response(200)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _font_context_get(self, path):
        if not getattr(self.server, "diagnostics_dir", None):
            self._json(404, {"error": "not found"})
            return
        try:
            if path in (FONT_ROUTE, FONT_ROUTE + "/"):
                module = font_context_module()
                template = (DIAGNOSTIC_DIR / "font-context.html").read_bytes()
                token = secrets.token_urlsafe(24)
                with self.server.diagnostic_lock:
                    now = time.monotonic()
                    tokens = self.server.diagnostic_tokens
                    for old in list(tokens):
                        if now - tokens[old]["issued"] > DIAGNOSTIC_TOKEN_TTL:
                            del tokens[old]
                    if len(tokens) >= 128:
                        del tokens[min(tokens, key=lambda key: tokens[key]["issued"])]
                    tokens[token] = {"issued": now, "source": {
                        "receiver_sha256": RECEIVER_SHA256,
                        "fixture_sha256": hashlib.sha256(FONT_FIXTURE.read_bytes()).hexdigest(),
                        "measurement_module_sha256": hashlib.sha256(module).hexdigest(),
                        "consent_page_sha256": hashlib.sha256(template).hexdigest(),
                        "page_script_sha256": hashlib.sha256((DIAGNOSTIC_DIR / "font-context.js").read_bytes()).hexdigest(),
                    }}
                self._diagnostic_asset(template.replace(b"__DIAGNOSTIC_TOKEN__", token.encode()), "text/html; charset=utf-8")
            elif path == FONT_ROUTE + "/probe.js":
                self._diagnostic_asset(font_context_module(), "text/javascript; charset=utf-8")
            elif path == FONT_ROUTE + "/app.js":
                self._diagnostic_asset((DIAGNOSTIC_DIR / "font-context.js").read_bytes(), "text/javascript; charset=utf-8")
            else:
                self._json(404, {"error": "not found"})
        except (OSError, ValueError, IndexError):
            self._json(503, {"error": "font diagnostic assets unavailable"})

    def _font_context_post(self):
        if not getattr(self.server, "diagnostics_dir", None):
            self._json(404, {"error": "not found"})
            return
        scheme = "https" if isinstance(self.connection, ssl.SSLSocket) else "http"
        if (self.headers.get("Origin") != scheme + "://" + self.headers.get("Host", "")
                or self.headers.get("Content-Type", "").split(";", 1)[0] != "application/json"):
            self._json(403, {"error": "same-origin JSON submission required"})
            return
        token = self.headers.get("X-Diagnostic-Token", "")
        with self.server.diagnostic_lock:
            grant = self.server.diagnostic_tokens.get(token)
            if not grant or time.monotonic() - grant["issued"] > DIAGNOSTIC_TOKEN_TTL:
                self._json(403, {"error": "consent page expired; reload it before another check"})
                return
        try:
            length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self._json(400, {"error": "bad Content-Length"})
            return
        if not 0 < length <= MAX_DIAGNOSTIC_BODY:
            self._json(413, {"error": "diagnostic body size limit"})
            return
        try:
            self.connection.settimeout(10)
            raw = self.rfile.read(length)
            if len(raw) != length:
                raise ValueError("incomplete diagnostic body")
            def reject_constant(_value):
                raise ValueError("non-finite JSON number")
            payload = json.loads(raw, parse_constant=reject_constant)
            label, result = validate_font_diagnostic(payload)
        except (ValueError, UnicodeError, OSError):
            self._json(400, {"error": "invalid or out-of-scope font diagnostic"})
            return
        digest = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        with self.server.diagnostic_lock:
            path = None
            created = False
            try:
                sources = grant["source"]
                current = {
                    "receiver_sha256": RECEIVER_SHA256,
                    "fixture_sha256": hashlib.sha256(FONT_FIXTURE.read_bytes()).hexdigest(),
                    "measurement_module_sha256": hashlib.sha256(font_context_module()).hexdigest(),
                    "consent_page_sha256": hashlib.sha256((DIAGNOSTIC_DIR / "font-context.html").read_bytes()).hexdigest(),
                    "page_script_sha256": hashlib.sha256((DIAGNOSTIC_DIR / "font-context.js").read_bytes()).hexdigest(),
                }
                if current != sources:
                    self._json(409, {"error": "the diagnostic changed; reload the consent page"})
                    return
            except (OSError, ValueError, IndexError):
                self._json(503, {"error": "font diagnostic assets unavailable"})
                return
            # Retry of an already stored request is idempotent, including a lost
            # response. A changed payload cannot reuse the same consent grant.
            if "saved" in grant:
                if grant["digest"] != digest:
                    self._json(409, {"error": "this consent token already stored a different check"})
                else:
                    self._json(200, {"ok": True, "id": grant["saved"], "diagnosticOnly": True})
                return
            received = datetime.datetime.now(datetime.timezone.utc)
            identifier = "font-context-" + received.strftime("%Y%m%dT%H%M%SZ") + "-" + secrets.token_hex(6)
            record = {"kind": "font-context-supplement", "version": 1,
                      "diagnosticOnly": True, "corpusAdmission": False,
                      "context": {"label": label, "consent": payload["consent"],
                                  "received_utc": received.isoformat(), "source": grant["source"],
                                  "secure_transport": scheme == "https"}, "result": result}
            try:
                directory = self.server.diagnostics_dir
                directory.mkdir(parents=True, exist_ok=True, mode=0o700)
                path = directory / (identifier + ".json")
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
                created = True
                with os.fdopen(fd, "w", encoding="utf-8") as output:
                    json.dump(record, output, indent=1, allow_nan=False)
            except OSError:
                if created and path is not None:
                    try:
                        path.unlink(missing_ok=True)
                    except OSError:
                        pass
                self._json(500, {"error": "diagnostic could not be saved; retry the same check"})
                return
            grant.update(saved=identifier, digest=digest)
        self._json(200, {"ok": True, "id": identifier, "diagnosticOnly": True})

    def do_GET(self):
        if self.path.split("?")[0].startswith(FONT_ROUTE):
            self._font_context_get(self.path.split("?")[0])
            return
        if self.path.split("?")[0] == "/echo":
            # Raw header list, in arrival order, with original casing preserved.
            self._json(200, {
                "headers": [[k, v] for k, v in self.headers.items()],
                "header_order": [k for k in self.headers.keys()],
                "http_version": self.request_version,
                "remote_family": "ipv6" if ":" in self.client_address[0] else "ipv4",
            })
            return
        super().do_GET()

    def do_POST(self):
        if self.path == FONT_ROUTE + "/submit":
            self._font_context_post()
            return
        if self.path != "/submit":
            self._json(404, {"error": "not found"})
            return

        try:
            length = int(self.headers.get("Content-Length", 0))
        except ValueError:
            self._json(400, {"error": "bad Content-Length"})
            return
        if length <= 0 or length > MAX_BODY:
            self._json(413, {"error": f"body must be 1..{MAX_BODY} bytes, got {length}"})
            return

        try:
            capture = json.loads(self.rfile.read(length))
        except json.JSONDecodeError as e:
            self._json(400, {"error": f"invalid json: {e}"})
            return

        ctx = capture.get("context") or {}
        stamp = datetime.datetime.now(datetime.timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        path = self.out_dir / f"{slug(ctx.get('label'))}-{stamp}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(capture, indent=1))

        summarise(capture, path)
        self._json(200, {"ok": True, "path": str(path)})

        if self.server.exit_after_capture:
            # Scripted runs otherwise have to kill this process by name, and a
            # pattern that matches "receive.py" also matches the ssh command
            # line invoking it — which kills the caller's own session. Exiting
            # on our own removes the need for any pkill at all.
            threading.Thread(target=self.server.shutdown, daemon=True).start()


def summarise(capture: dict, path: pathlib.Path) -> None:
    probes = capture.get("probes") or {}
    repeat = capture.get("repeat") or {}
    ctx = capture.get("context") or {}
    failed = [k for k, v in probes.items() if not v.get("ok")]
    unstable = [
        k for k, v in repeat.items()
        if v.get("ok") and probes.get(k, {}).get("ok")
        and json.dumps(probes[k].get("value"), sort_keys=True) != json.dumps(v.get("value"), sort_keys=True)
    ]

    print(f"\n=== capture: {path.name}")
    print(f"    label   {ctx.get('label')}")
    print(f"    ua      {(ctx.get('ua') or '')[:100]}")
    print(f"    probes  {len(probes) - len(failed)}/{len(probes)} measured")
    if ctx.get("automation_suspected"):
        print(f"    NOT T0  automation signals: {', '.join(ctx.get('automation_signals') or [])}")
    for k in failed:
        print(f"    FAILED  {k}: {probes[k].get('error')}")
    if unstable:
        # Not necessarily an error: some fields legitimately move. Recording which
        # ones is how we learn a field's variance before judging a V3 difference.
        print(f"    UNSTABLE across repeat read: {', '.join(unstable)}")
    else:
        print("    stable  all deterministic probes identical across repeat read")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--out", type=pathlib.Path, default=pathlib.Path("resources/fingerprints/raw"))
    ap.add_argument("--diagnostics-out", type=pathlib.Path,
                    help="opt in to the font-consent page; store diagnostics separately from captures")
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--bind", default="0.0.0.0")
    ap.add_argument("--once", action="store_true",
                    help="exit after the first capture is stored")
    ap.add_argument("--cert", type=pathlib.Path,
                    help="TLS certificate chain (enables https)")
    ap.add_argument("--key", type=pathlib.Path, help="TLS private key")
    args = ap.parse_args()

    if bool(args.cert) != bool(args.key):
        sys.exit("--cert and --key must be given together")

    if not (COLLECTOR_DIR / "collector.js").exists():
        sys.exit(f"collector not found at {COLLECTOR_DIR}")

    Handler.out_dir = args.out.resolve()
    Handler.out_dir.mkdir(parents=True, exist_ok=True)
    diagnostics_dir = args.diagnostics_out.resolve() if args.diagnostics_out else None
    if diagnostics_dir and (diagnostics_dir == Handler.out_dir
                            or diagnostics_dir.is_relative_to(Handler.out_dir)
                            or Handler.out_dir.is_relative_to(diagnostics_dir)):
        sys.exit("--diagnostics-out and --out must be separate, non-nested directories")
    if diagnostics_dir and diagnostics_dir.is_relative_to(COLLECTOR_DIR.resolve()):
        sys.exit("--diagnostics-out must be outside the publicly served collector directory")

    srv = TLSCapableServer((args.bind, args.port), Handler)
    # Request threads are non-daemon by default, so serve_forever() returning
    # is not enough to end the process — it waits for them. --once would then
    # store a capture and hang, which is the opposite of the point.
    srv.daemon_threads = True
    srv.exit_after_capture = args.once
    srv.diagnostics_dir = diagnostics_dir
    srv.diagnostic_tokens = {}
    srv.diagnostic_lock = threading.Lock()

    scheme = "http"
    if args.cert:
        # TLS is terminated here rather than behind a reverse proxy, and that is
        # deliberate. /echo is a probe: it reports the request's header names in
        # arrival order with original casing, which is how the field-trial
        # testing config divergence was found. Every mainstream reverse proxy
        # parses headers into a map before forwarding, which loses order and
        # canonicalises casing — it would answer the probe with a description of
        # the proxy instead of the browser.
        #
        # Serving HTTP/1.1 over TLS also keeps the header names mixed-case, so
        # captures taken here stay comparable with every capture taken over
        # plain HTTP/1.1 before it. An h2 edge would lowercase them all and
        # reorder pseudo-headers, which is a legitimate thing to measure but a
        # different thing, and it would silently invalidate the reference set.
        ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
        ctx.load_cert_chain(certfile=str(args.cert), keyfile=str(args.key))
        ctx.set_alpn_protocols(["http/1.1"])
        srv.ssl_ctx = ctx
        scheme = "https"

    print(f"capture receiver on :{args.port}   writing to {Handler.out_dir}")
    print(f"  this machine   {scheme}://localhost:{args.port}/")
    print(f"  other devices  {scheme}://{lan_ip()}:{args.port}/")
    if diagnostics_dir:
        print(f"  optional fonts {scheme}://{lan_ip()}:{args.port}{FONT_ROUTE}")
        print(f"  diagnostics    {diagnostics_dir} (not admitted to the capture corpus)")
    if scheme == "http":
        print("  NOTE: plain http is a secure context only on localhost; a "
              "capture taken over http to any other host loses 11 probes.")
    print("\nOpen in a normal browser window. Ctrl-C to stop.")
    try:
        srv.serve_forever()
        if args.once:
            print("captured; exiting")
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
