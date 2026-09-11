# HuntLoop

A self-hosted job discovery and scoring pipeline. Relevant roles surface without anyone going looking, and the definition of "relevant" improves as the search progresses.

## Status

Phase 4 of 5 complete: the pipeline interface (web UI + API). The whole system
runs from a browser on your own network — describe your criteria once, watch
employers resolve, review and act on scored listings, and read the dashboard.
There is no terminal or file editing required for day-to-day use; the CLI remains
the builder/operator harness.

## What works today

- **One command starts everything** (`OPS-01`): `docker compose up -d` runs both
  migrations, the scheduler, and the web interface.
- **Any device on your network** (`UI-01`): the interface is served on port 8000
  with no auth ceremony (private-network trust model).
- **The whole pipeline from the browser**: criteria intake, employer registry and
  resolution, the listings workspace, run history, settings, and diagnostics.

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
docker compose up -d          # migrations + scheduler + web interface
docker compose logs -f web    # watch the API/UI come up
```

`docker compose up -d` is the entire system: a one-shot `migrate` service (both
databases), the long-running `scheduler`, and the `web` service (FastAPI serving
the built React SPA same-origin). Open **http://localhost:8000** on the host, or
**http://<machine-ip>:8000** from any other device on the same network — no
terminal, no file editing, no login.

To stop everything: `docker compose down` (add `-v` to also delete the data
volume — that erases your listings).

Compose itself refuses to start if `HUNTLOOP_SECRET_KEY` is unset — this is the same fail-fast check as the one inside the application.

## The browser interface (Phase 4)

Everything the CLI does is reachable from the UI; the CLI remains the
builder/operator harness, not the day-to-day interface.

- **First run — onboarding** (`/onboarding`): describe in a paragraph what you
  are looking for. The model extracts a structured draft, which you review and
  correct in an editable form (locations, seniority, compensation floor +
  currency, exclusions, work authorization), then drag-order the four scoring
  dimensions. Saving creates criteria **version 1**. The same flow proposes
  employers to track; you check the ones you want, and resolution runs on those
  only, with an honest coverage headline ("we can watch N of the ~M we'd target").
- **Dashboard** (`/`): the pipeline funnel by stage, the next scheduled run, the
  last run's outcome and cost, and a **Run now** button for an on-demand run.
  A quiet week is explained here rather than left ambiguous.
- **Listings** (`/listings`): a dense table of every scored listing with a
  composable filter bar (status, employer, score threshold, date range). Open a
  row's drawer for the per-dimension score breakdown, reasoning, notes, open
  duration and repost count, and the status timeline. Change status in one click
  from the row or the drawer — that transition is the feedback signal.
- **Employers** (`/employers`): the registry with platform + board id for
  resolved employers and a per-employer **Retry** for the ones needing attention.
  Disable an employer in place without losing its history.
- **Runs** (`/runs`): run history and per-run detail, including failures.
- **Settings** (`/settings`): API access, per-stage model choice, schedule
  (time + timezone), and spend cap. Each section saves independently; a schedule
  change takes effect without restarting the scheduler. **Run diagnostics**
  checks API access, database connectivity, and a live employer fetch, streaming
  a result with a readable remedy for each.

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
