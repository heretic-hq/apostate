# Capture Receiver — probe.chaser.sh

The public endpoint people open to contribute a capture.

    https://probe.chaser.sh/

Runs on 64.118.137.58 as `apostate-probe.service`, writing to
`/var/lib/apostate-probe/captures/`. Enabled at boot; certbot renews on its own
timer and a deploy hook restarts the service, because the receiver holds the
certificate open and would otherwise serve the expired one until restarted.

## Why HTTPS was necessary

Eleven probes depend on secure-context-gated APIs — `crypto.subtle`,
`userAgentData`, `mediaDevices`, `storage`, `getBattery`, `keyboard`,
`getScreenDetails` and WebGPU among them. Over plain HTTP to anything but
localhost, every one reports "unsupported", and the capture reads as a device
missing half its APIs rather than as a capture taken through the wrong URL. Four
captures were lost to this before the collector learned to refuse.

An SSH tunnel to `localhost` also solves it, and is fine for a machine you
control. It is not fine for a machine you are borrowing for ten minutes, which
is most of the corpus.

## Why TLS is terminated in the receiver, not behind a proxy

`/echo` is a **probe**, not plumbing. It reports the request's header names in
arrival order with original casing, and that is how the field-trial testing
config divergence was found: our build emitted `Accept-Language` fifteenth of
twenty-five where stock Chrome emits it twenty-fourth.

Every mainstream reverse proxy parses headers into a map before forwarding.
A map has no order, and Go's `http.Header` canonicalises casing on the way
through. Proxying `/echo` would answer the probe with a description of the proxy
instead of the browser — and it would look plausible, which is worse.

So the receiver terminates TLS itself, with ALPN pinned to `http/1.1`.

## Why HTTP/1.1 and not h2

h2 lowercases every header name and puts pseudo-headers first. That is a
legitimate thing to measure — it is what real sites see — but it is a *different*
thing, and switching to it would silently invalidate every capture taken before,
since `headers.echo` would no longer be comparable across the transport change.

Pinning `http/1.1` keeps the names mixed-case and the order intact, so captures
taken here diff cleanly against captures taken over plain HTTP months earlier.
Verified: a capture through this endpoint reports `Accept-Language` at index 24
of 25, matching stock Chrome, with the first six header names identical to
earlier captures.

Measuring h2 header order is worth doing eventually. It should be a separate
probe against a separate endpoint, not a change to this one.

## Contract

    GET  /             index.html
    GET  /collector.js collector.js, byte-exact
    GET  /echo         request headers, arrival order, original casing
    POST /submit       capture JSON, up to 64 MB

`collector.js` must be served byte-for-byte. The page hashes it into every
capture's `context.collector_sha256`, and conform.py refuses to compare captures
whose hashes differ — so any minification or rewriting silently splits the
corpus in two. Deployed hash is verified against the repository copy.

Every response carries `Accept-CH` and `Critical-CH` for the fifteen high-entropy
hints, and `Cache-Control: no-store`. Without the first two the browser never
sends the hints and the header probe loses ten of its twenty-five headers.

## Operating it

    systemctl status apostate-probe        # on 64.118.137.58
    ls /var/lib/apostate-probe/captures/

Captures accumulate there and are pulled into `resources/fingerprints/raw/`
after checking `automation_suspected`, `secure_context` and the failed-probe
count — the three gates `scripts/decompose-capture.py` also enforces.
