# Profile and launch specification

This document is the public contract for Apostate profiles and launches. The
machine-readable profile schema is [`config/profile.schema.json`](../config/profile.schema.json).
The schema is authoritative for accepted profile fields; this page defines how
those fields are selected, combined, and reported.

A profile is a set of native inputs for a browser process. It is not a
JavaScript wrapper, a collection of per-request overrides, or a claim that the
host is physically the device being presented.

## Versions and identity

The release baseline uses these version boundaries:

- package version: `0.1.0`
- Chromium version: `152.0.7977.83`
- profile catalogue version: `1`

A seed is a deterministic selector. It is not a per-call randomizer and does
not invent a physical machine. A resolver uses this identity tuple:

```text
(profile_schema_version, catalogue_version, Chromium build,
 fingerprint_platform, fingerprint)
```

The tuple is hashed with a stable, explicitly specified hash algorithm. It must
not use language-runtime hash behavior, process-randomized hashes, or unordered
map iteration. The resolver then selects a compatible family and its validated
GPU/runtime, display, hardware, theme, and locale defaults. The same tuple
always produces the same result; changing the schema, catalogue, or browser
build creates a new identity rather than silently remapping an existing one.

There is no per-call fingerprint randomness. Repeated reads and fresh launches
with the same resolved profile are expected to be stable. Variation happens at
the profile-selection boundary.

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
| `fingerprint` | Integer seed used by the deterministic resolver. |
| `fingerprint_platform` | Platform persona, such as `windows`, `macos`, or `linux`. |
| `profile` | Explicit profile file or profile identifier. `null` means no explicit profile. |
| `locale` | Explicit locale or Accept-Language policy. `null` means use the precedence rules below. |
| `timezone` | Explicit IANA timezone. `null` means use the precedence rules below. |
| `geoip` | When `true`, resolve locale and timezone from the observed network exit before launch. |
| `proxy` | Proxy URL and credentials, if any. Credentials are used for launch and are never written to diagnostics or logs. |
| `headless` | Whether Chromium is launched headless. |
| `user_data_dir` | Persistent profile directory. `null` selects the integration's temporary-directory policy. |
| `args` | Additional Chromium arguments. Integrations must preserve the canonical profile and localization semantics when adding them. |

Package APIs may expose idiomatic camelCase aliases, but aliases map to this
same configuration and must not create a second profile model. For example,
`fingerprintPlatform` maps to `fingerprint_platform` and `userDataDir` maps to
`user_data_dir`.

## Selection precedence

Profile selection is resolved in this order, from strongest to weakest:

```text
explicit profile file
  > explicit profile ID
  > fingerprint seed plus platform persona
  > platform default
  > host inheritance
```

An explicit profile file is validated before launch. An explicit profile ID is
looked up in the versioned catalogue. A seed without a platform persona uses
the platform default only when one is defined; otherwise the affected fields
remain inherited from the host.

Network localization has a separate precedence chain:

```text
explicit locale/timezone
  > GeoIP-derived locale/timezone
  > profile-selected locale/timezone
  > host values
```

With `geoip: true`, the lookup is performed through the configured proxy, or
through the direct network when no proxy is configured. The result is fixed for
the process before Chromium starts. A timeout or lookup failure is reported as
an error or warning and must not silently invent `UTC` or `en-US`.

Geography controls locale and timezone only. Fonts, voices, GPU, rendering,
hardware, and codec behavior remain properties of the selected device family.
For example, `Asia/Bangkok` may validly be paired with `en-US,en` and English
or system fonts.

## Profile fields and inheritance

Every profile field is optional. An absent field remains host-inherited. This
is deliberate: a missing measurement must not be replaced with a plausible
constant that no consumer can distinguish from a real value.

The schema currently groups fields as follows:

| Section | Examples | Native limitation |
| --- | --- | --- |
| `platform`, `browser` | Client Hints platform, architecture, UA string | The UA browser version must match the Chromium binary. |
| `cpu`, `memory`, `audio` | Logical cores, memory input, audio buffer frames | Values are bounded by what the host and current native path can serve. |
| `screen` | Display geometry, work area, DPR, gamut, HDR | Impossible display arrangements are rejected; missing origins remain inherited. |
| `gpu`, `gl_limits`, `gl_extensions`, `gl_precisions` | Renderer/vendor, WebGL limits, extensions and shader precision | These are native target inputs. Limits are capped by native capability; unsupported extensions cannot be added. Exact compatibility requires native behavior validation, not renderer identity alone. |
| `webgpu` | Adapter vendor, architecture, features, limits | These are native target inputs. Features and limits are intersected with the native adapter; unsupported capabilities are not invented. |
| `locale`, `theme`, `input` | Timezone, language list, color scheme, pointer/hover | Locale and timezone can be overridden by the launch precedence rules. |
| `media`, `speech` | Hardware decode codecs, device counts, registered voices | Names do not create codecs, devices, or speech providers. |
| `keyboard`, `fonts` | Layout map and validated generic font family mappings | Unmeasured font and keyboard data stays inherited. |

A profile does not replay opaque canvas or audio bytes. Those surfaces are
served by native Chromium emitters using validated inputs. Where the native
implementation cannot honor a profile value, the value remains constrained by
native capability or is inherited; it is not fabricated.

### Compatibility validation status

Selecting a compatibility family supplies native target inputs; it does not
claim that every resulting observable is physically equivalent. A family is
WebGL-validated only after a native run checks the full behavior cluster:
vendor and renderer identity, extension exposure, numeric limits, shader
precision, and falsification boundaries such as allocations and shader
compilation that exercise the reported values. A renderer string or schema
validity alone is insufficient. Native caps, feature intersections and
inherited values make the affected surfaces limited or provisional until that
run passes.

## Provenance classes

Catalogue entries, profile reports, and release evidence use these classes:

- `physical-ground-truth`: a direct, consented capture from a physical device.
- `compatibility-capture`: our collector measured a profile emitted by an
  external compatibility runtime and that profile was exercised against real
  targets. This is compatibility evidence, not a direct physical-device
  capture.
- `catalogue-value`: a normalized value or combination accepted from
  compatibility research and constrained by Chromium and platform rules.
- `native-derived`: a value emitted by the current Apostate/Chromium build from
  source inputs.
- `proxy-derived`: a value determined at launch from the actual network exit.
- `host-inherited`: a value intentionally left to the host.

A generated profile may combine more than one class. Reporting must preserve
those classifications instead of collapsing them into a claim of physical
hardware authenticity. Public catalogue data contains Apostate-owned,
normalized values only; raw third-party fingerprint corpora are not part of the
product.

## Compatibility-backed limitations

Compatibility-backed and catalogue-composed profiles broaden the set of
supported families, but they have explicit limits:

1. A compatibility capture records what that runtime emitted. It does not prove
   that every field came from a matching physical device.
2. Normalization removes known wrapper, browser-version, and impossible-display
   artifacts before a value can enter the catalogue. Values that fail schema or
   coherence checks are rejected.
3. A catalogue value is selectable evidence, not a promise that the host owns
   the corresponding GPU, display, fonts, audio stack, or codec hardware.
4. Native capability is a ceiling. WebGL/WebGPU limits cannot exceed the active
   backend, extensions and features cannot be added, and media claims cannot
   create a decoder or provider that is absent.
5. Canvas, text, audio, font metrics, speech providers, and other native
   surfaces can remain host-inherited or differ when the required native
   resources are unavailable. The profile must report that limitation rather
   than add synthetic output.
6. Geographic localization never selects device traits. A proxy-derived
   timezone does not imply regional fonts, voices, or hardware.

These rules make a generated profile useful for compatibility testing and
repeatable launches without representing it as an independently measured
physical computer.

## Native and process contract

Profile values are consumed by native Chromium emitters. A conforming launch
must deliver the resolved profile consistently to every process that owns a
selected surface, including browser, renderer, GPU, and network paths where
applicable. JavaScript injection, CDP overrides, redefined accessors, and
wrapper objects are outside this contract.

Diagnostics are local-only and may identify `profile_id`, `catalogue_version`,
`browser_build`, platform, GPU family, display, hardware, locale source,
timezone source, and warnings. Diagnostics must not expose profile contents as a
page-visible API or log proxy credentials.

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

The following is the planned direct-CLI shape of the canonical contract:

```bash
apostate \
  --fingerprint=12345 \
  --fingerprint-platform=windows \
  --fingerprint-locale=en-US \
  --fingerprint-timezone=America/New_York \
  --proxy-server=http://proxy:8080
```

These seed and persona switches are **not implemented in the current
baseline**. The existing native profile entry point is `--apostate-profile`;
the M2 direct-CLI work remains blocked pending Chromium checkout and compile
evidence. The example documents the required end state and must not be read as
evidence that the native binary currently accepts these switches. Package
wrappers likewise do not make the direct CLI available.

The required package surface is Patchright-compatible Playwright behavior. The
initial entry points are:

| Shared operation | Python | Node.js |
| --- | --- | --- |
| Launch | `launch` | `launch` |
| Context | `launch_context` | `launchContext` |
| Persistent context | `launch_persistent_context` | `launchPersistentContext` |
| Binary download/cache | `ensure_binary` | `ensureBinary` |
| Binary metadata | `binary_info` | `binaryInfo` |
| Cache maintenance | `clear_cache` | `clearCache` |

Humanized input, Puppeteer, .NET bindings, GUI management, and cloud profile
synchronization are not part of the initial contract.

## Validation

Validate profile files against the schema before launch:

```bash
python3 scripts/validate-profile.py profile.json
```

A release-quality resolver also validates family compatibility and rejects
impossible combinations before starting Chromium. The release gate and its
current status are documented in [`docs/RELEASE.md`](RELEASE.md).
