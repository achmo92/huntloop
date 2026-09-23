# HuntLoop

A self-hosted job discovery and scoring pipeline. Relevant roles surface without anyone going looking, and the definition of "relevant" improves as the search progresses.

## Status

**v1.0 shipped.** All five phases are complete: durable persistence, a headless
discovery pipeline, unattended scheduling and run health, a full browser interface,
and an adaptive feedback loop that proposes bounded criteria changes you approve
explicitly. The whole system runs from a browser on your own network — describe your
criteria once, watch employers resolve, review and act on scored listings, read the
dashboard, and approve or reject proposed criteria changes. No terminal or file
editing is required for day-to-day use; the CLI remains the builder/operator harness.

## Prerequisites

- **Docker** with the **Compose v2 plugin** (`docker compose version` should work) —
  Docker Desktop on macOS/Windows, or Docker Engine + Compose on Linux.
- **Network access** to an OpenAI-compatible LLM endpoint for scoring, criteria
  extraction, and proposal rationales (the free `--no-score` CLI pass needs no key).
- A few GB of free disk: the image bundles Chromium for the crawler fallback, so the
  first `docker compose up` build takes a few minutes.

## Setup

```bash
git clone https://github.com/achmo92/huntloop.git
cd huntloop
cp .env.example .env

# REQUIRED: generate an encryption key and paste it into .env as HUNTLOOP_SECRET_KEY
openssl rand -base64 32
```

`HUNTLOOP_SECRET_KEY` must be filled in or nothing will start — this is deliberate.
See [`docs/operations/backup-restore.md`](docs/operations/backup-restore.md) for why an
auto-generated key would be unsafe.

Everything else in `.env` has a working default. SQLite is the default database; to
run against Postgres, change the single `DATABASE_URL` value (no code or schema
change).

## Running the stack

```bash
docker compose up -d          # builds the image, runs migrations, starts scheduler + web
docker compose logs -f web    # watch the API/UI come up
```

`docker compose up -d` is the entire system: a one-shot `migrate` service (both
databases), the long-running `scheduler`, and the `web` service (FastAPI serving the
built React SPA same-origin). Open **http://localhost:8000** on the host, or
`http://<machine-ip>:8000` from any other device on the same network — no
terminal, no file editing, no login.

Reaching the UI by a bare LAN IP needs no configuration. To reach it through a DNS
name or reverse proxy, add that hostname to `HUNTLOOP_ALLOWED_HOSTS` (default
`localhost,127.0.0.1`). Requests whose host is neither a listed hostname nor a bare IP
literal are rejected as a DNS-rebinding defense, and cross-origin browser writes are
rejected as a CSRF defense.

To stop everything: `docker compose down` (add `-v` to also delete the data volume —
that erases your listings).

Compose itself refuses to start if `HUNTLOOP_SECRET_KEY` is unset — the same fail-fast
check the application performs.

## Configure your LLM API key

Criteria onboarding, scoring, and proposal rationales all call the model, so **set an
API key before onboarding**. Open **http://localhost:8000/settings → API access** and
enter your OpenAI-compatible key and base URL. The key is encrypted into a separate
credentials database and never written to the main database.

Alternatively, for a headless/CLI setup, set `HUNTLOOP_OPENAI_API_KEY` (and optionally
`HUNTLOOP_OPENAI_BASE_URL`) in `.env` — the credentials store wins if it has a key.

A base URL must be `https://` and resolve to a public address, because the stored key
is sent there; private/loopback endpoints (e.g. a local Ollama) require opting in with
`HUNTLOOP_ALLOW_PRIVATE_ENDPOINT=1` on a network you control.

### Use Codex OAuth through a local gateway

The optional gateway lets HuntLoop use your authenticated Codex CLI without reading or
copying its OAuth credentials. Install the official Codex CLI, run `codex login`, then
start the gateway on the host:

```bash
export CODEX_GATEWAY_KEY="$(openssl rand -hex 32)"
uv run uvicorn huntloop.codex_gateway:app --host 127.0.0.1 --port 8787
```

Set these values in `.env`, restart HuntLoop, then save the same gateway key and base
URL in **Settings → API access**. The saved Settings key takes precedence over `.env`.

```env
HUNTLOOP_LLM_PROVIDER=codex_gateway
HUNTLOOP_OPENAI_BASE_URL=http://host.docker.internal:8787/v1
HUNTLOOP_OPENAI_API_KEY=<same CODEX_GATEWAY_KEY>
HUNTLOOP_ALLOW_PRIVATE_ENDPOINT=1
HUNTLOOP_TRIAGE_MODEL=codex
HUNTLOOP_SCORING_MODEL=codex
HUNTLOOP_EXTRACTION_MODEL=codex
```

```bash
docker compose up -d --force-recreate web scheduler
```

The `codex` model alias uses the CLI's default model. To expose explicit model names,
set `CODEX_GATEWAY_MODELS` on the host to a comma-separated list. Before using the
gateway, confirm the container can reach it:

```bash
docker compose exec web python -c \
  'import urllib.request as u; r=u.Request("http://host.docker.internal:8787/v1/models", headers={"Authorization":"Bearer <same CODEX_GATEWAY_KEY>"}); print(u.urlopen(r).status)'
```

If OrbStack cannot reach a loopback-bound service, bind the gateway to the host's
private interface and protect it with the generated gateway key. Codex-backed models
are reported as unpriced unless they exist in HuntLoop's static pricing table, so a
USD spend cap cannot enforce their subscription usage. Each request starts a Codex agent
session and carries substantial agent-context overhead; use this gateway for local
experimentation, not high-volume listing runs.

## The browser interface

Seven sections, all reachable from the left nav:

- **Dashboard** (`/`): the pipeline funnel by stage, the next scheduled run, the last
  run's outcome and cost, and a **Run now** button for an on-demand run. A quiet week
  is explained here rather than left ambiguous.
- **Listings** (`/listings`): a dense table of every scored listing with a composable
  filter bar (status, employer, score threshold, date range). Open a row's drawer for
  the per-dimension score breakdown, reasoning, notes, open duration and repost count,
  and the status timeline. Change status in one click from the row or the drawer — that
  transition is the feedback signal.
- **Employers** (`/employers`): the registry with platform + board id for resolved
  employers and a per-employer **Retry** for the ones needing attention. Disable an
  employer in place without losing its history.
- **Criteria** (`/criteria`): first-run onboarding lives here. Describe in a paragraph
  what you are looking for; the model extracts a structured draft, which you review and
  correct in an editable form (locations, seniority, compensation floor + currency,
  exclusions, work authorization), then drag-order the four scoring dimensions. Saving
  creates criteria **version 1**. The same flow proposes employers to track; you check
  the ones you want, and resolution runs on those only, with an honest coverage
  headline ("we can watch N of the ~M we'd target"). Later visits show the editable
  form plus diffable version history.
- **Proposals** (`/proposals`): bounded, evidence-backed criteria changes the system
  proposes from what actually happened to your surfaced listings. Each card shows what
  would change, why, the listings behind it, and the predicted effect on your backlog.
  **Approve change** writes a new criteria version; **Reject change** records a reason
  (a later similar proposal shows that you rejected one before). A feedback box lets
  you leave written guidance for the next proposal generation. Nothing changes without
  your explicit approval.
- **Runs** (`/runs`): run history and per-run detail, including failures.
- **Settings** (`/settings`): API access, per-stage model choice, schedule
  (time + timezone), and spend cap. Each section saves independently; a schedule change
  takes effect without restarting the scheduler. **Run diagnostics** checks API access,
  database connectivity, and a live employer fetch, streaming a result with a readable
  remedy for each.

## CLI and advanced usage

The CLI is the builder/operator harness. Everything below runs through the `app`
service so it shares the same databases:

```bash
# Optional: manage criteria as a file instead of through the UI
cp criteria.example.yml criteria.yml      # then edit the fields it marks
docker compose run --rm -v "$(pwd)/criteria.yml:/data/criteria.yml" app criteria load /data/criteria.yml

# Track an employer
docker compose run --rm app company add "Cobalt" --url https://cobalt.io/careers

# Free discovery pass, no LLM scoring and no API key needed
docker compose run --rm app run --no-score

# Real scoring pipeline
docker compose run --rm app run

# View scored jobs
docker compose run --rm app jobs list
```

The `-v` flag on `criteria load` is required: `/data` inside the container is a named
Docker volume (it holds the databases), not a bind mount of your working directory, so
the file must be mounted in explicitly. Outside Docker the same command is
`huntloop criteria load criteria.yml`.

If you see a `RendererUnavailable` error for the crawler locally outside Docker, make
sure you ran `playwright install --with-deps chromium`.

## Unattended scheduling

Run discovery on a daily schedule without anyone at the terminal:

```bash
docker compose up -d scheduler          # daily run at HUNTLOOP_RUN_AT in HUNTLOOP_TIMEZONE
```

For a local foreground scheduler (blocks until Ctrl-C): `huntloop scheduler start`.

Check what the scheduler has been doing — every run's trigger, status, per-stage counts
and cost, without opening the database:

```bash
huntloop run history          # last 10 runs, newest first
huntloop run history --json   # same fields, machine-readable
```

Configuration (`HUNTLOOP_RUN_AT`, `HUNTLOOP_TIMEZONE`, `HUNTLOOP_RUN_SPEND_CAP_USD`),
catch-up after downtime, and troubleshooting are covered in
[`docs/operations/scheduling.md`](docs/operations/scheduling.md).

## Guarantees worth testing yourself

1. Data persists correctly across a full container stop, recreation, and restart cycle.
   → `bash scripts/verify_persistence_cycle.sh`
2. The system runs against Postgres by changing one configuration value, with no code or schema change.
   → `pytest tests/test_portability.py -x`
3. A raw copy of the primary database file, opened independently, contains no usable LLM API credential.
   → `pytest tests/test_credentials.py -x` and step 7 of the cycle script

## Local development

The repository ships a `uv.lock`; `uv` is the fastest path, but a plain venv works too.

```bash
uv sync --extra dev          # or: python3.12 -m venv .venv && .venv/bin/pip install -e ".[dev]"

uv run pytest tests/ -x -m "not postgres"   # fast, no Docker needed
uv run pytest tests/ -x                     # full, includes a real Postgres container
```

Frontend (React + Vite) lives in `web/`:

```bash
cd web && npm install
npm run test        # vitest
npm run dev         # Vite dev server, proxies /api to localhost:8000
```

## Database migrations

HuntLoop has two named migration environments because the credentials store is a
physically separate database:

```bash
alembic --name main_db upgrade head          # 9 application tables
alembic --name credentials_db upgrade head   # 1 encrypted-secrets table, separate file
```

Migrations run automatically via the `migrate` Compose service, so these are only
needed for local development. The two histories are deliberately independent — they
never share a migration chain.

## Documentation

- [`docs/architecture/data-model.md`](docs/architecture/data-model.md)
- [`docs/operations/backup-restore.md`](docs/operations/backup-restore.md)
- [`docs/operations/database-backends.md`](docs/operations/database-backends.md)
- [`docs/operations/scheduling.md`](docs/operations/scheduling.md)
