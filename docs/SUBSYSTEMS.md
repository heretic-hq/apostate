# Subsystem map

Patch-ownership units. One owner per subsystem, because two patches editing the
same file from different premises is how a fork becomes incoherent.

Entry points marked **verified** were confirmed against Chromium source at the
pinned revision. Everything else is a starting point to be confirmed, not a
finding. Line numbers are advisory; the symbol is authoritative.

The purpose of this document is to make the mapping work dividable. Each
subsystem below states what it owns, where to start, what trap is known to be
waiting in it, and what it must produce.

---

## Why `is_source_of_truth` is the field that matters

WebGL's renderer string is the worked example, and it generalises.

Two independent emitters produce it:

- `gpu/config/gpu_info_collector.cc:607` — **verified**. `gpu_info->gl_renderer
  = GetGLString(GL_RENDERER)`, in the GPU process. Feeds `GPUInfo`, which
  drives `chrome://gpu` and the GPU blocklist.
- `third_party/blink/renderer/modules/webgl/webgl_rendering_context_base.cc:4214`
  — **verified**. `case WebGLDebugRendererInfo::kUnmaskedRendererWebgl:` returns
  `String(ContextGL()->GetString(GL_RENDERER))`, read live from the driver
  through the command buffer.

**The second is what JavaScript reads.** Patching only the first changes
`chrome://gpu` and leaves every detector's value untouched — a patch that looks
correct, passes casual inspection, and leaks. Patching only the second leaves
`GPUInfo` disagreeing with WebGL, which is a coherence break of its own.

So every ledger row needs its emitter identified as a *source* rather than a
waypoint, and both must be handled where two exist. Note also line 4107: the
*masked* `GL_RENDERER` already returns the constant `"WebKit WebGL"`, so the
masked and unmasked paths are separate and must not be conflated.

---

## 1. Profile Loader & Flag Plumbing

**Build this first.** Every other subsystem depends on it, and it is the one
place where a mistake is invisible in the surface it fixes and fatal elsewhere.

Owns: how a profile reaches the renderer, GPU and network processes without
becoming observable itself.

- `chrome/browser/chrome_content_browser_client.cc:2899` — **verified**.
  `ChromeContentBrowserClient::AppendExtraCommandLineSwitches` is where the
  browser process decides what each child is launched with.

Trap: a child process can read its own command line. Any switch carrying profile
data is readable by anything running in that process, and switch *presence* is
itself a signal even when the value is innocuous. Decide deliberately between
command-line switches, a shared memory region, and a mojo interface, and record
why — this decision is inherited by every patch downstream.

Must produce: the plumbing design, the set of processes needing profile data,
and the argument for why the chosen mechanism adds no observable.

## 2. Automation Guard & CDP Surface

Owns: `navigator.webdriver`, CDP-observable side effects, the artefacts that
distinguish an automated browser from a driven one.

Trap: much of what is detected here is behavioural rather than a value —
`Runtime.enable` side effects, console object mutation, anti-debugger timing. A
row that names a value to change is probably describing a symptom.

## 3. Navigator, Platform Identity & Client Hints

Owns: the user agent, `navigator.platform`, `userAgentData` low and high entropy
values, and every Client Hint header.

- `third_party/blink/renderer/core/frame/navigator_concurrent_hardware.cc:11` —
  **verified**. `hardwareConcurrency()` returns
  `base::SysInfo::NumberOfProcessors()` directly.

Trap: the UA string, `userAgentData`, Client Hints sent on the wire, and the
platform value must all agree, and they are produced in different places. This
subsystem owns more coherence edges than any other.

## 4. Screen, Window & Display Geometry

Owns: `screen.*`, available area, device pixel ratio, window chrome insets.

Trap: our own capture shows a real MacBook reporting `1728×1117` screen with
`1728×1001` available — a 116px inset from menu bar and dock. Geometry must be
internally consistent with the claimed OS and its window furniture, and the
deltas are platform-specific.

## 5. GPU, Graphics & Canvas

Owns: WebGL1/2, WebGPU, canvas 2D readback, the ANGLE backend, and the two
renderer-string emitters above.

Trap: the whole of the section above. Additionally, canvas output is a genuine
render — the value follows from the graphics stack actually in use, so making it
correct is a configuration problem before it is a patching problem.

## 6. Audio Synthesis & Media Capabilities

Owns: `OfflineAudioContext` render output, `AudioContext` properties, codec
support.

Trap: our capture measured `baseLatency` and `outputLatency` moving between
reads on one machine while the render stayed byte-identical. Latency is device
*state*, the render is device *identity*, and only the second is a fingerprint.

## 7. Fonts & Text Metrics

Owns: the installed font set, fallback behaviour, text measurement, sub-pixel
layout.

Trap: fonts drive canvas output, `getClientRects` and `measureText`
simultaneously. A font set that does not match the claimed OS produces
inconsistency across three subsystems at once, and it cannot be fixed in any of
them.

## 8. V8, ICU, Intl, Timezone & Locale

Owns: timezone, locale, `Intl` resolved options, `Math` transcendental results.

- `services/device/time_zone_monitor/time_zone_monitor.cc:51` — **verified**.
  Browser-process default via `icu::TimeZone::adoptDefault`.
- `third_party/blink/renderer/core/timezone/timezone_controller.cc:236` —
  **verified**. The Blink-side override; the same mechanism CDP's
  `Emulation.setTimezoneOverride` drives.
- `base/i18n/icu_util.cc:320` — **verified**. `InitializeICU`, reached in every
  process type.

Corrected: an earlier version of this section cited `icu_util.cc:332` as a third
override layer. That `adoptDefault` call is inside a Fuchsia branch and compiles
to nothing on macOS and Windows — the fourth time in this project the obvious
emitter turned out to be the wrong one, and the only time it was the arbiter's
error rather than a shard's.

Trap: the browser-side monitor is the source and pushes on client registration
rather than only on change, so a renderer spawned later still converges. A worker
cannot diverge from its parent within a process, because ICU's default zone is a
single file-static; what can diverge is a stale per-isolate V8 DateCache, which
is why the controller walks all worker threads and why a patch that skips that
step is caught by a two-line worker probe. Timezone must also agree with the
proxy exit IP — an edge owned jointly with §10.

Note also that ICU's data is compiled into the binary and file access is
restricted to packages at startup, so the zone list, calendar list and every DST
rule are properties of our build rather than of the host. Only the zone *name*
is read from the host environment.

## 9. WebRTC & Device Enumeration

Owns: ICE candidates, IP handling policy, mDNS obfuscation, `enumerateDevices`.

- `chrome/common/pref_names.h:1111` — **verified**. `kWebRTCIPHandlingPolicy`.

Trap: WebRTC leaks the real IP independently of the proxy. The candidate set has
to agree with the claimed network position, which makes this jointly owned with
§10 rather than independent of it.

## 10. Network & Proxy Transport

Owns: proxy support, TLS/HTTP2/QUIC shape, and proxy-induced observables. This
is the axis `resources/surfaces.json` almost entirely omits, and it carries most
of the parity gap against comparable tools.

- `net/socket/socks5_client_socket.h:31` — **verified**. The comment reads
  `// Currently no SOCKSv5 authentication is supported.` The state machine is
  `GREET → HANDSHAKE` with no authentication sub-negotiation, and the only
  command constant is `kTunnelCommand`. RFC 1929 username/password auth means
  adding states; BIND and UDP ASSOCIATE do not exist at all.
- `net/base/proxy_server.h:33,94` — **verified**. `ProxyServer` is documented as
  `{type, host, port}` and immutable. **There is no credential field**, so SOCKS
  credentials have nowhere to live today. HTTP proxies get credentials through
  the 407 challenge path, which has no SOCKS equivalent.
- `net/http/http_stream_factory_job_controller.cc:932` — **verified**. QUIC is
  attempted only when `proxy_info_.is_direct()`.

Trap, and it is the important one: **that last line means any proxied session
silently loses HTTP/3.** Real Chrome negotiates QUIC, so a server offering it
sees an absence that no amount of JavaScript-layer correctness explains. UDP
ASSOCIATE is therefore not a feature request but a coherence requirement, and
this subsystem should be scoped accordingly.

Also owns proxy-induced observables that are not protocol features: DNS,
connect and SSL timings visible through Resource Timing, `Proxy-Connection`
leakage, and proxy cache headers.

## 11. Storage, Quota & Permissions

Owns: `storage.estimate`, quota, permission states, incognito detection.

Detail: `subsystems/profile-persistence.md`.

Trap: our capture shows `Notification.permission` and the Permissions API as
separate reads. They must agree, and disagreement is a known headless tell.

Trap: a non-persistent automation context *is* an off-the-record profile —
`Target.createBrowserContext`, which Playwright's `newContext()` calls, goes
through `GetOffTheRecordProfile`. Off-the-record quota is derived from physical
memory rather than disk, so before patch 0022 it reported the host's RAM and
bypassed `navigator.deviceMemory` entirely. Note the 10 GiB cap hides this on
hosts above roughly 50 GiB, which is why it survived several captures
unnoticed.

## 12. Speech & Sensors

Owns: `speechSynthesis` voices, sensor availability, battery.

Trap: the voice list is strongly OS- and locale-specific and is one of the
easier ways to contradict a claimed platform.

---

## What a mapping shard must return

Per assigned surface, JSON validating against `ledger/schema/surface.schema.json`:

1. The observable, written as a detector reads it.
2. Every emitter found, each marked `is_source_of_truth` true or false, with the
   evidence that settled it. Where a value passes through several layers, all of
   them, ordered.
3. A verdict — `spoof`, `inherit`, `suppress`, `out-of-scope`, `escalate` —
   with a cited reason for `out-of-scope` and `inherit`.
4. Coherence edges to any surface that must agree with this one.
5. Evidence at T0 or T1 to close a row. T2 opens rows and never closes them.

Rules: never write the ledger directly, emit to `ledger/inbox/`. When uncertain,
`escalate` — a dropped surface costs a detection, a false keep costs one review.
