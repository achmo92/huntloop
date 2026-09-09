# Choosing a database backend

## The default: SQLite

HuntLoop ships on SQLite with WAL journaling, stored at `$HUNTLOOP_DATA_DIR/huntloop.db` (default `/data/huntloop.db` in Docker). Nothing to install, nothing to run, and it is the right choice for a laptop or any single machine with a persistent disk.

## When to switch to Postgres

- Your container platform has no persistent volume (Fargate, Cloud Run, Fly without a volume) — SQLite is unworkable there, not merely suboptimal.
- You want your provider's managed backups and point-in-time recovery for job data.
- You plan to run the agent and the interface on different hosts.

SQLite works across two containers sharing one local volume, but it is the weakest link in a hosted setup.

## Switching: one value

```bash
# Before (default)
DATABASE_URL=sqlite:////data/huntloop.db

# After
DATABASE_URL=postgresql+psycopg://huntloop:CHANGEME@db:5432/huntloop
```

No other change is needed. No code edit, no schema edit, no migration rewrite. On next start HuntLoop runs the same migrations against Postgres that it runs against SQLite.

Two constraints that will otherwise bite:

1. The URL MUST use the `postgresql+psycopg://` prefix (psycopg3). A bare `postgresql://` or a `postgresql+psycopg2://` URL is rejected at startup with a message naming the required prefix. SQLAlchemy 2.0 uses psycopg3 natively.
2. Install the driver: `pip install "huntloop[postgres]"` (which pins `psycopg[binary]==3.3.5`), or use the Docker image, which already includes it.

## What does NOT move to Postgres

`CREDENTIALS_DATABASE_URL` stays a local SQLite file (`$HUNTLOOP_DATA_DIR/credentials.db`) no matter what `DATABASE_URL` is set to, and HuntLoop refuses to start if it is pointed anywhere else.

Reason: a managed Postgres provider's automatic snapshots would then carry your encrypted API key, reintroducing exactly the "the backup carries the secret" problem the separate store exists to prevent. See `backup-restore.md` for the full backup/restore story. The tradeoff, stated honestly: the credentials store gets no vendor HA — acceptable, because recovery is re-entering one API key.

## Adding a Postgres service to Docker Compose

```yaml
services:
  db:
    image: postgres:16
    environment:
      POSTGRES_USER: huntloop
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD:?set POSTGRES_PASSWORD}
      POSTGRES_DB: huntloop
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U huntloop -d huntloop"]
      interval: 5s
      timeout: 5s
      retries: 10

  migrate:
    depends_on:
      db:
        condition: service_healthy

volumes:
  pgdata:
```

`migrate` must wait for `db` to report `service_healthy` via the Compose-native healthcheck above — never a hand-rolled retry/sleep loop. Every other service already waits for `migrate` to complete successfully before starting.

## Moving existing data from SQLite to Postgres

Honest and brief: there is no built-in migration path in v1. Point `DATABASE_URL` at the new Postgres, start the stack (migrations create an empty schema), then re-register employers and re-run discovery. Open listings are re-discovered; pipeline statuses and notes are not carried over. If that history matters, export it before switching. `credentials.db` is unaffected and needs no action.

## Verifying the switch

```bash
docker compose logs migrate                          # both migrations ran, exit code 0
docker compose --profile dev run --rm probe read     # reads a row back from the configured backend
```

`probe read` reports whether the expected row is readable from whichever backend is configured — it is the same command used to verify persistence across a container restart.
