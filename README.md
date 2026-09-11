# HuntLoop

A self-hosted job discovery and scoring pipeline. Relevant roles surface without anyone going looking, and the definition of "relevant" improves as the search progresses.

## Status

Phase 1 of 5 complete: the shared data model and persistence layer. There is no CLI, API, or web interface yet — Phase 2 adds the discovery pipeline. What exists today is the schema both later containers build on, plus its three guarantees.

## What Phase 1 guarantees

1. Data persists correctly across a full container stop, recreation, and restart cycle.
   → `bash scripts/verify_persistence_cycle.sh`
2. The system runs against Postgres by changing one configuration value, with no code or schema change.
   → `pytest tests/test_portability.py -x`
3. A raw copy of the primary database file, opened independently, contains no usable LLM API credential.
   → `pytest tests/test_credentials.py -x` and step 7 of the cycle script

## Setup

```bash
git clone <repo> && cd huntloop
cp .env.example .env
# REQUIRED: generate an encryption key and paste it into .env
openssl rand -base64 32
```

`HUNTLOOP_SECRET_KEY` must be filled in or nothing will start — this is deliberate. See `docs/operations/backup-restore.md` for why an auto-generated key would be unsafe.

## Running the stack

```bash
docker compose up          # runs both migrations, then exits (no app service yet in Phase 1)
docker compose logs migrate
```

Compose itself refuses to start if `HUNTLOOP_SECRET_KEY` is unset — this is the same fail-fast check as the one inside the application.

## Local development

```bash
python3.12 -m venv .venv
.venv/bin/pip install -e ".[dev]"
.venv/bin/python -m pytest tests/ -x -m "not postgres"   # fast, no Docker needed
.venv/bin/python -m pytest tests/ -x                     # full, includes a real Postgres container
```

## Database migrations

HuntLoop has two named migration environments because the credentials store is a physically separate database:

```bash
alembic --name main_db upgrade head          # 9 application tables
alembic --name credentials_db upgrade head   # 1 encrypted-secrets table, separate file
```

Migrations run automatically via the `migrate` Compose service, so these are only needed for local development. The two histories are deliberately independent — they never share a migration chain.

## Documentation

- [`docs/architecture/data-model.md`](docs/architecture/data-model.md)
- [`docs/operations/backup-restore.md`](docs/operations/backup-restore.md)
- [`docs/operations/database-backends.md`](docs/operations/database-backends.md)
- [`docs/operations/scheduling.md`](docs/operations/scheduling.md)

## Phase 2 — discovery and scoring

1. Copy the criteria template and edit it:
   ```bash
   cp criteria.example.yml criteria.yml
   ```
   Fields you must change: `profile_summary`, `seniority_min` / `seniority_max`, `posting_age_days`, `locations.eligible_countries`, `compensation_floor`, and `dimension_weights`.

2. Set your environment variables (the API key lives in the credentials store, not an env var, but you can use `HUNTLOOP_OPENAI_API_KEY` as a headless builder fallback):
   ```bash
   export HUNTLOOP_SECRET_KEY=$(openssl rand -base64 32)
   export HUNTLOOP_OPENAI_BASE_URL="https://api.openai.com/v1"
   export HUNTLOOP_OPENAI_API_KEY="sk-..."
   ```

3. Load your criteria into the database:
   ```bash
   docker compose run --rm -v "$(pwd)/criteria.yml:/data/criteria.yml" app criteria load /data/criteria.yml
   ```
   The `-v` flag is required: `/data` inside the container is a named Docker volume (it holds the databases), not a bind mount of your working directory, so the file must be mounted in explicitly.
   (Outside Docker, the same command is `huntloop criteria load criteria.yml` — it prints the new version number.)

4. Add an employer to track:
   ```bash
   docker compose run --rm app company add "Cobalt" --url https://cobalt.io/careers
   ```

5. Run a free discovery pass (no LLM scoring):
   ```bash
   docker compose run --rm app run --no-score
   ```
   *Note: `run --no-score` requires no API key at all and costs nothing. If you see a `RendererUnavailable` error locally outside Docker, make sure you ran `playwright install --with-deps chromium`.*

6. Run the real scoring pipeline:
   ```bash
   docker compose run --rm app run
   ```

7. View your scored jobs:
   ```bash
   docker compose run --rm app jobs list
   ```

## Phase 3 — unattended scheduling and run health

Run discovery on a daily schedule without anyone at the terminal:

```bash
docker compose up -d scheduler          # daily run at HUNTLOOP_RUN_AT in HUNTLOOP_TIMEZONE
```

For a local foreground scheduler (blocks until Ctrl-C): `huntloop scheduler start`.

Check what the scheduler has been doing — every run's trigger, status, per-stage
counts and cost, without opening the database:

```bash
huntloop run history          # last 10 runs, newest first
huntloop run history --json   # same fields, machine-readable
```

Configuration (`HUNTLOOP_RUN_AT`, `HUNTLOOP_TIMEZONE`, `HUNTLOOP_RUN_SPEND_CAP_USD`),
catch-up after downtime, and troubleshooting are covered in
[`docs/operations/scheduling.md`](docs/operations/scheduling.md).
