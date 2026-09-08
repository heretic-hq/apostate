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
import ssl
import sys
import threading

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

    srv = TLSCapableServer((args.bind, args.port), Handler)
    # Request threads are non-daemon by default, so serve_forever() returning
    # is not enough to end the process — it waits for them. --once would then
    # store a capture and hang, which is the opposite of the point.
    srv.daemon_threads = True
    srv.exit_after_capture = args.once

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
