# Backing up and restoring HuntLoop

## What lives where

| Thing | Default location | Contains | Safe to copy off the host? |
|---|---|---|---|
| Main database | `$HUNTLOOP_DATA_DIR/huntloop.db` (default `/data/huntloop.db` in Docker) | Employers, listings, scores, criteria, runs, notes | **Yes** — contains no credentials |
| Credentials database | `$HUNTLOOP_DATA_DIR/credentials.db` | Your LLM API key, encrypted | Only alongside a separate copy of the key — useless on its own |
| Encryption key | The `HUNTLOOP_SECRET_KEY` environment variable | The key that decrypts `credentials.db` | **Never store it in the same place as `credentials.db`** |
| Sidecar files | `huntloop.db-wal`, `huntloop.db-shm` (and the same for `credentials.db`) | Recent writes not yet folded into the main file | Copy them together with their `.db` file or the copy may be stale |

## The one rule

The encryption key must never travel with the data it protects. A full-volume backup that also captures the key carries both the lock and the key, which defeats the entire separation. `HUNTLOOP_SECRET_KEY` belongs in your deployment's environment configuration (Docker Compose `.env`, a systemd unit, your host's secret manager) and that file must not be inside `$HUNTLOOP_DATA_DIR`.

## Generating the key (one time, at setup)

```bash
openssl rand -base64 32
```

Run this once, store the output as `HUNTLOOP_SECRET_KEY` in your deployment environment, and keep a copy somewhere you would keep a password. HuntLoop will never generate this for you and will refuse to start without it — that refusal is deliberate, because an auto-generated key written into the data volume would be backed up together with the data.

## Backing up

Stop the app first so no write is in flight:

```bash
docker compose stop
cp "$HUNTLOOP_DATA_DIR/huntloop.db"      /path/to/backups/
cp "$HUNTLOOP_DATA_DIR/huntloop.db-wal"  /path/to/backups/ 2>/dev/null || true
cp "$HUNTLOOP_DATA_DIR/credentials.db"   /path/to/backups/
docker compose start
```

If you would rather not stop the app, use `sqlite3 "$HUNTLOOP_DATA_DIR/huntloop.db" ".backup /path/to/backups/huntloop.db"`, which takes a consistent snapshot of a live database.

It is fine to put `huntloop.db` in cloud storage; think twice before putting `credentials.db` there, and never put `HUNTLOOP_SECRET_KEY` in the same place.

## Restoring

1. Stop the stack.
2. Copy the `.db` files back into `$HUNTLOOP_DATA_DIR`.
3. Confirm `HUNTLOOP_SECRET_KEY` in the environment is the SAME value that was in use when the backup was taken.
4. Start the stack — migrations run automatically before anything else starts, so an older backup is upgraded to the current schema on first boot.

## If you lost the key

HuntLoop will fail to read your stored API key and report:

`Stored credential 'llm_api_key' could not be decrypted — HUNTLOOP_SECRET_KEY is wrong or was rotated.`

Recovery is cheap and safe: set a NEW `HUNTLOOP_SECRET_KEY`, delete `credentials.db`, restart, and re-enter your API key through the settings screen. Nothing in `huntloop.db` is affected — no listings, scores, or notes are lost. This is why the credentials store is deliberately tiny.

## If you lost the main database

Job history is gone and cannot be reconstructed; re-register employers and re-run discovery. Current open listings will be re-discovered; pipeline statuses and notes will not. Back up `huntloop.db` on whatever cadence matches how much manual pipeline state you would hate to re-enter.

## What is NOT a safe backup

- A `git commit` of the data directory — `data/` and `*.db` are in `.gitignore` for this reason; do not force-add them.
- Copying only `huntloop.db` while a run is in progress without using `.backup` — the `-wal` sidecar holds recent writes.
- A whole-host image or Time Machine snapshot that also captures the file holding `HUNTLOOP_SECRET_KEY` — that single artifact carries both halves.
- Sharing a backup with someone to "help debug" — `huntloop.db` is credential-free but is a full record of your job search.
