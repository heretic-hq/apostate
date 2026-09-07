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
import http.server
import json
import pathlib
import re
import socket
import sys

COLLECTOR_DIR = pathlib.Path(__file__).resolve().parent.parent / "collector"
MAX_BODY = 64 * 1024 * 1024  # captures carry raw PNG and audio payloads

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
        self.send_header("Accept-CH", ACCEPT_CH)
        self.send_header("Critical-CH", ACCEPT_CH)
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def do_GET(self):
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
    ap.add_argument("--port", type=int, default=8777)
    ap.add_argument("--bind", default="0.0.0.0")
    args = ap.parse_args()

    if not (COLLECTOR_DIR / "collector.js").exists():
        sys.exit(f"collector not found at {COLLECTOR_DIR}")

    Handler.out_dir = args.out.resolve()
    Handler.out_dir.mkdir(parents=True, exist_ok=True)

    srv = http.server.ThreadingHTTPServer((args.bind, args.port), Handler)
    print(f"capture receiver on :{args.port}   writing to {Handler.out_dir}")
    print(f"  this machine   http://localhost:{args.port}/")
    print(f"  other devices  http://{lan_ip()}:{args.port}/")
    print("\nOpen in a normal browser window. Ctrl-C to stop.")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        print("\nstopped")


if __name__ == "__main__":
    main()
