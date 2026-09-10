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

RUN mkdir -p /data
VOLUME ["/data"]

# Deliberately no default CMD: this one image serves the one-shot `migrate`
# service, the dev `probe`, and the `app` service, each supplying its own command.
