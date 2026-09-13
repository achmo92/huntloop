"""GAP-15: schema-neutral run liveness and stale-run reconciliation.

A RUNNING Run row can outlive the process that owned it: a crash or a container
restart leaves the row ``running`` forever, the cooperative stop marker (GAP-4)
names a run no live process observes, and Phase 3's overlap guard keys on that
same row -- so a single orphan blocks every manual and scheduled run.

Liveness is a per-run ``Setting`` lease (``run_lease:{run_id}``) holding an
ISO-8601 UTC timestamp, refreshed by a serial daemon heartbeat started from the
shared ``run_discovery`` entrypoint (CLI, API daemon thread, and scheduler). A
RUNNING row whose lease is older than the configurable threshold -- falling back
to ``started_at`` for pre-deploy rows with no lease -- is stale.
:func:`reconcile_stale_runs` finalizes stale rows to a terminal status with an
honest reason and clears their lease.

This module deliberately imports only ``db``/``config`` and never
``huntloop.graph``: ``huntloop.scheduler.jobs`` imports it at module top, and
must not pull langgraph into its small import surface (OPS-02).
"""

from __future__ import annotations

import logging
import threading
import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from huntloop.config import load_config
from huntloop.db.models import Run, RunStatus, Setting
from huntloop.db.repository import RunRepository, SettingsRepository

logger = logging.getLogger(__name__)

# The Setting key prefix holding a run's lease timestamp.
RUN_LEASE_SETTING_PREFIX = "run_lease:"

# How often the heartbeat refreshes a live run's lease. The stale threshold is
# 8x this, so jitter and a few missed beats never falsely kill a live run while
# a genuinely dead run unblocks within ~2 minutes.
HEARTBEAT_INTERVAL_SECONDS = 15

# Automatic reconciliation is not a user action: the process ended without
# completing, so the objective fact is FAILED (D-15e).
INTERRUPTED_RUN_REASON_PREFIX = (
    "run interrupted: the process ended before the run completed"
)

# The stop endpoint's stale path: the user did ask to stop, and the reason makes
# clear the run had already ended (D-15e).
STOPPED_STALE_REASON = (
    "stopped: the run was no longer active (its process had already ended)"
)

# GAP-16: the canonical cooperative-stop keys live in this db-only module so the
# scheduler can run the grace sweep at module top without importing
# ``huntloop.graph``. ``graph/cancellation.py`` imports and re-exports them.
#
# The marker value is the string run id (GAP-4, unchanged); the additive
# ``run_stop_requested_at:{run_id}`` Setting holds the ISO-8601 UTC timestamp
# the stop was requested. Both live in the existing ``settings`` table, so no
# model change and no Alembic revision are needed.
STOP_REQUEST_SETTING_KEY = "run_stop_request"
STOP_REQUESTED_AT_SETTING_PREFIX = "run_stop_requested_at:"

# A user-initiated stop that the run thread could not honor within the grace
# window is finalized STOPPED, not FAILED (D-16a): the user asked, the run just
# did not reach a checkable boundary in time.
GRACE_EXPIRED_STOP_REASON = (
    "stopped: the run did not reach its next safe boundary within the stop "
    "grace period"
)


def lease_key(run_id: uuid.UUID | str) -> str:
    """The per-run lease Setting key."""
    return f"{RUN_LEASE_SETTING_PREFIX}{run_id}"


def stop_requested_at_key(run_id: uuid.UUID | str) -> str:
    """The Setting key holding a run's stop-requested-at timestamp."""
    return f"{STOP_REQUESTED_AT_SETTING_PREFIX}{run_id}"


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def record_run_heartbeat(session, run_id: uuid.UUID | str, *, now=None) -> None:
    """Write the run's lease timestamp. The caller commits."""
    value = _as_utc(now or datetime.now(UTC)).isoformat()
    SettingsRepository(session).set_value(lease_key(run_id), value)


def clear_run_lease(session, run_id: uuid.UUID | str) -> None:
    """Delete the run's lease row if present (mirrors clear_stop_request)."""
    setting = session.get(Setting, lease_key(run_id))
    if setting is not None:
        session.delete(setting)


def _parse_timestamp(raw) -> datetime | None:
    """Parse an ISO-8601 Setting value to an aware UTC datetime, or None."""
    if not isinstance(raw, str):
        return None
    try:
        return _as_utc(datetime.fromisoformat(raw))
    except ValueError:
        return None


def _delete_setting(session, key: str) -> None:
    setting = session.get(Setting, key)
    if setting is not None:
        session.delete(setting)


def lease_timestamp(session, run) -> datetime:
    """The run's last-seen time: its lease, or ``started_at`` when absent (D-15d).

    The fallback is what reconciles the pre-deploy orphaned rows the moment this
    ships: they have no lease, so they are judged from when they started.
    """
    parsed = _parse_timestamp(SettingsRepository(session).get_value(lease_key(run.id)))
    return parsed if parsed is not None else _as_utc(run.started_at)


def run_is_live(
    session, run, *, now=None, stale_after_seconds: int | None = None
) -> bool:
    """True when a RUNNING run's last-seen time is within the threshold.

    A future lease yields a non-positive age and is therefore live. A terminal
    run is never live, even if a lease lingers.
    """
    if run.status is not RunStatus.RUNNING:
        return False
    threshold = (
        stale_after_seconds
        if stale_after_seconds is not None
        else load_config().run_stale_after_seconds
    )
    now_value = _as_utc(now or datetime.now(UTC))
    return (now_value - lease_timestamp(session, run)).total_seconds() <= threshold


def finalize_stale_run(session, run, *, status: RunStatus, reason: str) -> Run | None:
    """Finalize an orphaned run terminal and clear its lease. Caller commits.

    An orphan's true spend is unknowable, so every counter is zero and the
    reason names the interruption. Uses the conditional finish so a row that
    another writer already made terminal is never overwritten (GAP-16).
    """
    finished = RunRepository(session).finish_if_running(
        run.id,
        status=status,
        companies_checked=0,
        listings_fetched=0,
        after_dedup=0,
        after_deterministic=0,
        after_triage=0,
        scored=0,
        new_jobs_written=0,
        tokens_in=0,
        tokens_out=0,
        cost_usd=0,
        error_summary=reason,
    )
    clear_run_lease(session, run.id)
    return finished


def reconcile_stale_runs(
    session, *, now=None, stale_after_seconds: int | None = None
) -> list[uuid.UUID]:
    """Finalize every stale RUNNING row; return the ids finalized.

    Commits once iff anything changed, so the caller's session is durable before
    the overlap guard runs. Live RUNNING rows and terminal rows are untouched.
    """
    runs = (
        session.execute(select(Run).where(Run.status == RunStatus.RUNNING))
        .scalars()
        .all()
    )
    finalized: list[uuid.UUID] = []
    for run in runs:
        if run_is_live(session, run, now=now, stale_after_seconds=stale_after_seconds):
            continue
        last_seen = lease_timestamp(session, run).isoformat()
        finalize_stale_run(
            session,
            run,
            status=RunStatus.FAILED,
            reason=f"{INTERRUPTED_RUN_REASON_PREFIX} (last seen {last_seen})",
        )
        finalized.append(run.id)
    if finalized:
        session.commit()
    return finalized


def finalize_grace_expired_stops(
    session, *, now=None, grace_seconds: int | None = None
) -> list[uuid.UUID]:
    """Finalize RUNNING runs whose stop was not honored within the grace window.

    This is the deterministic half of GAP-16: a run thread stuck in a bounded but
    long stage is not waited on. The stop's ``requested_at`` timestamp is read
    from the additive Setting, and once it is older than the grace window the
    row is finalized STOPPED via ``finish_if_running`` (never overwriting a row
    another writer already made terminal). The lease, the requested-at key, and
    the marker (only when it still names this run) are all cleared so the
    Phase 3 overlap guard releases.

    Durable and cross-process by construction: it is a DB sweep driven by the
    same gates as stale reconciliation (GET/POST ``/api/runs`` and the scheduler
    before its overlap check), so no in-memory timer is involved and it works
    for both API-owned and scheduler-owned runs. Commits once iff it finalized
    anything.
    """
    threshold = (
        grace_seconds
        if grace_seconds is not None
        else load_config().run_stop_grace_seconds
    )
    now_value = _as_utc(now or datetime.now(UTC))
    runs = (
        session.execute(select(Run).where(Run.status == RunStatus.RUNNING))
        .scalars()
        .all()
    )
    repo = RunRepository(session)
    settings = SettingsRepository(session)
    finalized: list[uuid.UUID] = []
    for run in runs:
        requested_at = _parse_timestamp(
            settings.get_value(stop_requested_at_key(run.id))
        )
        if requested_at is None:
            continue
        if (now_value - requested_at).total_seconds() <= threshold:
            continue

        finished = repo.finish_if_running(
            run.id,
            status=RunStatus.STOPPED,
            companies_checked=0,
            listings_fetched=0,
            after_dedup=0,
            after_deterministic=0,
            after_triage=0,
            scored=0,
            new_jobs_written=0,
            tokens_in=0,
            tokens_out=0,
            cost_usd=0,
            error_summary=GRACE_EXPIRED_STOP_REASON,
        )
        if finished is None:
            # Someone else finalized it between the SELECT and now.
            continue

        clear_run_lease(session, run.id)
        _delete_setting(session, stop_requested_at_key(run.id))
        if settings.get_value(STOP_REQUEST_SETTING_KEY) == str(run.id):
            _delete_setting(session, STOP_REQUEST_SETTING_KEY)
        finalized.append(run.id)

    if finalized:
        session.commit()
    return finalized


class RunHeartbeat:
    """Handle to the daemon thread refreshing a run's lease."""

    def __init__(self, thread: threading.Thread, stop_event: threading.Event) -> None:
        self.thread = thread
        self._stop_event = stop_event

    def stop(self) -> None:
        """Signal the heartbeat loop to exit after its current wait."""
        self._stop_event.set()

    def join(self, timeout: float | None = None) -> None:
        self.thread.join(timeout)


def _beat_best_effort(sessionmaker, run_id: uuid.UUID | str) -> None:
    """One heartbeat write on its own session; never raises.

    A missed beat is tolerated by the generous threshold and must never surface
    inside the daemon thread.
    """
    session = sessionmaker()
    try:
        record_run_heartbeat(session, run_id)
        session.commit()
    except Exception:
        logger.debug("run lease heartbeat failed for %s", run_id, exc_info=True)
        try:
            session.rollback()
        except Exception:
            logger.debug("rollback after a failed heartbeat also failed", exc_info=True)
    finally:
        session.close()


def start_run_heartbeat(
    sessionmaker, run_id: uuid.UUID | str, *, interval: float = HEARTBEAT_INTERVAL_SECONDS
) -> RunHeartbeat:
    """Start a daemon thread refreshing ``run_id``'s lease every ``interval``.

    One serial writer (not per-employer-boundary writes) avoids cross-branch
    SQLite write contention when the employer fan-out runs concurrently. The
    run's lease is also written synchronously with the Run row at start, so a
    live run is never briefly unclassifiable.
    """
    stop_event = threading.Event()

    def _beat_loop() -> None:
        while not stop_event.wait(interval):
            _beat_best_effort(sessionmaker, run_id)

    thread = threading.Thread(
        target=_beat_loop, daemon=True, name="huntloop-run-heartbeat"
    )
    thread.start()
    return RunHeartbeat(thread, stop_event)
