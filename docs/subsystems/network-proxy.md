# Subsystem 10 — Network & Proxy Transport

Design for authenticated proxy support and QUIC through SOCKS5.

Entry points marked **verified** were confirmed against Chromium 152.0.7977.82.

## Why this is P0 rather than a feature

Real Chrome on a residential connection negotiates QUIC. Chromium attempts it
only when the proxy is direct — `net/http/http_stream_factory_job_controller.cc:932`,
**verified**: `session_->IsQuicEnabled() && proxy_info_.is_direct()`. So every
proxied session is TCP-only, and against a server that offers HTTP/3 the absent
QUIC attempt is a class-level signal that nothing at the JavaScript layer
explains.

That makes UDP ASSOCIATE a coherence requirement under axiom A2, not a
convenience. A browser that is perfect on every scriptable surface and never
speaks QUIC has simply moved its tell down the stack.

## What Chromium has and does not have

Establishing this first, because the temptation is to assume an existing
abstraction can be reused.

| | State |
|---|---|
| SOCKS5 authentication | **Absent.** `net/socket/socks5_client_socket.h:31` — *"Currently no SOCKSv5 authentication is supported."* |
| Proxy credentials in config | **Absent.** `net/base/proxy_server.h` — `ProxyServer` is documented `{type, host, port}` and immutable. No credential field exists. |
| SOCKS5 UDP ASSOCIATE | **Absent.** Only `kTunnelCommand = 0x01` is defined (`socks5_client_socket.cc:31`). No BIND, no UDP. |
| `ProxyDatagramClientSocket` | **Does not exist** anywhere in `net/socket`. |
| `QuicProxyClientSocket` | Exists — but is the *opposite* direction. It tunnels a **stream** over QUIC-to-the-proxy for IP Protection. It does not relay origin datagrams. |
| `ProxyChain` with `SCHEME_QUIC` | Exists (`net/base/proxy_chain.h:243`) — means *the proxy speaks QUIC*, not that a proxy carries origin QUIC. |
| RFC 9298 CONNECT-UDP encoding | Exists in QUICHE: `net/third_party/quiche/src/quiche/common/masque/connect_udp_datagram_payload.h`. Different wire format from RFC 1928, but a useful reference for the encapsulation pattern. |

So there is no shortcut. The datagram-through-proxy path has to be built.

## 1. Credentials

Parse `user:pass@` at the command-line / manual-config boundary, percent-decoding
userinfo. Keep credentials **beside** `ProxyServer`, never inside it:

```cpp
struct ProxyCredentials {
  std::string username;
  std::string password;
};

struct ConfiguredProxy {
  ProxyServer endpoint;
  std::optional<ProxyCredentials> credentials;
};
```

The leak surface is wider than NetLog and is the reason for the separation.
`ProxyServer` and `ProxyChain` values reach NetLog events, `chrome://net-export`
dumps, socket pool group keys, session cache keys, error strings and histograms.
A credential inside `ProxyServer` reaches all of them. Look up credentials by
proxy-chain identity from a store owned by the `NetworkContext`, so the
serialisable identity stays credential-free by construction.

This also matters under "never introduce a new observable": credentials landing
in a cache key would partition connection reuse differently from stock Chrome,
which is itself a behavioural difference.

## 2. HTTP and HTTPS proxy auth

The cheap half. Do not write a second HTTP auth implementation — supply inline
credentials to the existing `HttpAuthController` as a high-priority credential
source, so a 407 resolves against configured credentials instead of raising the
browser auth UI.

```text
proxy configured with credentials
  → CONNECT / request
  → 407 + Proxy-Authenticate
  → existing HttpAuthController
  → inline credentials for the selected proxy
  → retry with Proxy-Authorization
```

Authenticated `CONNECT` for HTTPS and WebSocket falls out of this without
special handling.

## 3. SOCKS5 RFC 1929

Two exact insertion points, both **verified**.

The greeting is a hardcoded constant at `net/socket/socks5_client_socket.cc:255`:

```cpp
0x05, 0x01, 0x00};  // no authentication
```

And `DoGreetReadComplete` at line 326 hard-rejects anything else:

```cpp
if (read_data[1] != 0x00) {
  net_log_.AddEventWithIntParams(NetLogEventType::SOCKS_UNEXPECTED_AUTH, ...);
  return ERR_SOCKS_CONNECTION_FAILED;
}
```

So today a server that *selects* username/password fails the connection outright.
Both sites change:

```text
credentials absent:   VER=5 NMETHODS=1 METHODS=[0x00]
credentials present:  VER=5 NMETHODS=2 METHODS=[0x00, 0x02]
```

On `0x02`, run RFC 1929 (`01 ULEN USERNAME PLEN PASSWORD`) then rejoin the
existing CONNECT handshake. The state machine gains an authentication pair:

```text
STATE_GREET_WRITE / _COMPLETE
STATE_GREET_READ  / _COMPLETE
STATE_AUTH_WRITE  / _COMPLETE     <- new
STATE_AUTH_READ   / _COMPLETE     <- new
STATE_HANDSHAKE_WRITE / _COMPLETE
STATE_HANDSHAKE_READ  / _COMPLETE
```

Note `kGreetReadHeaderSize` is a fixed-size read; the auth reply is a separate
fixed 2-byte read, but username and password are variable and need their own
buffer handling rather than reuse of the greet buffer.

## 4. SOCKS5 UDP ASSOCIATE

The substantial piece. Add a datagram transport beside the stream proxy socket,
and keep QUIC ignorant of SOCKS:

```text
QUIC
 │  DatagramClientSocket-shaped API
 ▼
SOCKS5DatagramClientSocket
 ├── TCP control connection — auth + UDP ASSOCIATE, held open for the lifetime
 │                            of the association
 └── UDP relay socket      — RFC 1928 encapsulation to BND.ADDR:BND.PORT
```

Per-datagram header, `FRAG=0` only — fragmented SOCKS UDP is rare and widely
unimplemented:

```text
RSV RSV FRAG ATYP DST.ADDR DST.PORT DATA
00  00  00   ...                    <- the QUIC packet, unmodified
```

Receive path validates `RSV` and `FRAG`, decodes the address tuple, strips the
header and hands the original packet up.

Route selection becomes roughly:

```cpp
if (proxy.scheme() == SCHEME_SOCKS5 && request_transport == DATAGRAM)
  return CreateSocks5UdpAssociation(...);
```

The control connection's lifetime is load-bearing: per RFC 1928 the association
ends when the TCP control connection closes, so it must outlive the QUIC session
and its teardown must tear down the association.

### DNS

Chromium resolves proxy-side for SOCKS5 by design — `socks5_client_socket.h:37`,
**verified**: *"we will always pass it a hostname. This means the DNS resolving
is done proxy side."* Preserve that for UDP by sending `ATYP=DOMAINNAME` with the
hostname, avoiding a DNS leak and keeping TCP and QUIC semantically identical.
Note the 255-byte hostname cap enforced at line 260 applies equally.

### Peer address, migration, and the bypass hazard

This was recorded as an open question needing a prototype. Reading the pinned
tree settled the architecture; what remains open is empirical rather than
structural.

Chromium's QUIC reconnects on network change through
`QuicChromiumClientSession::Migrate(new_network, ToIPEndPoint(connection()->peer_address()), ...)`
— **verified**, `net/quic/quic_chromium_client_session.cc:2451`. Migration
therefore targets whatever the connection believes its peer to be.

**That makes the choice of peer address a security decision, not a modelling
one.** If the QUIC connection's peer address were the origin, then on the first
network change Chromium would open a fresh socket **directly to the origin**,
bypassing the proxy entirely and leaking the real client address. Every network
transition would silently deproxy the session.

So the peer address must be the relay's `BND.ADDR:BND.PORT`. The QUIC session
believes it is talking to the relay, which is true at the transport layer, and
the origin lives inside the SOCKS datagram header where it belongs. Migration
then reconnects to the relay, which is the correct behaviour.

The substitution point follows: `QuicSessionPool::CreateSocket` and
`ConnectAndConfigureSocket` (`net/quic/quic_session_pool.h:478,437`) — **verified**
— are the single factory through which both initial connection and migration
obtain a `DatagramClientSocket`. Substituting there covers migration for free
rather than requiring a second code path.

One consequence needs handling rather than discovering later. RFC 1928 binds a
UDP association to the client address and port given in the request, and the
association lives only as long as its TCP control connection. On migration the
client's source address changes, so the replacement socket needs a **new
association** — a new control connection and a new UDP ASSOCIATE — not merely a
new UDP socket. A `Socks5DatagramClientSocket` that establishes its association
during `Connect()` gets this right automatically, precisely because migration
goes through the same factory.

**Still empirical, and still wants a prototype:** whether relays in practice
tolerate association churn across migrations, and what the added handshake
latency costs on transition. Neither blocks the design.

## Target behaviour

```text
HTTP/1.1 through SOCKS5        CONNECT
HTTP/2   through SOCKS5        CONNECT
HTTP/3   through SOCKS5        UDP ASSOCIATE

HTTP     through HTTP proxy    Proxy-Authorization
HTTPS/H2 through HTTP proxy    authenticated CONNECT
HTTP/3   through HTTP proxy    not initially
```

HTTP/3 through an HTTP proxy is the standards-track CONNECT-UDP case, and QUICHE
already carries the RFC 9298 payload encoding, so it is a cheaper follow-on than
its absence here suggests.

## Files expected to change

```text
net/proxy_resolution/     command-line and manual proxy parsing; config representation
net/base/                 ConfiguredProxy / credential store keyed by chain identity
net/socket/               socks5_client_socket: RFC 1929 auth
                          new: socks5 datagram socket + UDP association
net/http/                 inline credentials into HttpAuthController
net/quic/, net/http/      H3 job/session creation: proxied datagram transport
services/network/         carry credentials from --proxy-server into NetworkContext
```

## Also owned by this subsystem

Proxy-induced observables that are not protocol features, and which a working
proxy implementation will otherwise expose: DNS, connect and SSL timings visible
through Resource Timing; `Proxy-Connection` leakage; proxy cache headers. These
are separate ledger rows and do not block the transport work.
