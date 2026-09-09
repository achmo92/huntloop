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

# alembic.ini's script_location values are repo-root-relative, so both the ini
# and the migrations tree must land at WORKDIR for `alembic --name ...` to
# resolve regardless of where it is invoked from.
COPY alembic.ini ./
COPY migrations ./migrations

RUN mkdir -p /data
VOLUME ["/data"]

# Deliberately no default CMD: this one image serves the one-shot `migrate`
# service and the dev `probe`, each supplying its own command. Phase 2+
# services add their own entrypoints against the same image.
