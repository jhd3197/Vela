FROM node:22-bookworm-slim AS dashboard
WORKDIR /build/web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Where the `computer` stage copies the browser worker's Node runtime from:
# the official image, nothing added on top. npm rides along for the build
# steps and is removed again once the worker is installed.
FROM node:22-bookworm-slim AS node-runtime

# The `computer` target — the image a controller application (CachiBot) builds
# on the user's machine on first use and then runs one container of per bot:
# `docker build --target computer .`. It is the controller-mode entry point
# plus what agent desktops need, the pinned browser worker and its Chromium,
# and nothing a bot will want to choose for itself later: no compiler, no
# curl, no language toolchains. The stage above the default one on purpose,
# so a plain `docker build` keeps producing the regular server image.
FROM python:3.12-slim-bookworm AS computer
ARG VELA_VERSION=""
LABEL org.opencontainers.image.title="Vela computer" \
      org.opencontainers.image.source="https://github.com/jhd3197/Vela" \
      org.opencontainers.image.version="${VELA_VERSION}" \
      vela.controller.protocol="5"
WORKDIR /app
# HOME matters twice: the build's npm and Playwright caches live there, and
# the worker Vela spawns at runtime carries this HOME (and nothing else) to
# find the browser at $HOME/.cache/ms-playwright.
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VELA_DATA_DIR=/data \
    VELA_PORT=7700 \
    VELA_CONTROLLER_TOKEN_FILE=/run/secrets/vela-controller-token \
    HOME=/home/vela
COPY requirements.txt ./
# libstdc++ and libgcc are what the copied Node binary and Chromium link
# against; ca-certificates is how the worker's package and its browser arrive.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libstdc++6 libgcc-s1 ca-certificates \
    && rm -rf /var/lib/apt/lists/* \
    && pip install --no-cache-dir -r requirements.txt
COPY vela/ ./vela/
COPY --from=dashboard /build/web/dist ./web/dist
COPY --from=node-runtime /usr/local/bin/node /usr/local/bin/node
COPY --from=node-runtime /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
    && ln -s /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx
# The worker the way scripts/setup-browser-worker.py installs it: the pinned
# playwright-core from the lockfile, then Chromium with the system libraries
# it links against. Playwright puts the browser under $HOME; the user made
# below owns that directory, and the spawned worker finds it through HOME.
# The script itself, run with --skip-browser once the browser is in place,
# writes the provenance the runtime checks read — one writer for that file.
COPY scripts/browser-worker/package.json scripts/browser-worker/package-lock.json ./scripts/browser-worker/
RUN cd scripts/browser-worker && npm ci --omit=dev --no-audit --no-fund
COPY scripts/browser-worker/src ./scripts/browser-worker/src
COPY scripts/setup-browser-worker.py ./scripts/setup-browser-worker.py
RUN cd scripts/browser-worker \
    && npx playwright-core install --with-deps chromium \
    && cd /app \
    && python scripts/setup-browser-worker.py --skip-browser \
    && rm -rf /usr/local/lib/node_modules/npm /usr/local/bin/npm /usr/local/bin/npx /home/vela/.npm /var/lib/apt/lists/*
# A fixed uid/gid a controller can reason about, owning its home (browser
# included), the data directory, and /workspace, where a bot's own tools land.
RUN groupadd --gid 10001 vela \
    && useradd --uid 10001 --gid vela --create-home vela \
    && mkdir -p /data /workspace \
    && chown -R 10001:10001 /home/vela /data /workspace
USER 10001:10001
VOLUME ["/data", "/workspace"]
EXPOSE 7700
# python only: nothing ships in this image just to probe its own health.
HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen('http://127.0.0.1:' + os.environ.get('VELA_PORT', '7700') + '/api/health', timeout=4)"]
ENTRYPOINT ["python", "-m", "vela.computer"]

FROM python:3.12-slim-bookworm
WORKDIR /app
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    VELA_DATA_DIR=/data
COPY requirements.txt ./
RUN pip install --no-cache-dir -r requirements.txt
COPY vela/ ./vela/
COPY --from=dashboard /build/web/dist ./web/dist
EXPOSE 7700
CMD ["python", "-m", "vela.container"]
