# syntax=docker/dockerfile:1

FROM node:22-bookworm-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci
COPY frontend/ ./
RUN npm run build

# Téléchargement et extraction de s6-overlay (curl/xz uniquement dans cet étage).
FROM debian:bookworm-slim AS s6-overlay
ARG S6_OVERLAY_VERSION=3.2.3.2
ARG TARGETARCH
RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates curl xz-utils \
    && case "$TARGETARCH" in \
         amd64) S6_ARCH=x86_64 ;; \
         arm64) S6_ARCH=aarch64 ;; \
         *) echo "architecture non supportée: $TARGETARCH" >&2; exit 1 ;; \
       esac \
    && curl -fsSL -o /tmp/s6-noarch.tar.xz \
         "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-noarch.tar.xz" \
    && curl -fsSL -o /tmp/s6-arch.tar.xz \
         "https://github.com/just-containers/s6-overlay/releases/download/v${S6_OVERLAY_VERSION}/s6-overlay-${S6_ARCH}.tar.xz" \
    && tar -C / -Jxpf /tmp/s6-noarch.tar.xz \
    && tar -C / -Jxpf /tmp/s6-arch.tar.xz \
    && rm /tmp/s6-noarch.tar.xz /tmp/s6-arch.tar.xz \
    && rm -rf /var/lib/apt/lists/*

# Dépendances Python compilées/installées hors de l'image finale.
FROM python:3.12-slim-bookworm AS python-builder
WORKDIR /build
COPY requirements.lock.txt pyproject.toml alembic.ini README.md ./
COPY backend ./backend
RUN cat /proc/sys/kernel/random/uuid | tr -d '\n' > ./backend/mediatracker/core/.build-id
RUN pip install --no-cache-dir --prefix=/python-prefix --require-hashes --no-deps -r requirements.lock.txt \
    && pip install --no-cache-dir --prefix=/python-prefix --no-deps .

FROM python:3.12-slim-bookworm

ARG S6_OVERLAY_VERSION=3.2.3.2
ARG TARGETARCH

COPY --from=s6-overlay /command /command
COPY --from=s6-overlay /etc/s6-overlay /etc/s6-overlay
COPY --from=s6-overlay /init /init
COPY --from=s6-overlay /package /package

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        aria2 \
        ca-certificates \
        # Compatibilité des compose déjà déployés (healthcheck curl qui surcharge le Dockerfile).
        curl \
        tzdata \
    && rm -rf /var/lib/apt/lists/*

COPY --from=python-builder /python-prefix /usr/local
WORKDIR /app

COPY docker/log-config.json /app/docker/log-config.json
COPY docker/s6-rc.d/ /etc/s6-overlay/s6-rc.d/
COPY docker/cont-init.d/ /etc/cont-init.d/
COPY docker/scripts/mediatracker-init /etc/s6-overlay/scripts/mediatracker-init
COPY --from=frontend /src/dist /app/frontend/dist
# Le oneshot « init » dépend de base (cont-init). Les services sont dans le
# bundle user sous user-bundles.d : s6-rc.d/user est déprécié.
RUN chmod +x /etc/cont-init.d/* /etc/s6-overlay/scripts/mediatracker-init \
    /etc/s6-overlay/s6-rc.d/api/run /etc/s6-overlay/s6-rc.d/worker/run \
    /etc/s6-overlay/s6-rc.d/aria2/run /etc/s6-overlay/s6-rc.d/fingerprint/run \
    && mkdir -p /etc/s6-overlay/s6-rc.d/init/dependencies.d \
       /etc/s6-overlay/s6-rc.d/api/dependencies.d \
       /etc/s6-overlay/s6-rc.d/worker/dependencies.d \
       /etc/s6-overlay/s6-rc.d/aria2/dependencies.d \
       /etc/s6-overlay/s6-rc.d/fingerprint/dependencies.d \
       /etc/s6-overlay/user-bundles.d/user/contents.d \
    && touch /etc/s6-overlay/s6-rc.d/init/dependencies.d/base \
       /etc/s6-overlay/s6-rc.d/api/dependencies.d/init \
       /etc/s6-overlay/s6-rc.d/worker/dependencies.d/init \
       /etc/s6-overlay/s6-rc.d/worker/dependencies.d/aria2 \
       /etc/s6-overlay/s6-rc.d/aria2/dependencies.d/init \
       /etc/s6-overlay/s6-rc.d/fingerprint/dependencies.d/init \
       /etc/s6-overlay/user-bundles.d/user/contents.d/init \
       /etc/s6-overlay/user-bundles.d/user/contents.d/api \
       /etc/s6-overlay/user-bundles.d/user/contents.d/worker \
       /etc/s6-overlay/user-bundles.d/user/contents.d/aria2 \
       /etc/s6-overlay/user-bundles.d/user/contents.d/fingerprint

# s6 démarre en root le temps d'appliquer PUID/PGID, puis API, worker et aria2
# tournent sous cet utilisateur. Pas de directive USER : l'init s6 en a besoin.
ARG APP_VERSION=2.0.0
ARG GIT_SHA=dev
ARG BUILD_DATE=
ARG MEDIATRACKER_UPDATE_IMAGE=ghcr.io/nico2511/mediatracker-hive
ARG IMAGE_SOURCE=https://github.com/nico2511/Mediatracker-hive
ENV MEDIATRACKER_VERSION=${APP_VERSION} \
    MEDIATRACKER_GIT_SHA=${GIT_SHA} \
    MEDIATRACKER_BUILD_DATE=${BUILD_DATE} \
    MEDIATRACKER_UPDATE_IMAGE=${MEDIATRACKER_UPDATE_IMAGE}
LABEL org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.created="${BUILD_DATE}" \
      org.opencontainers.image.source="${IMAGE_SOURCE}" \
      org.opencontainers.image.title="MediaTracker"

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PORT=8080 \
    TZ=Europe/Paris \
    PUID=1000 \
    PGID=100 \
    UMASK=022 \
    CONFIG_DIR=/config \
    DATA_DIR=/data \
    DATABASE_URL=sqlite:////config/app.db \
    S6_LOGGING=0 \
    S6_VERBOSITY=1 \
    S6_BEHAVIOUR_IF_STAGE2_FAILS=2 \
    S6_CMD_WAIT_FOR_SERVICES_MAXTIME=0 \
    S6_KILL_GRACETIME=10000

EXPOSE 8080
VOLUME ["/config", "/data"]
HEALTHCHECK --interval=60s --timeout=10s --start-period=15s --retries=3 \
    CMD python -c "import os, urllib.request; urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\", \"8080\")}/healthz', timeout=9)"
ENTRYPOINT ["/init"]
