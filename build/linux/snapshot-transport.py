#!/usr/bin/env python3
"""Run one build step through a temporary loopback snapshot transport.

Only the configured dated Ubuntu snapshot is reachable. Responses retain their
signed bytes; cache-busting query parameters affect transport, not APT inputs.
"""
import hashlib
import http.server
import os
import pathlib
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request


def main():
    if len(sys.argv) < 2:
        raise SystemExit('provide a command to run')
    sources = pathlib.Path('/etc/apt/sources.list.d/ubuntu.sources')
    original = sources.read_text()
    origins = set(re.findall(r'https://snapshot\.ubuntu\.com/ubuntu/[0-9]{8}T[0-9]{6}Z/?', original))
    if len(origins) != 1:
        raise SystemExit('expected exactly one pinned Ubuntu snapshot')
    origin = origins.pop().rstrip('/')
    prefix = urllib.parse.urlsplit(origin).path + '/'
    token = hashlib.sha256(pathlib.Path(__file__).read_bytes()).hexdigest()[:24]
    slots = threading.BoundedSemaphore(8)

    class Handler(http.server.BaseHTTPRequestHandler):
        def log_message(self, *_):
            pass

        def do_GET(self):
            parsed = urllib.parse.urlsplit(self.path)
            if (parsed.scheme or parsed.netloc or parsed.query
                    or not parsed.path.startswith(prefix)
                    or '..' in urllib.parse.unquote(parsed.path).split('/')):
                self.send_error(403)
                return
            if not slots.acquire(blocking=False):
                self.send_error(503)
                return
            try:
                for attempt in range(3):
                    suffix = str(attempt) if attempt == 0 else str(time.time_ns())
                    url = 'https://snapshot.ubuntu.com' + parsed.path + '?apostate-input=' + token + '&retry=' + suffix
                    try:
                        request = urllib.request.Request(url, headers={'Cache-Control': 'no-cache'})
                        with urllib.request.urlopen(request, timeout=20) as upstream, tempfile.TemporaryFile() as data:
                            size = 0
                            while block := upstream.read(1024 * 1024):
                                size += len(block)
                                if size > 512 * 1024 * 1024:
                                    raise ValueError('snapshot object exceeds transport limit')
                                data.write(block)
                            data.seek(0)
                            self.send_response(200)
                            self.send_header('Content-Length', str(size))
                            self.end_headers()
                            while block := data.read(1024 * 1024):
                                self.wfile.write(block)
                        return
                    except urllib.error.HTTPError as error:
                        if error.code == 404:
                            self.send_error(404)
                            return
                    except (BrokenPipeError, ConnectionResetError):
                        return
                    except (OSError, ValueError):
                        pass
                self.send_error(502)
            finally:
                slots.release()

    server = http.server.ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    server.daemon_threads = True
    proxy = 'http://127.0.0.1:' + str(server.server_address[1])
    transport = proxy + urllib.parse.urlsplit(origin).path
    config = pathlib.Path('/etc/apt/apt.conf.d/52-snapshot-transport')
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        sources.write_text(original.replace(origin, transport))
        config.write_text('Acquire::http::Timeout "90";\n')
        environment = dict(os.environ, APOSTATE_SNAPSHOT_PROXY=transport,
                           APOSTATE_SNAPSHOT_BASE=origin)
        result = subprocess.run(sys.argv[1:], env=environment)
    finally:
        sources.write_text(original)
        config.unlink(missing_ok=True)
        server.shutdown()
        server.server_close()
        thread.join()
    raise SystemExit(result.returncode)


if __name__ == '__main__':
    main()
