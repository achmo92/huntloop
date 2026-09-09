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
