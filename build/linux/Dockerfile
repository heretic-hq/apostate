# Linux build container.
#
# Pinned by digest, never by tag: `ubuntu:24.04` moves, and a moving base image
# is the most common way a "reproducible" build stops being one.
FROM ubuntu@sha256:33ceb71981b602c1a7443a53469e4dba065f7503eab3078a2d7a57a2ab987517

ENV DEBIAN_FRONTEND=noninteractive \
    DEPOT_TOOLS_UPDATE=0 \
    PYTHONDONTWRITEBYTECODE=1

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
RUN groupadd -g "${GID}" build 2>/dev/null || true \
 && useradd -m -u "${UID}" -g "${GID}" -s /bin/bash build 2>/dev/null || true \
 && echo 'build ALL=(ALL) NOPASSWD:ALL' > /etc/sudoers.d/build

USER build
WORKDIR /work
