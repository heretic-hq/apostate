# Profile and launch specification

This document is the public contract for Apostate profiles and launches. The
machine-readable profile schema is [`config/profile.schema.json`](../config/profile.schema.json).
The schema is authoritative for accepted profile fields; this page defines how
those fields are selected, combined, and reported.
[`docs/FINGERPRINTS.md`](FINGERPRINTS.md) is the composition model that produces
them, and this page defers to it.

A profile is a set of native inputs for a browser process. It is not a
JavaScript wrapper, a collection of per-request overrides, or a claim that the
host is physically the device being presented.

## Versions and identity

The release baseline uses these version boundaries:

- package version: `0.1.0`
- Chromium version: `152.0.7977.83`
- profile catalogue version: `2`
- profile schema version: `3`

Catalogue version 2 replaced the fourteen-family catalogue with a composition
model. A profile is no longer selected from a list: it is composed from an
**anchor** (a measured GPU capability cluster, taken atomically) and the
**dispersion** axes under `resources/profiles/dispersion/`, each of which is an
enumerated table of options observed on real systems. The retired families and
their compatibility-acceptance record are gone, and `catalogue.json` records why
in `retired_model`.

A seed is a deterministic selector. It is not a per-call randomizer and does not
invent a physical machine. Composition uses this identity tuple:

```text
(profile_schema_version, catalogue_version, Chromium build,
 fingerprint_platform, fingerprint)
```

The tuple is hashed with SHA-256 under the domain string `apostate/fp/v1`. It
must not use language-runtime hash behavior, process-randomized hashes, or
unordered map iteration. Each axis then draws from a substream keyed by its own
label, so adding an axis or changing one axis's option list cannot shift any
other axis's choice. Weighted selection takes the high 64 bits of a draw as a
`u64` and picks index `(u64 * total_weight) >> 64` against cumulative weights in
file order: no modulo, no rejection loop, no floating point.

The same tuple always produces the same result; changing the schema, catalogue,
or browser build creates a new identity rather than silently remapping an
existing one.

There is no per-call fingerprint randomness. Repeated reads and fresh launches
with the same resolved profile are expected to be stable. Variation happens at
the profile-composition boundary.

## The default launch

A launch with no arguments composes a profile. Every launch draws a fresh seed
from OS entropy and composes a fresh coherent device, whether or not
`--user-data-dir` is set. Nothing about the identity is written to disk.

| Launch | Seed source | Result |
| --- | --- | --- |
| no arguments | fresh OS entropy, per launch | a new device every launch |
| `--fingerprint=<seed>` | the argument | the same device anywhere, every launch |
| `--fingerprint=host` | none | no composition; the host's own values |

There is no persisted seed. Earlier builds wrote one to the user-data directory
and reused it, so relaunching the same directory reproduced the identity. That
file is gone and the precedence step that read it is gone with it. A stable
identity comes from `--fingerprint=<seed>`, which reproduces on another machine
as well, which a file never did.

The practical consequence for automation: Playwright's
`launch_persistent_context` reuses one user-data directory by design, and under
this default it presents a different device on every launch. Pass
`--fingerprint=<seed>` for returning-visitor behaviour.

The profile is fully materialized before the first renderer starts, and nothing
inside the session varies.

## Canonical launch configuration

CLI, Python, and Node integrations share one logical configuration. The shared
wire representation uses snake_case:

```json
{
  "fingerprint": 12345,
  "fingerprint_platform": "windows",
  "profile": null,
  "locale": null,
  "timezone": null,
  "geoip": true,
  "proxy": null,
  "headless": true,
  "user_data_dir": null,
  "args": []
}
```

Field meanings:

| Field | Meaning |
| --- | --- |
| `fingerprint` | Seed for the deterministic compositor: an integer, or any printable ASCII up to 512 bytes. `null` requests the default, a fresh seed per launch. The literal `host`, or `off`, `false`, `0`, `disable`, `disabled`, turns composition off. |
| `fingerprint_platform` | Platform persona: `windows`, `macos` or `linux`. `null` uses the host's own platform. It changes OS identity, client hints, fonts, voices, locale, screen geometry and hardware buckets. It does not move the GPU cluster; see below. |
| `profile` | Explicit profile file or inline profile object. `null` means no explicit profile. |
| `locale` | Explicit locale or Accept-Language policy. `null` means use the precedence rules below. |
| `timezone` | Explicit IANA timezone. `null` means use the precedence rules below. |
| `geoip` | When `true`, resolve locale and timezone from the observed network exit before launch. |
| `proxy` | Proxy URL and credentials, if any. Credentials are used for launch and are never written to diagnostics or logs. |
| `headless` | Whether Chromium is launched headless. |
| `user_data_dir` | Persistent profile directory for cookies, storage and history. It does not hold the identity. `null` selects the integration's temporary-directory policy. |
| `args` | Additional Chromium arguments. Integrations must preserve the canonical profile and localization semantics when adding them. |

Package APIs may expose idiomatic camelCase aliases, but aliases map to this
same configuration and must not create a second profile model. For example,
`fingerprintPlatform` maps to `fingerprint_platform` and `userDataDir` maps to
`user_data_dir`.

## Selection precedence

Profile selection is resolved in this order, from strongest to weakest:

```text
explicit profile file
  > inline profile object
  > host mode
  > per-field override switches
  > fingerprint seed plus platform persona
  > a fresh seed drawn for this launch
```

An explicit profile file is validated before launch and **bypasses
composition**: its coherence and servability are the author's responsibility,
not the catalogue's, and the resolver reports that as a warning.

Network localization has a separate precedence chain:

```text
explicit locale/timezone
  > GeoIP-derived locale/timezone
  > composed locale/timezone
  > host values
```

With `geoip: true`, the lookup is performed through the configured proxy, or
through the direct network when no proxy is configured. The result is fixed for
the process before Chromium starts. A timeout or lookup failure is reported as
an error or warning and must not silently invent `UTC` or `en-US`.

The Node adapter performs the built-in GeoIP request through the configured
HTTP(S), SOCKS4, or SOCKS5 proxy. Chromium authentication is a separate native
path: the credential-free `--proxy-server` endpoint does not authenticate by
itself. When proxy credentials are supplied, the launcher carries them in an
ephemeral `--apostate-profile` envelope beside the validated `device_profile`;
the native loader keeps them out of the device-profile schema, diagnostics,
and persisted artifacts. They do enter Chromium's in-memory `HttpAuthCache`,
exactly as interactively entered proxy credentials do: that cache is
per-`NetworkContext`, never written to disk, and not page-visible, and
populating it is what allows preemptive authentication. Skipping it forces a
407 round trip on every new connection and leaves multi-round schemes
unfinished. UDP over SOCKS5 UDP ASSOCIATE, including proxied QUIC/HTTP3, is
supported natively; WebRTC UDP/STUN/TURN is not offered.
`humanize: true` is rejected rather than accepted as a no-op.

Geography controls locale and timezone only. Fonts, voices, GPU, rendering,
hardware, and codec behavior remain properties of the composed profile. For
example, `Asia/Bangkok` may validly be paired with `en-US,en` and English or
system fonts.

## The persona does not move the GPU cluster

`fingerprint_platform` selects OS identity, client hints, fonts, voices, locale,
screen geometry and hardware buckets. It does not select the GPU capability
cluster. The anchor is chosen from the anchors the host's graphics backend can
actually serve.

This is a measured constraint, not a policy preference. Within one backend,
silicon generation does not matter: Ada, Ampere and Blackwell produce
byte-identical WebGL capability tables and an identical pixel render digest on
Linux/Vulkan, so one anchor legitimately covers a range of cards and the
renderer string is cosmetic relative to it. Across backends nothing transfers:
the same NVIDIA silicon produces a different capability digest through D3D11
than through Vulkan, and Apple Metal differs from both.

Consequently, on a macOS host a Windows persona keeps an Apple Metal cluster,
and that mismatch is **reported as a limitation rather than hidden**. A coherent
Windows fingerprint wants a Windows or Linux host with the corresponding
silicon. That is a property of graphics drivers, not of this codebase.

Identity strings (`unmaskedVendor`, `unmaskedRenderer`, WebGPU
`vendor`/`architecture`) rotate only among measured members of one anchor,
because those members provably agree on every capability digest. An anchor with
one measured member offers no rotation at all.

## Profile fields and inheritance

Every profile field is optional. An absent field remains host-inherited. This
is deliberate: a missing measurement must not be replaced with a plausible
constant that no consumer can distinguish from a real value.

The schema groups fields as follows:

| Section | Examples | Native limitation |
| --- | --- | --- |
| `platform`, `browser` | Client Hints platform, platform version, architecture, bitness, WoW64, form factors, UA string | The UA browser version must match the Chromium binary. Brand fields are assert-only: the brand list is a build invariant and the loader fails closed on a mismatch instead of rewriting it. |
| `cpu`, `memory`, `audio` | Logical cores, installed memory, audio buffer frames | Bucket values only, and clamped down to host capability, never up. |
| `screen`, `window` | Panel geometry, DPR, gamut, HDR, work-area insets, window chrome deltas | Impossible display arrangements are rejected. `avail_*` is derived from the panel plus the furniture insets; insets exceeding the panel fail the launch. |
| `gpu`, `gl_limits`, `gl_extensions`, `gl_precisions` | Renderer/vendor identity, WebGL limits, extensions and shader precision | These are native target inputs. Limits are capped by native capability; unsupported extensions cannot be added. Exact equality requires native behavior validation, not renderer identity alone. |
| `webgpu` | Adapter vendor, architecture, features, limits | Native target inputs. Features and limits are intersected with the native adapter; unsupported capabilities are not invented. |
| `locale`, `theme`, `input` | Timezone, language list, color scheme, pointer/hover | Locale and timezone can be overridden by the launch precedence rules. |
| `media`, `speech` | Hardware decode codecs, device counts, registered voices | Names do not create codecs, devices, or speech providers. `media.hw_decode_codecs` sets what `MediaCapabilities` reports as `powerEfficient` and deliberately does not touch `supported`, which answers from the decoders this build actually has. Device counts are a floor: inputs are added to reach the count and the host's own devices are never removed. Network voices are a build capability, not a profile value. |
| `keyboard`, `fonts` | Layout map, generic family mappings, enumeration allowlist | Enumeration only ever removes families; adding one needs the font file on the machine, which the operator installs and the browser assumes is done. See [docs/FONTS.md](FONTS.md). Unmeasured font and keyboard data stays inherited. |
| `network` | `effective_type`, `http_rtt_ms`, `downlink_mbps`, `save_data` | The emitter clamps `http_rtt_ms` into the band Chromium derives `effective_type` from and floors `downlink_mbps` at that type's own typical throughput, so the pair cannot contradict itself. `save_data` also drives the `Save-Data` request header and the `prefers-reduced-data` media feature. |
| `battery` | `present`, `charging`, `level`, `charging_time_seconds`, `discharging_time_seconds` | `present: false` reports what a real desktop reports: charging true, level 1.0, `chargingTime` 0, `dischargingTime` `Infinity`. The API stays exposed either way, because hiding it is what desktop Chrome does not do. Presence is conditioned on the panel axis, which is what encodes a laptop display. |

A profile does not replay opaque canvas or audio bytes. Those surfaces are
served by native Chromium emitters using validated inputs. Where the native
implementation cannot honor a profile value, the value remains constrained by
native capability or is inherited; it is not fabricated.

### Anchors and what selecting one claims

Selecting an anchor supplies the GPU capability inputs. It does not claim that
every resulting observable matches the reference device on a host that is not
that device. Native limits, feature intersections and inherited values can all
leave a surface below the anchor's own measurement, and `--fingerprint-explain`
names those.

Three anchors in the current catalogue were measured on a Chromium other than
152.0.7977.83, and each carries a `build_caveat` recording it. Capability tables
move between builds, so those three clusters can differ from this binary in
version-bearing fields. The resolver reports the caveat as a warning.

## Where a value came from

Every dispersion option, anchor and profile report carries one of these labels,
and `--fingerprint-explain` prints it per surface:

- `physical-ground-truth`: a consented capture from a physical device.
- `catalogue-value`: a value authored from platform release history and
  constrained by Chromium and platform rules. Authored rather than measured, and
  each option says so in its own `note`.
- `native-derived`: a value this build emits from its own source inputs.
- `proxy-derived`: a value resolved at launch from the network exit.
- `host-inherited`: a value left to the host.
- `compatibility-capture`: a value our collector measured from another
  runtime's output rather than from a physical device. Exactly one entry carries
  it, the software-rasteriser anchor `linux-swiftshader-google-6922d61bab83`,
  which was captured from a stock Chromium and claims no hardware.

A composed profile mixes several of these. The report keeps them apart rather
than presenting the whole profile as a measured machine. The catalogue holds
normalized values authored here; it does not redistribute third-party
fingerprint corpora.

That label used to mean something worse and was cleared out once. Fourteen
catalogue entries carried it under catalogue version 1 and all fourteen were
removed: the WebGL1 capability digest, the WebGL2 capability digest and the
canvas pixel digest were each a single shared value across supposedly distinct
Intel, NVIDIA, AMD and Apple GPUs. They were identity-string swaps taken on one
Mac. The one surviving use is a software rasteriser measured from stock
Chromium, where "not a physical device" is the accurate description rather than
a euphemism.

## Composition limitations

The dispersion space is large and every point in it is a machine someone could
own, but composition has hard limits. [docs/LIMITATIONS.md](LIMITATIONS.md) is
the user-facing version of this list.

1. A `catalogue-value` option is authored from platform release history. Drawing
   it does not turn it into a measurement.
2. A composed profile does not claim the host owns the corresponding GPU,
   display, fonts, audio stack or codec hardware.
3. Native capability is a ceiling. WebGL and WebGPU limits cannot exceed the
   active backend, extensions and features cannot be added, and a media claim
   cannot create a decoder or provider that is absent. The first two clauses are
   under change for a host with no hardware backend, where the ceiling is the
   software rasteriser's own and is being raised to meet the claim instead of
   reducing the claim to meet it. [docs/LIMITATIONS.md](LIMITATIONS.md) has the
   measured gap and the status; nothing in that direction is in a binary yet.
4. Capacity only ever goes down. A profile may claim fewer cores than the host
   has, never more, and the same holds for memory, GPU limits, codec support,
   font families, speech voices, and display area against window bounds. A page
   can measure parallel throughput, allocate until allocation fails, compile a
   shader at the advertised limit, or ask a voice to speak; a claim below host
   capability survives all of those and a claim above it fails the first.
   Options the host cannot serve are dropped before the draw, so a small host
   draws from a smaller set than a large one.
5. Canvas, text, audio, font metrics and speech providers can stay
   host-inherited when the native resources are missing. The profile reports
   that rather than adding synthetic output.
6. Geography selects locale and timezone only. A proxy-derived timezone does not
   imply regional fonts, voices or hardware.

## Where composition runs

**The compositor lives in the browser process, in C++, and is the only
implementation.** The bare binary must produce a fingerprint with no arguments,
which puts the compositor inside the binary by necessity. Reimplementing it in
the Python and Node packages would create three sources of truth for one
deterministic function, and the drift between them would be silent.

So the packages do not compose profiles. They do what only they can do: CLI
ergonomics, schema validation, launch orchestration, and GeoIP. A code path that
used to compose and now has no model fails closed with an explicit error rather
than guessing.

`scripts/profile_resolver.py` is the repository's reference implementation of the
same deterministic function. It exists so the tables can be validated, listed
and resolved outside a build, and so the C++ implementation has golden vectors
to agree with. Determinism across implementations is pinned by those vectors
rather than by cross-language byte comparison.

## Native and process contract

Profile values are consumed by native Chromium emitters. A conforming launch
must deliver the composed profile consistently to every process that owns a
selected surface, including browser, renderer, GPU, and network paths where
applicable. The transport is `--apostate-profile=<base64 JSON>`, parsed by
`base::apostate::Profile`. JavaScript injection, CDP overrides, redefined
accessors, and wrapper objects are outside this contract.

Diagnostics are local-only and may identify `profile_id`, `catalogue_version`,
`profile_schema_version`, `browser_build`, platform, anchor, the chosen option
per axis, locale source, timezone source, and warnings. Diagnostics must not
expose profile contents as a page-visible API or log proxy credentials.

## Examples

### Python

```python
from apostate import launch

browser = launch(
    fingerprint=12345,
    fingerprint_platform="windows",
    proxy="http://user:pass@proxy:8080",
    geoip=True,
)
```

### Node.js

```javascript
import { launch } from "apostate";

const browser = await launch({
  fingerprint: 12345,
  fingerprintPlatform: "windows",
  proxy: "http://user:pass@proxy:8080",
  geoip: true,
});
```

### Direct CLI

The binary takes the same configuration as switches:

```bash
./chrome \
  --fingerprint=12345 \
  --fingerprint-platform=windows \
  --fingerprint-locale=en-US,en \
  --fingerprint-timezone=America/New_York \
  --proxy-server=http://proxy:8080
```

[docs/FLAGS.md](FLAGS.md) is the full switch reference.

The package entry points are:

| Shared operation | Python | Node.js |
| --- | --- | --- |
| Launch | `launch` | `launch` |
| Context | `launch_context` | `launchContext` |
| Persistent context | `launch_persistent_context` | `launchPersistentContext` |
| Binary download/cache | `ensure_binary` | `ensureBinary` |
| Binary metadata | `binary_info` | `binaryInfo` |
| Cache maintenance | `clear_cache` | `clearCache` |

The package surface is Patchright-compatible Playwright behaviour. There are no
.NET bindings, no Puppeteer adapter, no GUI profile manager and no cloud profile
sync. `humanize: true` is rejected rather than accepted as a no-op.

## Validation

Validate profile files against the schema before launch:

```bash
python3 scripts/validate-profile.py profile.json
```

Validate the catalogue, every dispersion table and every anchor, and compose a
profile from a seed:

```bash
python3 scripts/profile_resolver.py --catalogue
python3 scripts/profile_resolver.py --list
python3 scripts/profile_resolver.py --resolve --fingerprint=12345 \
  --fingerprint-platform=windows
```

Switches are documented in [docs/FLAGS.md](FLAGS.md) and the residuals a profile
cannot close are in [docs/LIMITATIONS.md](LIMITATIONS.md).
