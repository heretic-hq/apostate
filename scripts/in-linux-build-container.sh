#!/usr/bin/env bash
# Prepare once per pinned input set; run a repository script in that image.
# Usage: scripts/in-linux-build-container.sh --prepare
#        scripts/in-linux-build-container.sh [--user UID:GID] scripts/build.sh linux-x64
# The checkout and optional external workspace retain their absolute paths.
set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd -P)"
fail() { printf 'error: %s\n' "$*" >&2; exit 1; }
[[ "$(uname -s)-$(uname -m)" == Linux-x86_64 ]] || fail 'requires Linux x86_64'
[[ -z "${APOSTATE_BUILD_IMAGE_ID:-}" ]] || fail 'already inside a build container'
run_user="$(id -u):$(id -g)"
prepare=false
while (($#)); do
  case "$1" in
    --prepare) prepare=true; shift ;;
    --user) (($# >= 2)) || fail '--user requires UID:GID'; run_user="$2"; shift 2 ;;
    --) shift; break ;;
    -*) fail "unknown option: $1" ;;
    *) break ;;
  esac
done
[[ "$run_user" =~ ^[0-9]+:[0-9]+$ ]] || fail '--user requires numeric UID:GID'
if "$prepare"; then
  (($# == 0)) || fail '--prepare takes no repository command'
else
  (($# > 0)) || fail 'provide --prepare or scripts/NAME.sh [arguments]'
  [[ "$1" == scripts/*.sh ]] || fail 'command must name a repository shell script'
  command_path="$(realpath "$REPO_ROOT/$1")"
  [[ "$command_path" == "$REPO_ROOT/scripts/"* && -f "$command_path" ]] || fail 'script escapes repository or is missing'
  [[ "$command_path" != "$REPO_ROOT/scripts/in-linux-build-container.sh" ]] || fail 'recursive container invocation'
  shift
fi
workspace="$(realpath "${APOSTATE_WORKSPACE:-$REPO_ROOT/.workspace}")"
src="$workspace/src"
version="$(tr -d '[:space:]' < "$REPO_ROOT/build/CHROMIUM_VERSION")"
depot_revision="$(tr -d '[:space:]' < "$REPO_ROOT/build/DEPOT_TOOLS_REVISION")"
chromium_revision="$(git -C "$src" rev-parse "$version^{commit}")"
[[ "$(git -C "$src" rev-parse HEAD)" == "$chromium_revision" ]] || fail 'checkout HEAD differs from CHROMIUM_VERSION'
[[ "$(git -C "$workspace/depot_tools" rev-parse HEAD)" == "$depot_revision" ]] || fail 'depot_tools HEAD differs from pin'
context="$(mktemp -d)"
trap 'rm -rf "$context"' EXIT
for name in install-build-deps.sh install-build-deps.py; do
  git -C "$src" show "$chromium_revision:build/$name" > "$context/$name"
  chmod 755 "$context/$name"
done
cp "$REPO_ROOT/build/linux/Dockerfile" "$context/Dockerfile"
cp "$REPO_ROOT/build/linux/prefetch-build-deps.py" "$context/prefetch-build-deps.py"
cp "$REPO_ROOT/build/linux/snapshot-transport.py" "$context/snapshot-transport.py"
cat >> "$context/Dockerfile" <<'DOCKER'

# These files are extracted from the pinned commit by the wrapper script.
USER root
COPY install-build-deps.sh install-build-deps.py prefetch-build-deps.py snapshot-transport.py /opt/apostate-build-deps/
RUN rm -f /etc/apt/apt.conf.d/docker-clean \
 && printf 'Acquire::https::No-Cache "true";\nAcquire::http::No-Cache "true";\n' > /etc/apt/apt.conf.d/51-no-cache \
 && python3 /opt/apostate-build-deps/snapshot-transport.py python3 /opt/apostate-build-deps/prefetch-build-deps.py --no-prompt --no-syms --no-arm --no-android --no-chromeos-fonts --no-backwards-compatible
RUN python3 /opt/apostate-build-deps/snapshot-transport.py /opt/apostate-build-deps/install-build-deps.sh --no-prompt --no-syms --no-arm --no-android --no-chromeos-fonts --no-backwards-compatible \
 && dpkg-query -W -f='${binary:Package}\t${Version}\n' | LC_ALL=C sort > /opt/apostate-build-deps/packages.lock \
 && rm -rf /var/lib/apt/lists/* /var/cache/apt/archives/*.deb
USER build
DOCKER
input_hash="$(python3 - "$context" "$version" "$chromium_revision" "$depot_revision" <<'PY'
import hashlib, pathlib, sys
p = pathlib.Path(sys.argv[1])
h = hashlib.sha256()
for value in sys.argv[2:]:
    h.update(value.encode() + b'\0')
for name in ('Dockerfile', 'install-build-deps.sh', 'install-build-deps.py', 'prefetch-build-deps.py', 'snapshot-transport.py'):
    h.update(name.encode() + b'\0' + (p / name).read_bytes() + b'\0')
print(h.hexdigest())
PY
)"
tag="apostate-linux-build:$input_hash"
if ! docker image inspect "$tag" >/dev/null 2>&1; then
  docker build --platform linux/amd64 --label "org.apostate.build-inputs=$input_hash" \
    --label "org.apostate.chromium-revision=$chromium_revision" --tag "$tag" "$context"
fi
image_id="$(docker image inspect --format '{{.Id}}' "$tag")"
[[ "$(docker image inspect --format '{{index .Config.Labels "org.apostate.build-inputs"}}' "$image_id")" == "$input_hash" ]] || fail 'cached image input label mismatch'
receipt_dir="$workspace/build-container"
mkdir -p "$receipt_dir"
docker run --rm --network none --entrypoint cat "$image_id" /opt/apostate-build-deps/packages.lock > "$context/packages.lock"
python3 - "$receipt_dir" "$input_hash" "$image_id" "$version" "$chromium_revision" "$depot_revision" "$context/packages.lock" <<'PY'
import hashlib, json, pathlib, sys
out, inputs, image, version, chromium, depot, packages = sys.argv[1:]
data = pathlib.Path(packages).read_bytes()
receipt = {'input_sha256': inputs, 'image_id': image, 'chromium_version': version,
           'chromium_revision': chromium, 'depot_tools_revision': depot,
           'packages_sha256': hashlib.sha256(data).hexdigest()}
path = pathlib.Path(out)
(path / (inputs + '.json')).write_text(json.dumps(receipt, indent=2) + '\n')
(path / (inputs + '.packages.lock')).write_bytes(data)
(path / 'current.json').write_text(json.dumps(receipt, indent=2) + '\n')
PY
printf 'build image: %s\nreceipt: %s/current.json\n' "$image_id" "$receipt_dir"
if "$prepare"; then exit 0; fi
mounts=(--mount "type=bind,source=$REPO_ROOT,target=$REPO_ROOT")
if [[ "$workspace" != "$REPO_ROOT/"* ]]; then
  mounts+=(--mount "type=bind,source=$workspace,target=$workspace")
fi
env_args=(--env DEPOT_TOOLS_UPDATE=0 --env DEPOT_TOOLS_METRICS=0
          --env "APOSTATE_WORKSPACE=$workspace" --env "APOSTATE_BUILD_IMAGE_ID=$image_id")
if [[ -n "${APOSTATE_JOBS:-}" ]]; then env_args+=(--env "APOSTATE_JOBS=$APOSTATE_JOBS"); fi
docker run --rm --init --user "$run_user" --workdir "$REPO_ROOT" \
  "${mounts[@]}" "${env_args[@]}" --entrypoint /bin/bash "$image_id" "$command_path" "$@"
