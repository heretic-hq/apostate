# Release policy

`.github/workflows/release.yml` publishes browser artifacts from a semver tag
after its release gate, target builds and artifact checks succeed.
Nightly builds use the same reusable build job but retain their outputs as
Actions artifacts and do not create build-provenance attestations.

## Release identity and platform set

`.github/release/artifact-policy.json` defines the package version, Chromium
version, recognized platforms, artifact filenames and release-manifest fields.
The package version is `0.1.0`; `build/CHROMIUM_VERSION` pins Chromium to
`152.0.7977.83` and must agree with the policy.

The profile catalogue has its own version sequence. Each manifest takes
`catalogue_version` from `resources/profiles/catalogue.json` at its
`source_revision`. Publication checks it against the catalogue at the release
tag, rather than a constant in the release policy.

The CI targets are `linux-x64`, `linux-arm64`, `macos-arm64` and
`windows-x64`. `scripts/resolve-build-targets.sh` selects all four when the
repository variable `APOSTATE_BUILD_TARGETS` is unset or empty. Set that
variable to a comma-separated subset to narrow a release. The publish job
requires exactly the resolved targets, with one manifest and archive per
target.

Linux arm64 is a cross-build on the x64 runner inside the pinned
`linux/amd64` container. Windows x64 builds natively against the runner's own
Visual Studio and Windows SDK.
See [Build contract](BUILD.md) for runner mappings and toolchain requirements.

## Cut a release

For a source revision that satisfies the release gate:

1. Set the repository variable `APOSTATE_BUILD_TARGETS` only if narrowing the
   default three-target set. This is a repository variable, not a shell variable
   on the maintainer's machine.
2. Create and push a tag in the exact form `vMAJOR.MINOR.PATCH`.

For example, when publishing package version `0.1.0`:

```sh
git tag -a v0.1.0 -m 'Apostate 0.1.0'
git push origin v0.1.0
```

The tag push triggers the rest. There is no release secret to configure and
no maintainer-held artifact key to manage. Annotated and lightweight tags both
work; a tag signature is not required or checked.

Creating a tag in this repository requires push access, the same authority
that permits changing its workflow files. Requiring a tag signature would
add another key to manage without adding an independent trust root. Artifact
provenance comes from GitHub's keyless attestation, which binds each archive
to the repository, commit and workflow that produced it.

Manual dispatch accepts an existing `release_tag` and requires `confirm=true`
to guard against starting a multi-hour paid build accidentally. It executes
the same gate and build path; the confirmation is not an authentication check.

### Patch changes require a refreshed baseline

`scripts/build.sh` generates `build/MANIFEST.lock` after a successful build.
Its `patch_contents_sha256` covers every listed patch's name and complete
bytes, in `patches/series` order. Changing any patch changes that digest even
when the series file itself is unchanged.

`scripts/validate-release-baseline.py` checks the committed manifest in the
release gate before any Chromium build is scheduled. After a patch edit,
rebuild locally with the build scripts to refresh `build/MANIFEST.lock` and
include it in the source revision before tagging. Otherwise the release gate
fails immediately on the stale patch hash. A fresh CI build cannot repair an
input rejected by the earlier gate.

## What the workflow checks

The release gate runs on a hosted Ubuntu runner. It validates the exact tag
form, fetches tags, resolves the requested tag to a commit and requires the
checkout to match that commit. It then validates the checked-in build baseline
and runs the resolver and Python and Node package tests.

Target resolution follows the gate. Each selected target then runs
`.github/workflows/build-target.yml` on a fresh Blacksmith VM. The job checks
host tools and runner identity, fetches the pinned sources, applies patches,
builds, checks the resolver and generated baseline, performs a binary smoke
check, packages, attests and uploads the result. The Linux arm64 smoke check
checks the ELF machine type because the x64 build host cannot execute it.

The release matrix runs one target at a time with `fail-fast: true`. The
targets have separate VMs and could run concurrently; serialization contains
costs while the pipeline is unproven. Both release and nightly workflows
share the `apostate-build` concurrency group with
`cancel-in-progress: false`, preventing overlapping full-build runs.

The publish job checks the expected platform set, manifest schema and exact
field set, package and Chromium versions, catalogue version at the tag,
artifact filename and platform binding, source revision, and every archive's
SHA-256. Missing, duplicate or unexpected platforms fail publication. It then
creates the GitHub release with the archives and their manifests. Provenance
verification through `gh attestation verify` is an out-of-band operation,
not an additional check performed by the publish job.

Publication uses `gh release create --verify-tag`. That flag requires the tag
to exist before creating the release; it does not verify a tag signature.

## Archives and manifests

`scripts/package-artifact.sh` writes a `.tar.zst` archive for each available
target. The policy reserves `.zip` for Windows. Each archive has the runtime
files needed by its target, the repository license, an optional notice, the
generated build manifest and the profile resources. Missing required runtime
files fail packaging. The release manifest is generated beside the archive
with a `.manifest.json` suffix.

`release/manifest.schema.json` permits exactly these fields:

| Field | Value |
| --- | --- |
| `package_version` | Package version from the artifact policy |
| `chromium_version` | Pinned Chromium version |
| `catalogue_version` | Catalogue version at the source revision |
| `platform` | Target built for this archive |
| `artifact` | Exact archive filename from the policy |
| `sha256` | SHA-256 of the complete archive bytes |
| `source_revision` | Repository commit that produced the archive |
| `patch_series_sha256` | SHA-256 of the patch-series file |
| `build_manifest_sha256` | SHA-256 of the generated build manifest |

Packaging writes UTF-8 JSON with sorted keys, compact separators and exactly
one trailing newline. Each release identity binds the source and build inputs
to its archive; do not replace published artifacts with different bytes.

## Verify a download

The Python and Node installers read the manifest and validate its artifact
binding before extraction. They compute SHA-256 over the complete downloaded
archive and require it to equal `sha256` in the manifest. A hash mismatch
fails installation before the archive is extracted. This integrity check
needs no additional network request or external verification tool.

SHA-256 detects bytes that differ from the manifest. Build provenance is a
separate check. Release builds call `actions/attest-build-provenance` to
create a GitHub artifact attestation for each archive. Sigstore mints an
ephemeral keypair for that workflow run and discards it; the attestation binds
the artifact to the repository, commit and workflow.

Verify that provenance against the repository with GitHub CLI:

```sh
gh attestation verify <artifact> --repo <owner>/<repo>
```

Replace the placeholders with the downloaded archive and the repository that
published it. Run this separately when checking provenance. Installers enforce
the archive hash before extraction; they do not invoke GitHub CLI or query
GitHub for attestations.

## Release-candidate evidence

`scripts/release-candidate-check.py` runs the local validators and tests
available in a checkout and reports missing evidence as `blocked`. It does
not download, build or publish artifacts. Its artifact-presence checks do not
verify archive hashes or attestations, and it does not exercise installation
from built package archives.

The checker inventories all four contract targets, including Windows, and
records unprovided native and conformance evidence as blocked. It is not called
by the release workflow and is not the target-selection gate for a subset
release.

A passing release workflow proves its listed checks, not native process
propagation, full browser conformance, installation on every platform or an
independent reproducibility result. Keep runtime and reproducibility evidence
separate from CI status. `scripts/verify-reproducible.sh` compares clean-build
output hashes when collecting that evidence; the release workflow does not
invoke it.

Physical-device and compatibility claims follow [Methodology](METHODOLOGY.md)
and [Profile specification](PROFILE_SPEC.md). Report only claims supported by
the corresponding captures and conformance runs. A schema check, successful
compile, renderer string or single detector result does not establish those
milestones.
