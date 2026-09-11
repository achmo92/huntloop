# Scheduling and run health

How HuntLoop runs discovery unattended, and how to tell a working quiet week from a broken one. This is the operator's reference for the scheduler (`RUN-01`, `RUN-04`), the spend cap (`RUN-08`), and reading run history (`RUN-05`, `RUN-07`).

## What runs, and when

Exactly **one discovery run per day**, at the wall-clock time set by `HUNTLOOP_RUN_AT`, interpreted in the timezone set by `HUNTLOOP_TIMEZONE`. With the defaults below, that is 08:00 UTC every day. There is no interval mode and no cron expression — a daily time-of-day in your own timezone is the entire schedule model.

Internally HuntLoop stores and reasons in UTC; your timezone is used only to interpret `HUNTLOOP_RUN_AT` when the fire time is computed. Daylight-saving transitions are handled by the scheduler library, not by hand-rolled clock arithmetic.

## Configuration

All three variables live in your `.env` (see `.env.example`). They are validated **once at startup** — a typo is a startup error, never a silent fallback to a default.

| Variable | Format | Default | On an invalid value |
|---|---|---|---|
| `HUNTLOOP_RUN_AT` | 24-hour `HH:MM` (e.g. `08:00`) | `08:00` | `ConfigError` at startup, exit code 2 |
| `HUNTLOOP_TIMEZONE` | IANA name (e.g. `America/New_York`) | `UTC` | `ConfigError` at startup, exit code 2 |
| `HUNTLOOP_RUN_SPEND_CAP_USD` | Decimal USD (e.g. `2.00`), blank = no cap | *(blank)* | `ConfigError` at startup, exit code 2 |

The exit code 2 is the CLI's "aborted before doing anything" code — the scheduler refuses to start on a bad value rather than running at a wrong time or with a wrong cap.

## Starting it

The normal path, in Docker:

```bash
docker compose up -d scheduler
docker compose logs -f scheduler   # watch it come up
```

The `scheduler` service is the only long-running service in the stack (`restart: unless-stopped`), so it survives host reboots. It runs no browser window and needs no display; the crawl path inside a run launches Chromium headless.

For a local foreground run (blocks until Ctrl-C):

```bash
huntloop scheduler start
```

On startup it prints the configured schedule, e.g. `huntloop scheduler: daily discovery at 08:00 UTC`, then blocks. A stray one-off run outside the schedule is always available as `huntloop run`.

## Catch-up after downtime

If the scheduled time was missed while the process was down — container restart, host sleep, laptop lid closed — **exactly one catch-up run fires on restart**, no matter how long the gap was. A three-day outage produces one catch-up run, not three.

This is deliberate, and it is implemented by two APScheduler settings you should not "fix":

- `coalesce=True` — N missed fires collapse into exactly one.
- `misfire_grace_time=None` (infinite) — a missed fire is never dropped for being "too late".

A catch-up run is distinguishable in history: its trigger is `catch_up`, not `scheduled` (the cutoff is 10 minutes past the scheduled occurrence — later than that and the fire is classified as post-downtime).

## Changing the schedule

Edit `HUNTLOOP_RUN_AT` / `HUNTLOOP_TIMEZONE` in your environment and restart the service:

```bash
docker compose up -d scheduler   # recreates the container with the new env
```

On startup the scheduler compares the persisted job's trigger (time *and* timezone) against the newly configured one. On a change it drops the old job and installs the new one with a fresh next-run time. Any pending catch-up belonging to the old schedule is **dropped** with it — the schedule that catch-up was for no longer exists. Restarting *without* changing the variables leaves the persisted next-run time untouched (that is what preserves catch-up).

## Reading run health

```bash
huntloop run history            # last 10 runs, newest first
huntloop run history --limit 3
huntloop run history --json     # same fields, machine-readable (Phase 4's API wraps this)
```

Real output from a database with four runs (seeded test data):

```
2026-09-10 09:30  manual     success   written=0    scored=0    fetched=139   $0.0000
2026-09-10 06:12  catch_up   skipped   written=0    scored=0    fetched=0     $0.0000
        ! skipped: a previous run was still in progress (run 6f1c2a41-9b3e-4c8d-a2f5-77e0c1b3d902, started 2026-09-10T08:00:00+00:00)
2026-09-09 08:00  scheduled  capped    written=9    scored=14   fetched=138   $3.0000
        ! stopped: spend cap of $3.00 reached after $3.75 of model spend
2026-09-08 08:00  scheduled  success   written=12   scored=31   fetched=142   $0.3127
```

Reading it: the newest run was manual, succeeded, and found nothing new worth writing (`fetched=139`, `written=0` — it ran and the market was quiet). Below it, a catch-up that never started because a previous run was still going. Below that, a scheduled run stopped by the spend cap, and a clean scheduled run.

An empty history states the fact (`no runs recorded yet. Start the scheduler with ...`) and exits 0 — an empty database is a valid answer, not an error.

What each status means (the `RunStatus` values; history shows them lowercased, as above):

| Status | Meaning |
|---|---|
| `SUCCESS` | Every employer was checked; nothing failed. (Written count can still be 0 — that is an empty market, not an error.) |
| `PARTIAL` | The run finished but some employers failed (network errors, blocked boards). Their errors are listed in the run's details; other employers were unaffected. |
| `FAILED` | The run could not complete at all. The run's spend so far is still recorded. |
| `SKIPPED` | A scheduled fire that did not start because the previous run was still in progress. The reason names the run it yielded to. |
| `CAPPED` | Scoring stopped mid-run because `HUNTLOOP_RUN_SPEND_CAP_USD` was reached. Deliberate budget stop, not a failure. |
| `RUNNING` | The run is in progress right now. A row stuck in this state is a troubleshooting signal — see below. |

## The spend cap

`HUNTLOOP_RUN_SPEND_CAP_USD` is a hard ceiling on model spend within **one run**. It is checked before every individual triage and scoring model call, so the run stops *before* spending past the cap rather than reacting after.

When it trips:

- The listing being processed at that moment is **not written**. Listings not yet scored are held back, and the next run picks them up naturally through normal dedup — nothing is lost, nothing needs a manual resume.
- The run is recorded with status `CAPPED`, and the reason travels in the run's details (e.g. `stopped: spend cap of $3.00 reached after $3.75 of model spend`) — visible in `huntloop run history` on the line beneath the run.
- Genuine employer errors still outrank the cap: a run with real failures is `PARTIAL`, not `CAPPED`, because a failure is more actionable than a budget stop.

Leave `HUNTLOOP_RUN_SPEND_CAP_USD` blank to disable the cap; cost is still measured and recorded on every run either way.

## Troubleshooting

**The scheduler container restarts in a loop.** Check `docker compose logs scheduler`. The usual cause is a `ConfigError` naming the offending variable — a malformed `HUNTLOOP_RUN_AT` (must be `HH:MM`), an unknown `HUNTLOOP_TIMEZONE` (must be an IANA name like `Europe/Berlin`), or a non-numeric `HUNTLOOP_RUN_SPEND_CAP_USD`. Fix `.env`, then `docker compose up -d scheduler` again. The container refusing to run on bad config is the designed behaviour, not a bug.

**Every scheduled run shows `skipped`: a previous run was still in progress.** Look for a `running` row that never finishes — that is a run whose process died mid-flight (hard kill, OOM, power loss; an ordinary crash is marked `failed` by the pipeline itself). While any `running` row exists, every new scheduled fire records a `skipped` row and does no work. There is currently no automatic reaper and no CLI command to close a stale `running` row; close it directly against the database, e.g. for the default SQLite backend:

```bash
sqlite3 "$HUNTLOOP_DATA_DIR/huntloop.db" \
  "UPDATE runs SET status = 'failed', finished_at = CURRENT_TIMESTAMP WHERE status = 'running' AND started_at < datetime('now', '-1 hour');"
```

**"No runs happened" vs "runs happened and found nothing."** These look identical from the outside and are opposites in meaning. `huntloop run history` makes them unambiguous: rows with `fetched=142, written=0` mean runs happened and the market was quiet; no rows at all (the `no runs recorded yet` message) means the scheduler is not running — check `docker compose ps scheduler` and its logs.
