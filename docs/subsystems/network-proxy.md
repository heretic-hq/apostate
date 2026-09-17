# Subsystem 10 — Network & Proxy Transport

Apostate supports credentialed **stream** proxy transport and, for single-hop
SOCKS5 proxies, **datagram** transport via RFC 1928 UDP ASSOCIATE, which is
what carries HTTP/3. Proxy credentials are launch-only transport state; they
are not device-profile identity and are never placed in `ProxyServer`,
`ProxyChain`, `QuicSessionKey`, cache keys, socket-pool keys, NetLog, or
persisted profile data.

## Support boundary

| Transport | HTTP proxy | HTTPS proxy | SOCKS4 | SOCKS5 |
|---|---:|---:|---:|---:|
| HTTP request stream | supported | supported | supported | supported |
| HTTPS request stream (`CONNECT`) | supported | supported | supported | supported |
| WebSocket stream | supported | supported | supported | supported |
| SOCKS5 UDP ASSOCIATE | n/a | n/a | n/a | supported |
| Proxied QUIC / HTTP/3 | unsupported | unsupported | unsupported | supported |
| WebRTC UDP/STUN/TURN | unsupported | unsupported | unsupported | supported |

"Supported" means the native path can use the configured endpoint and, when
supplied, launch-only credentials. Proxied QUIC is supported only through a
**single-hop SOCKS5** chain: a multi-proxy chain, a SOCKS4 proxy, an HTTP
proxy or an HTTPS proxy still refuses QUIC with `ERR_NO_SUPPORTED_PROXIES`,
because none of them can relay a datagram. QUIC over a QUIC proxy remains
Chromium's own MASQUE path and is untouched.

WebRTC UDP, STUN and TURN are relayed through a single-hop SOCKS5 chain by patch
`0086`. Every WebRTC datagram travels through an RFC 1928 UDP association, so a
peer sees the proxy as the packet source and the candidates the page is given
are the proxy's. Patches `0073` and `0074` rewrite only the SDP candidate text,
which on its own left the candidate stating one address while the datagrams left
from a real interface.

Under a proxy that cannot carry a datagram, no UDP socket is created: no host
candidate, no srflx candidate and no UDP relay candidate. TCP is unaffected, so
a TURN server reached over `turn:...?transport=tcp` or `turns:` still yields a
relay candidate through `P2PSocketTcp`, which already routes through
`ProxyResolvingClientSocketFactory`. With no proxy configured the behaviour is
unchanged.

Two residuals while the relay is in use. The host candidate carries the
association's ingress address, the address the proxy told the browser to send
to, and whether that is the same port a peer observes as the packet source
depends on the proxy implementation; the address peers actually see reaches the
page as the srflx candidate that the page's own STUN server produces over the
same association. And the enterprise `WebRtcUdpPortRange` constraint no longer
applies, because the port a page sees is the proxy's rather than one this host
chose.

`--fingerprint-webrtc-udp=direct` forces direct UDP and is an explicit opt-in to
publishing the host's real address; `block` never creates the socket.

Apostate does not silently fall back from a failed datagram association to a
direct socket. When UDP ASSOCIATE fails, the QUIC attempt fails and Chromium's
ordinary alternative-service machinery resumes the TCP job through the same
proxy — the same outcome as a network that blocks UDP. No packet for the
destination is ever emitted outside the proxy.

## Credential and identity model

The public proxy endpoint remains credential-free:

```text
--proxy-server=socks5://proxy.example:1080
```

The Node launcher parses URL userinfo only for an ephemeral native launch
envelope:

```json
{
  "device_profile": { "...": "validated profile fields" },
  "proxy_credentials": {
    "username": "launch-only username",
    "password": "launch-only password"
  }
}
```

The envelope travels through the existing `--apostate-profile` profile-loader
channel. The native loader keeps credentials in memory and exposes them only to
the network stack. It accepts the legacy profile-only payload when no proxy
credentials are configured. The device-profile schema remains unchanged;
credentials must not be persisted in profile files, resolver records, family
manifests, acceptance metadata, diagnostics, or release artifacts.

Credentials are deliberately separate from `ProxyServer` and `ProxyChain`.
Chromium serializes proxy identity into NetLog, net-export, socket-pool group
keys, session/cache keys, error paths, and telemetry. Embedding userinfo there
would leak secrets and alter connection reuse. Invalid or oversized credentials
are rejected or ignored deterministically; they are never truncated.

The datagram path preserves this exactly. `SOCKS5DatagramClientSocket` reads
`base::apostate::Profile::Get()->proxy_credentials()` at socket construction —
the same point and the same source as `connect_job_params_factory.cc` does for
streams — and holds them for the lifetime of that one socket. No credential
reaches `QuicSessionKey`, `QuicSessionAliasKey`, the crypto config key, the
QUIC server-id, or any NetLog parameter, so two launches with different
credentials against the same proxy still share session keying, and a NetLog
capture of a proxied HTTP/3 session contains no secret.

## HTTP and HTTPS proxy authentication

HTTP proxy authentication uses Chromium's existing `HttpAuthController` and
proxy connection state machine:

```text
proxy request / CONNECT
  -> 407 Proxy-Authenticate
  -> native launch credentials
  -> Proxy-Authorization retry
```

The launch-only credential path bypasses `HttpAuthCache`, so credentials do not
become reusable persisted or shared auth state. Authenticated HTTP and HTTPS
streams share the same native path; HTTPS is an HTTP `CONNECT` tunnel followed
by the normal TLS stream.

Unauthenticated proxies continue through the ordinary Chromium path. A proxy
that challenges without configured credentials retains Chromium's normal auth
challenge behaviour rather than receiving invented credentials.

## SOCKS4 and SOCKS5 stream authentication

SOCKS4 receives the configured username as its RFC 1928 user-ID field. SOCKS4
has no password field, so a password does not create a second authentication
mechanism.

SOCKS5 uses the existing RFC 1929 implementation. Without credentials, the
client offers no authentication. With credentials, it offers username/password
and sends the RFC 1929 sub-negotiation before the existing CONNECT request.
Credential bytes are held only by the connection attempt and are not part of
proxy identity or socket keys.

---

# The datagram path

## 1. Why a socket and not a proxy job

Chromium has one mechanism for proxied QUIC — `QuicSessionPool::ProxyJob` —
and it is QUIC-proxy-only: it opens a QUIC session to the proxy and tunnels
the inner session over a stream (`net/quic/quic_session_pool_proxy_job.h`).
Routing a SOCKS5 chain into it would ask it to speak QUIC to a SOCKS proxy.

A SOCKS5 UDP association is not a tunnel over a stream. It is a *datagram
relay*: each datagram carries its own destination header and is forwarded
independently. That maps exactly onto `DatagramClientSocket`, which is the one
abstraction `QuicSessionPool`, `QuicChromiumPacketReader` and
`QuicChromiumPacketWriter` already talk to. So the relay belongs behind that
interface, and the QUIC stack above it does not learn a new concept.

This satisfies A1 in the narrow sense that matters here: no QUIC behaviour is
re-implemented, no packet is rewritten, no accessor is wrapped. The bytes
Chromium's QUIC stack decides to send are the bytes that leave the machine,
prefixed by the relay header the RFC requires.

## 2. The three connections

```text
                  TCP control (held open, idle)
  browser  ─────────────────────────────────────────►  proxy:1080
     │        greet / RFC 1929 auth / UDP ASSOCIATE
     │                                                    │
     │        UDP datagrams, each with a
     │        RFC 1928 §7 request header               relay:NNNN
     └─────────────────────────────────────────────────►  │
                                                          │  proxy resolves
                                                          │  DST.ADDR and
                                                          ▼  forwards
                                                     destination:443
```

**The TCP control connection is the association's lifetime.** RFC 1928 §7:
“A UDP association terminates when the TCP connection that the UDP ASSOCIATE
request arrived on terminates.” So `SOCKS5DatagramClientSocket` owns that
`StreamSocket` for as long as it owns the UDP socket, and destroying the
datagram socket destroys the association.

Keeping it open is not the same as keeping it busy. After the ASSOCIATE reply
the client sends **nothing** further on the control connection — the RFC
defines no keep-alive and inventing one would give the proxy a signature no
other SOCKS5 client emits. Instead the socket posts one 1-byte `Read()` on the
control connection and never completes it under normal operation. Any
completion is terminal:

| control read result | meaning | surfaced as |
|---|---|---|
| `0` | proxy closed the control connection | `ERR_CONNECTION_CLOSED` |
| `> 0` | proxy sent unexpected stream data | `ERR_SOCKS_CONNECTION_FAILED` |
| `< 0` | transport error | that error |

The error is latched. A pending datagram read completes with it immediately; a
later `Read`/`Write`/`ReadMultiple` returns it synchronously. QUIC sees a
socket error, which is the path it already has for a dead network, and closes
the connection. Liveness of the datagram path itself is QUIC's own business:
its idle timeout and `PING` keep the relay's NAT mapping alive exactly as they
do for a direct socket, so no Apostate-specific traffic pattern exists.

### When the control connection drops mid-flight

This is the failure mode worth spelling out, because it is the one that most
resembles a censored connection and the one a naive implementation gets wrong.
A relay whose control connection has gone still accepts nothing: RFC 1928 §7
terminates the association with the TCP connection, so every subsequent
datagram is discarded by the proxy. A socket that only *forwarded* writes would
keep reporting success for each `Write`, QUIC would keep retransmitting into a
relay that is no longer listening, and the connection would end in an idle
timeout tens of seconds later — the exact shape of a blackholed, filtered path.

So the drop is made loud at the first opportunity:

1. The control read is outstanding at all times after the ASSOCIATE reply, so
   the closure is observed as soon as the kernel delivers it, not on the next
   write.
2. Its result is latched into a member error before anything else can run.
3. A datagram read already pending is completed with that error immediately —
   `Read` through its callback, `ReadMultiple` through the
   `base::expected` failure path — rather than left waiting for a datagram
   that can never arrive.
4. Every later `Read`, `ReadMultiple` and `Write` returns the latched error
   synchronously. In particular `Write` **fails** instead of succeeding, which
   is the difference between QUIC closing the session now and QUIC discovering
   it at the idle timeout.

QUIC then takes its ordinary write-error path: `QuicChromiumPacketWriter`
hands the error to its delegate, the session closes, and
`HttpStreamFactory` resumes the TCP job through the same proxy. The timing is
indistinguishable from a socket error on a direct connection, because that is
what it is reported as.

What this design does *not* do is reconnect the control connection and open a
second association. A new association has a new relay port and, from the
proxy's point of view, a new client flow; QUIC's connection ID would continue
across it while the 4-tuple changed underneath, which is a migration the server
never agreed to and a pattern no ordinary client produces. Failing closed and
letting the stack re-establish is both simpler and quieter.

## 3. Establishment, and why the reply address is often wrong

`ConnectAsync(addr, callback)` receives the **proxy's** endpoint (see §5) and
runs one state machine:

1. `ClientSocketFactory::CreateTransportClientSocket({addr})` → `Connect`.
2. Greeting. Without credentials: `05 01 00`, byte-identical to upstream.
   With credentials: `05 02 00 02`, and the proxy chooses.
3. If the proxy selected `0x02`, the RFC 1929 sub-negotiation
   (`01 ULEN UNAME PLEN PASSWD`), then a 2-byte status reply. Any status other
   than `0x00` is `ERR_SOCKS_CONNECTION_FAILED`. Method `0xFF` is also an
   authentication failure; that is what a proxy requiring credentials answers
   when none were configured.
4. UDP ASSOCIATE request: `05 03 00` + `ATYP 0x01` + `00 00 00 00` + `00 00`.
   The DST.ADDR/DST.PORT of an ASSOCIATE request is the address the *client*
   will send datagrams from, and RFC 1928 §4 says a client that does not know
   it “MUST use a port number and address of all zeros”. We never know it: the
   local UDP port is not bound until step 6. Sending zeros is therefore the
   correct and the only honest value, and it is what every conforming client
   sends.
5. Reply: `05 REP 00 ATYP BND.ADDR BND.PORT`. `REP != 0x00` maps to
   `ERR_SOCKS_CONNECTION_FAILED`, except `0x07` (command not supported) which
   also maps there — a proxy without UDP ASSOCIATE is the common case and must
   fail fast so the TCP job resumes.
6. The relay endpoint is `BND.PORT` at an address chosen by these rules, in
   order:

   | reply BND.ADDR | action | why |
   |---|---|---|
   | unspecified (`0.0.0.0`, `::`) | substitute the control connection's peer address | The most common real reply. RFC 1928 does not require a routable BND.ADDR and many proxies fill zeros meaning “this host”. |
   | loopback, while the control peer is not loopback | substitute the control peer address | The proxy reported its own inside-the-box address; sending there would leave the browser talking to itself. |
   | `ATYP 0x03` (domain name) | substitute the control peer address | A datagram socket has no resolver, and resolving the relay name locally would be a DNS query the stream path never makes. |
   | address family differs from the control peer's | substitute the control peer address | A v4 relay address on a v6 control connection cannot be reached from the socket the pool created. |
   | otherwise | use it verbatim | Split control/relay deployments are legitimate. |

   The port is always taken from `BND.PORT`; only the address is ever
   substituted. No new NetLog event type is introduced for any of this — the
   socket returns the underlying UDP socket's `NetLogWithSource` from
   `NetLog()`, so a net-export capture gains no new field and, in particular,
   no field containing the relay's address or the proxy's credentials. Whether
   substitution happened is reachable from tests through an accessor and
   nowhere else. Patch `0001` deferred dedicated SOCKS auth events for the
   same reason; this keeps that choice consistent.
7. The real UDP socket — created by the pool through the ordinary
   `ClientSocketFactory::CreateDatagramClientSocket` path, so it is a stock
   `UDPClientSocket` with stock behaviour — is connected to the relay endpoint.
   Connecting rather than binding matters: the kernel then drops datagrams
   from any source other than the relay, which restores exactly the source
   filtering the direct path has. RFC 1928 §7 requires the relay to drop
   datagrams whose source is not the associated client, so the filtering is
   symmetric.

Only step 7's socket carries data. Everything the pool configures
(`UseNonBlockingIO`, `ApplySocketTag`, `SetReceiveBufferSize`,
`SetDoNotFragment`, `SetSendBufferSize`, `GetLocalAddress`,
`EnableRecvOptimization`, `SetMulticastInterface`, `SetIOSNetworkServiceType`,
`RegisterQuicConnectionClosePayload`) forwards to it unchanged, so the socket's
kernel-visible configuration is identical to a direct QUIC socket.

The synchronous `Connect`, `ConnectUsingNetwork` and `ConnectUsingDefaultNetwork`
cannot run a multi-round-trip handshake and return `ERR_NOT_IMPLEMENTED`. They
are unreachable: `QuicSessionAttempt` is forced onto `CreateSessionAsync` for a
SOCKS5 chain regardless of `kAsyncQuicSession`, which is disabled by default off
Windows (`net/base/features.cc`).

## 4. Per-datagram framing

RFC 1928 §7 defines the header on every relayed datagram:

```text
+-----+------+------+----------+----------+----------+
| RSV | FRAG | ATYP | DST.ADDR | DST.PORT |   DATA   |
+-----+------+------+----------+----------+----------+
|  2  |  1   |  1   | Variable |    2     | Variable |
+-----+------+------+----------+----------+----------+
```

`RSV` is `0x0000` and `FRAG` is `0x00`. Fragmentation is not implemented: a
conforming implementation may decline it, so outbound `FRAG` is always zero and
an inbound datagram with `FRAG != 0` is dropped. Dropping is the right answer
rather than an error, because it is indistinguishable from packet loss, which
QUIC already handles.

Overhead by address type:

| ATYP | DST.ADDR | header bytes | usable inner packet (from 1452) |
|---|---|---:|---:|
| `0x01` IPv4 | 4 | **10** | 1442 |
| `0x04` IPv6 | 16 | **22** | 1430 |
| `0x03` domain name | 1 + len | **7 + len** | 1445 − len |

**Which one we emit, and the DNS consequence.** The socket is constructed with
the QUIC destination as a `HostPortPair`, taken from
`QuicSessionKey::server_id()`. When that host is an IP literal it frames
`0x01`/`0x04`. When it is a name — the overwhelmingly common case — it frames
`0x03` and the **proxy** resolves it.

That choice is not an efficiency decision, it is a leak decision. Chromium's
SOCKS5 stream path never resolves the destination locally:
`SOCKS5ClientSocket` is documented as always passing a hostname so “the DNS
resolving is done proxy side”, and `HttpStreamFactory::Job` does no resolution
for a proxied connection. If the datagram path resolved the destination to fill
an IPv4/IPv6 header, every HTTP/3 request would emit a destination DNS query on
the host's resolver that the HTTP/1.1 and HTTP/2 paths through the same proxy do
not. A site can observe that directly — serve a unique hostname, watch which
resolver arrives at its authoritative server — so it is a page-observable
asymmetry between protocol versions on one proxy configuration, and adding it
would violate the rule against new observables. Domain-name framing removes it:
after this change the only name resolved locally for a proxied HTTP/3 request is
the proxy's own, which is exactly what the stream path already resolves.

The cost is a variable, larger header. For a 20-character hostname the overhead
is 27 bytes against IPv4's 10 — about 1.9% of a 1442-byte packet.
A hostname long enough to push the usable packet below QUIC's 1200-byte Initial
floor (len > 245) makes the capability unservable; the socket refuses the
association with `ERR_SOCKS_CONNECTION_FAILED` at construction rather than
starting a handshake that cannot complete. Under A2 that is the required
behaviour: a capability that cannot be served is declined, not advertised.

**The overhead is fixed for the socket's lifetime, and that is load-bearing.**
`QuicConnection` caches the writer's maximum packet size and only revises it
through MTU discovery, so an overhead that changed after the connection was
sized would leave QUIC emitting packets whose framed form exceeds the path MTU
— the black-hole failure of risk 4, arriving late and intermittently instead of
at connect time. The header length is therefore derived **once**, at
construction, from the destination form the socket was given:

| destination form | ATYP emitted | `overhead` |
|---|---|---:|
| IPv4 literal | `0x01` | 10 |
| IPv6 literal | `0x04` | 22 |
| host name, `len` bytes | `0x03` | 7 + len |

It is stored as a const member, every frame is built from that same member, and
`GetEncapsulation()` returns it. The destination form cannot change mid-session
because it comes from `QuicSessionKey::server_id()`, which is part of the
session key: a different destination is a different key and therefore a
different socket. The construction-time derivation makes that structural
guarantee explicit rather than relying on it, so a future caller that reuses a
socket for a second destination gets a wrong-address bug instead of a silent
MTU regression — and neither is reachable today.

**Write.** `Socket::Write` forbids modifying the caller's buffer, so the socket
copies header + payload into an internal buffer sized once to
`kMaxOutgoingPacketSize + kMaxHeader` and issues a single `Write` to the UDP
socket — one write per packet, matching the writer's expectation of exactly one
`Write` call. The return value is translated back to the caller's frame: a full
write returns `buf_len`, not `header + buf_len`, so `QuicChromiumPacketWriter`
does not see a short write. A short write of a datagram socket is not a
recoverable condition and is reported as `ERR_MSG_TOO_BIG`, which QUIC treats as
a write error.

**Read.** The deframing must not hand the QUIC reader a stale offset.
`QuicChromiumPacketReader::ProcessSingleDatagram` takes
`read_buffer_->span().subspan(datagram.offset, datagram.length)` on *its own*
buffer, so every offset returned must index the caller's buffer and must point
at inner-packet bytes.

Single `Read`: the socket reads into its internal buffer, validates and strips
the header, and copies the payload to the front of the caller's buffer. The
copy is unavoidable — the caller's API has no offset — and is bounded by one
packet.

`ReadMultiple`: the socket allocates one internal buffer and asks the UDP
socket for `slots` datagrams of `max_message_size + kMaxHeader` each, where
`slots = buf_len / max_message_size` is the caller's own capacity in payloads.
The internal length is raised to `kMinimumReadMultipleBufferSize` when that
product is smaller, because `UDPSocketPosix::ReadMultiple` `CHECK`s a 64 KiB
floor. Each returned datagram is validated, its header stripped, and its
payload appended to the caller's buffer at a packed offset; the metadata vector
records `{packed offset, payload length, 0}`. A payload that would not fit the
remaining capacity is dropped rather than truncated — truncation would corrupt
a packet, dropping is loss. Invalid and `FRAG != 0` datagrams are dropped the
same way.

If every datagram in a batch is dropped the socket **re-issues the read**
instead of returning an empty vector, because the reader does
`CHECK(!datagrams.empty())`. That loop is bounded by the relay's send rate, not
by the socket: each iteration requires a fresh datagram to have arrived.

## 5. Wiring, and what stays refused

Four gates in Chromium refuse QUIC through a non-QUIC proxy. Exactly the two
that are outright rejections are relaxed, and both only for a chain that
`IsQuicOverSocks5Chain()` accepts — a single-proxy chain whose one proxy is
`SCHEME_SOCKS5`:

| site | pristine behaviour | after |
|---|---|---|
| `http_stream_factory_job.cc` `DoInitConnectionImpl` | `using_quic && !is_direct && !Last().is_quic()` → `ERR_NO_SUPPORTED_PROXIES` | the same, unless the chain is a single-hop SOCKS5 chain |
| `http_stream_factory_job.cc` `DoInitConnectionImplQuic` | any non-direct chain containing a non-QUIC proxy → `ERR_NO_SUPPORTED_PROXIES` | the same, unless the chain is a single-hop SOCKS5 chain |
| `http_stream_factory_job_controller.cc` H3-from-DNS | gated on `proxy_info_.is_direct()` | **unchanged** — DNS-based H3 discovery stays direct-only |
| `QuicSessionPool::RequestSession` | non-direct → `ProxyJob` | a single-hop SOCKS5 chain takes `DirectJob`; every other non-direct chain still takes `ProxyJob` |

Leaving the third gate alone is deliberate. `require_dns_https_alpn` jobs
resolve the destination's HTTPS record, which is the DNS query §4 exists to
avoid. HTTP/3 over SOCKS5 is therefore reached the way it is reached on a
network without HTTPS-RR support: via `Alt-Svc` on a response that already came
through the proxy. Nothing about discovery becomes proxy-specific.

`DirectJob` needs one adjustment, and it is forced rather than chosen. The
socket must connect a TCP control connection to the proxy, and the only address
it is given is `ConnectAsync`'s. So for a SOCKS5 datagram chain `DirectJob`
resolves the **proxy's** host instead of the destination's:

- `DoResolveHost` issues the request for the proxy's `HostPortPair` (the
  `HostPortPair` overload, so plain A/AAAA — a proxy has no HTTPS record worth
  asking for) instead of `key_.destination()`.
- `peer_address` therefore becomes the proxy's endpoint, and
  `dns_resolution_start_time`/`end_time` measure the proxy's lookup. Both match
  the stream path, where `TransportConnectJob` resolves the proxy and that
  lookup is what Resource Timing's `domainLookupStart`/`domainLookupEnd`
  already report for a proxied request.
- Cross-origin **IP pooling is skipped** for this path. The resolved addresses
  are the proxy's, and pooling on them would key sessions by proxy identity.
- **DNS aliases are dropped** for this path. They would be the proxy's
  canonical names, which are not the destination's and have no business
  reaching `HttpResponseInfo`.

Everything that identifies the server is untouched: `QuicSessionAliasKey`'s
destination, `QuicSessionKey::server_id()`, SNI, certificate verification and
session keying all still name the real destination, and the proxy chain is
already part of `QuicSessionKey`, so a session through a proxy can never be
reused for a direct request or for a different proxy.

`GetPeerAddress()` returns the address `ConnectAsync` was given — the proxy's
endpoint — not the relay's. This is required for consistency, not cosmetic:
`QuicChromiumPacketReader` reports `socket_->GetPeerAddress()` as each packet's
peer address, and `QuicConnection` compares that against the address the
connection was built with. Reporting the relay's port instead would look like a
server address change on every packet and trigger migration logic. The relay
endpoint stays inside the socket.

### Migration and probing

`QuicChromiumClientSession` builds its own sockets for port migration, network
migration and multi-port paths. All three go through `CreateSocket()` with the
session key, which is not tidiness: a probe socket built without the key would
be a plain UDP socket connected to `peer_address()`, and for a proxied session
that address is the proxy's — so the probe would go straight at the proxy's IP
on a UDP port nothing is listening on, and on any future refactor that used the
destination instead, straight past the proxy. Routing every construction
through one function is what makes the leak unrepresentable.

What each one then does:

- **Port migration** opens a second association. The relay assigns it a new
  source port, so the destination sees a path change and validates it, which is
  what port migration is for. It costs a TCP connection and a handshake to the
  proxy, which is slower than a direct rebind but not different in kind.
- **Network migration** changes the client's own address. RFC 1928 §7 requires
  the relay to drop datagrams from any source other than the associated
  client's recorded address, so the new path's probe is discarded, validation
  fails, and QUIC keeps the old path or closes. It fails closed; no datagram
  escapes the proxy.
- **Multi-port** has one synchronous construction path, used when
  `kAsyncMultiPortPath` is disabled. It calls `ConfigureSocket()`, whose
  synchronous `Connect()` this socket answers with `ERR_NOT_IMPLEMENTED`, so
  multi-port context creation returns without a context. That is a clean
  decline of an off-by-default feature, not an error path.

## 6. ECN, TOS, DF and packet size

**ECN is withdrawn, honestly.** A SOCKS5 relay re-originates the inner packet;
RFC 1928 gives it no way to carry the client's ECN codepoint to the destination
or the destination's back to the client. `QuicChromiumPacketWriter::SupportsEcn()`
returns `true` unconditionally upstream, and leaving it true would have QUIC mark
packets ECT and then fail its own ECN validation when no counts came back —
advertising a capability the transport cannot serve, which is an A2 defect.

So `DatagramClientSocket` gains one query with a default that preserves upstream
behaviour for every existing implementation:

```cpp
struct Encapsulation {
  size_t overhead = 0;       // bytes prepended to every datagram
  bool preserves_ecn = true; // do the payload's ECN bits reach the far end
};
virtual Encapsulation GetEncapsulation() const { return Encapsulation(); }
```

`SOCKS5DatagramClientSocket` returns `{header size, false}`. The writer then
reports `SupportsEcn()` as the socket's `preserves_ecn`, so QUIC never marks a
relayed packet and never waits for feedback that cannot arrive. Receive side is
symmetric: `SetRecvTos()` succeeds without enabling `IP_RECVTOS`, `GetLastTos()`
reports `{DSCP_DEFAULT, ECN_NOT_ECT}`, and every datagram's metadata `tos` is
`0`. Enabling it would report the *relay-to-client* hop's TOS as if it were the
destination's, which is worse than reporting none. A path that returns no ECN
counts is the majority of the real Internet — ECN bleaching by middleboxes is
ordinary — so this is in-distribution rather than distinctive.

**DF passes through.** `SetDoNotFragment()` forwards to the UDP socket and its
result, including `ERR_NOT_IMPLEMENTED`, is returned verbatim; the pool already
tolerates that one error. DF then applies to the outer datagram on the
client-to-relay hop, which is the hop whose MTU the client can discover. The
relay-to-destination hop's DF is the proxy's choice and is not observable here;
claiming otherwise would be invention.

**Packet size follows the overhead.** `GetMaxPacketSize()` returns
`kMaxOutgoingPacketSize` (1452, verified in
`net/third_party/quiche/src/quiche/quic/core/quic_constants.h`) minus the
socket's `overhead`, so MTU discovery tops out where the outer datagram still
fits 1500 bytes. Without that subtraction a probe at 1452 would produce a
1462-byte payload — 1510 bytes on the wire over IPv6 — and be silently dropped
by the first 1500-MTU hop, which QUIC would read as a path-MTU black hole.

The initial size is unaffected: quiche starts at `kDefaultMaxPacketSize` = 1250
(`kDefaultMaxPacketSizeForTunnels` = 1350 applies to MASQUE, not here), so the
first flight is 1250 + 10…37 bytes of header and fits every ordinary path. The
1200-byte Initial-packet floor is never at risk except for the pathological
hostname length handled in §4.

## 7. Failure and fallback

| failure | detected | result |
|---|---|---|
| proxy refuses TCP | control connect | `ERR_PROXY_CONNECTION_FAILED` from the transport |
| bad credentials, or auth required and none configured | greet/auth reply | `ERR_SOCKS_CONNECTION_FAILED` |
| proxy does not implement UDP ASSOCIATE (`REP 0x07`) | ASSOCIATE reply | `ERR_SOCKS_CONNECTION_FAILED` |
| malformed reply, unusable relay address | ASSOCIATE reply | `ERR_SOCKS_CONNECTION_FAILED` |
| hostname too long to frame (§4) | construction | `ERR_SOCKS_CONNECTION_FAILED` |
| control connection closes later | latched control read | `ERR_CONNECTION_CLOSED` on the next datagram operation |
| association silently drops datagrams | QUIC handshake timeout | QUIC's existing timeout path |

Every one of these fails the QUIC session attempt. `HttpStreamFactory` then
resumes the non-alternative job, which uses the same SOCKS5 proxy over TCP, and
the alternative service is marked broken by the machinery that already handles a
UDP-blocked network. No new retry loop, no new cache, and no direct socket: the
destination is never contacted outside the proxy.

## 8. Risk register — the observables a naive implementation adds

| # | observable | naive behaviour | what this design does |
|---|---|---|---|
| 1 | **Destination DNS query** | Resolve the destination to fill an IPv4 header, so HTTP/3 leaks a resolver the HTTP/2 path through the same proxy does not. A site can see this from its authoritative DNS. | Domain-name framing (§4). Only the proxy's name is resolved locally, matching the stream path exactly. |
| 2 | **Resource Timing DNS phase** | Destination lookup timing appears in `domainLookupStart`/`domainLookupEnd` for a proxied request, where the stream path reports the proxy's lookup. | `DirectJob` resolves the proxy, so the reported phase measures the same lookup the stream path measures (§5). |
| 3 | **Fallback-to-TCP timing** | A per-request UDP-ASSOCIATE probe that fails adds a measurable delay before the TCP job, on every request, forever. | The failure fails the QUIC job, and Chromium's existing broken-alternative-service marking suppresses further attempts — identical to a UDP-blocked network, which is common. No Apostate-specific cache or timer (§7). |
| 4 | **Reduced MTU / altered packet size** | Leave `GetMaxPacketSize()` at 1452, so probes become 1462-byte payloads that black-hole; or subtract nothing and let discovery oscillate. | The socket publishes its overhead and the writer subtracts it (§6). Max inner packet becomes 1430–1442 for IP framing and 1445 − len for names — a reduced-MTU path, which VPN, CLAT and PPPoE users produce in quantity. |
| 5 | **Initial packet size** | An overhead large enough to push the inner packet under 1200 breaks the handshake, or is papered over by shrinking the Initial below the spec floor. | Initial stays at quiche's 1250; the only case that cannot hold the floor (hostname > 245 bytes) declines the capability instead of shrinking it (§4). |
| 6 | **Absent ECN** | Keep `SupportsEcn() == true`, mark packets ECT(0), get no counts back, fail ECN validation — a distinctive advertise-then-retract pattern. | `SupportsEcn()` becomes `false` through a relay, receive TOS is reported as `ECN_NOT_ECT` consistently, so QUIC never advertises what the path cannot carry (§6). |
| 7 | **Retransmission behaviour** | Nothing to do: the relay adds a hop, so RTT and jitter differ from direct. | Untouched by design. QUIC's loss recovery runs unmodified on the measured RTT; the relay's added latency is what any proxy user has, and a proxy user who looked direct would be the anomaly. No timer is adjusted to hide it. |
| 8 | **Novel keep-alive on the control connection** | Send periodic bytes to hold the association open, giving the proxy a fingerprint no standard client emits. | Nothing is ever sent after the ASSOCIATE reply; the connection is held open and read-watched only (§2). |
| 9 | **Packet injection from the relay port** | Bind the UDP socket and accept datagrams from any source, since the relay's address was “learned”. | The UDP socket is `Connect`ed to the relay, so the kernel filters by source exactly as on the direct path, and no state is derived from the header's address (§3). |
| 10 | **Credential in a session key** | Put credentials on `ProxyServer` so the socket can reach them, and watch them appear in NetLog and pool keys. | Credentials are read from the launch envelope at socket construction and held nowhere else; keying is unchanged. |

Residual and reported, not mitigated: a destination server sees the proxy's
exit address, a relayed RTT, and a maximum datagram slightly smaller than an
unproxied client's. Those are properties of using a proxy at all. The proxy
operator sees an idle control connection and a UDP association, which is what
UDP ASSOCIATE is.

## 9. Verification contract

Stream support is not release-complete until all of these are separately
verified:

- V0/V1 patch application and source checks pass.
- Native unit coverage exercises HTTP, HTTPS, SOCKS4, SOCKS5, absent
  credentials, malformed credentials, and credential redaction.
- A built Chromium binary reaches local authenticated and unauthenticated
  stream fixtures without exposing credentials in argv or diagnostics.

Datagram support adds:

- Unit coverage for `SOCKS5DatagramClientSocket`: the unauthenticated and
  authenticated handshakes byte-for-byte, `REP 0x07` refusal, every
  relay-address substitution rule, write framing for all three ATYP forms,
  read deframing through both `Read` and `ReadMultiple`, `FRAG != 0` and
  malformed-header drops, packed offsets across a multi-datagram batch,
  control-connection closure, and the reported `Encapsulation`.
- A built binary completing an HTTP/3 request through a local SOCKS5 proxy
  that implements UDP ASSOCIATE, and falling back to TCP against one that
  answers `REP 0x07`.
- A packet capture on the host showing no datagram to the destination and no
  destination DNS query, only proxy traffic.
- A packet capture during a WebRTC connectivity check showing every datagram
  leaving through the proxy association and none to the peer directly, and no
  UDP socket created at all under a non-relayable proxy.

Package-level GeoIP proxy transport is separate from native Chromium proxy
authentication. It uses Node's request APIs and proxy agents only to resolve
locale/timezone before launch; that transport does not prove browser-native
proxy authentication.
