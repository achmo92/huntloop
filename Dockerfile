# Stage 1: build the React SPA (plan 04-11). Kept separate so the Python
# runtime stage never carries node_modules or the Node toolchain, and so source
# edits don't invalidate the npm layer unless package.json/package-lock.json change.
FROM node:22-slim AS webbuild

WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci
COPY web/ ./
RUN npm run build

# Stage 2: the runtime image. Everything below is the pre-04-11 image verbatim
# (the same image serves the CLI, the scheduler, and now the web API).
FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    HUNTLOOP_DATA_DIR=/data

WORKDIR /app

# Dependency metadata first so the pip layer caches across source edits.
COPY pyproject.toml ./
COPY src ./src
RUN pip install --no-cache-dir ".[postgres]"

# --with-deps is not optional on slim: Chromium fails at launch with a bare-library error that reads like a Playwright bug. PLAYWRIGHT_BROWSERS_PATH is set explicitly because the default (~/.cache/ms-playwright) resolves to a different home directory at build time than at run time, and the resulting "executable doesn't exist" surfaces only in the container, never locally.
ENV PLAYWRIGHT_BROWSERS_PATH=/opt/playwright
RUN playwright install --with-deps chromium \
    && chmod -R a+rX /opt/playwright

# alembic.ini's script_location values are repo-root-relative, so both the ini
# and the migrations tree must land at WORKDIR for `alembic --name ...` to
# resolve regardless of where it is invoked from.
COPY alembic.ini ./
COPY migrations ./migrations
COPY scripts ./scripts

# The built SPA, at /app/web/dist — the location the `web` Compose service's
# uvicorn process (WORKDIR /app) serves same-origin via create_app().
COPY --from=webbuild /web/dist ./web/dist

RUN mkdir -p /data

# T-04-07: run as a non-root user. /app (code + baked SPA) is read-only at
# runtime; /data is the writable volume.
RUN useradd --create-home --uid 10001 huntloop \
    && chown -R huntloop:huntloop /app /data
USER huntloop

VOLUME ["/data"]

# Deliberately no default CMD: this one image serves the one-shot `migrate`
# service, the dev `probe`, the one-shot `app` CLI runner, the long-running
# `scheduler`, and the `web` service, each supplying its own command.
