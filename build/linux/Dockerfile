# Linux build container.
#
# Pinned by digest, never by tag: `ubuntu:24.04` moves, and a moving base image
# is the most common way a "reproducible" build stops being one.
FROM ubuntu@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ENV DEBIAN_FRONTEND=noninteractive \
    DEPOT_TOOLS_UPDATE=0 \
    PYTHONDONTWRITEBYTECODE=1

# Freeze package resolution as well as the base filesystem. Ubuntu documents
# this setting at https://snapshot.ubuntu.com/.
RUN sed -i -E 's#http://(archive|security).ubuntu.com/ubuntu/?#https://snapshot.ubuntu.com/ubuntu/20260910T000001Z/#g' /etc/apt/sources.list.d/ubuntu.sources \
 && printf 'Acquire::Retries "2";\nAcquire::https::Timeout "30";\nAPT::Update::Error-Mode "any";\n' > /etc/apt/apt.conf.d/50snapshot

# The minimal base has no CA bundle, while the snapshot service uses HTTPS.
# Bootstrap trust from a hashed Ubuntu package without disabling TLS checks.
ADD --checksum=sha256:6bac2a01979e210d9eac1d4d56747ec709ea60654744d66705dc3c36e7629e50 \
    https://archive.ubuntu.com/ubuntu/pool/main/c/ca-certificates/ca-certificates_20260601~24.04.1_all.deb /tmp/ca-certificates.deb
RUN dpkg-deb --extract /tmp/ca-certificates.deb /tmp/ca-bootstrap \
 && mkdir -p /etc/ssl/certs \
 && cat /tmp/ca-bootstrap/usr/share/ca-certificates/mozilla/*.crt > /etc/ssl/certs/ca-certificates.crt \
 && rm -rf /tmp/ca-bootstrap /tmp/ca-certificates.deb

# Only what is needed to reach the point where Chromium's own
# install-build-deps.sh can run. Everything after that comes from the checkout,
# so the dependency set is pinned by CHROMIUM_VERSION rather than by this file.
RUN apt-get update && apt-get install -y --no-install-recommends \
      ca-certificates curl git python3 python3-venv lsb-release sudo file \
      xz-utils zip unzip pkg-config \
 && rm -rf /var/lib/apt/lists/*

# Build as a non-root user whose uid is supplied at build time, so artefacts
# written to the mounted workspace are owned by the invoking user.
ARG UID=1000
ARG GID=1000
RUN (getent group "${GID}" >/dev/null || groupadd -g "${GID}" build) \
 && useradd -m -o -u "${UID}" -g "${GID}" -s /bin/bash build \
 && echo 'build ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/build

USER build
WORKDIR /work
