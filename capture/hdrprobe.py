#!/usr/bin/env python3
"""Serve a page that issues one fetch(), and record that fetch's header order."""
import sys, json
from http.server import BaseHTTPRequestHandler, HTTPServer

PAGE = b"""<!doctype html><meta charset=utf-8><title>h</title>
<script>fetch('/echo').then(()=>{}).catch(()=>{});</script>
<p>probing</p>"""

result = {}

class H(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"
    def log_message(self, *a): pass
    def do_GET(self):
        if self.path == "/echo":
            result["order"] = [k for k, _ in self.headers.raw_items()]
            result["headers"] = {k: v for k, v in self.headers.raw_items()}
            body = b"ok"
        else:
            body = PAGE
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(body)
        if self.path == "/echo":
            raise SystemExit(0)

port = int(sys.argv[1])
s = HTTPServer(("0.0.0.0", port), H)
s.timeout = 90
try:
    while "order" not in result:
        s.handle_request()
except SystemExit:
    pass
print(json.dumps(result, indent=1))
