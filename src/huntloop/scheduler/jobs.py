"""RUN-03 / RUN-04 / RUN-05. What the scheduled job actually does.

Kept separate from build.py because APScheduler persists a TEXTUAL reference to
run_scheduled_discovery and re-imports it in a worker thread. The function must
therefore be top-level, zero-argument, and must construct its own sessionmaker --
a sessionmaker passed via add_job(args=...) would fail to pickle on the next
process restart.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from huntloop.config import Config, load_config
from huntloop.db.base import get_engine, make_session_factory
from huntloop.db.models import Run, RunStatus, RunTrigger
from huntloop.db.repository import RunRepository
from huntloop.scheduler.build import build_trigger

logger = logging.getLogger(__name__)

# How far past a scheduled occurrence a fire may land and still count as "on time".
# Anything later is a post-downtime catch-up. Generous enough to absorb container
# start-up and clock skew, far tighter than the daily period it sits inside.
CATCHUP_SKEW = timedelta(minutes=10)

# Every counter RunRepository.finish() requires, for the terminal rows that never
# ran a pipeline (a skipped run). finish() has no defaults; omitting one is a TypeError.
_ZERO_COUNTERS = {
    "companies_checked": 0,
    "listings_fetched": 0,
    "after_dedup": 0,
    "after_deterministic": 0,
    "after_triage": 0,
    "scored": 0,
    "new_jobs_written": 0,
    "tokens_in": 0,
    "tokens_out": 0,
    "cost_usd": 0,
}


def classify_trigger(cfg: Config, now: datetime | None = None) -> RunTrigger:
    """SCHEDULED for an on-time fire, CATCH_UP for a post-downtime one.

    Derived from the trigger itself rather than from APScheduler's job-submitted
    event: that event is dispatched AFTER the executor has already submitted the
    job, so reading it from inside the job function is a race.
    """
    now = now or datetime.now(UTC)
    trigger = build_trigger(cfg)
    # For a daily trigger, the first occurrence at/after (now - 1 day) is the most
    # recent occurrence at/before now. The +1us matters: get_next_fire_time's
    # lower bound is INCLUSIVE, so without it a fire landing exactly on the
    # occurrence would find *yesterday's* occurrence and be misclassified as a
    # catch-up (found live by the RED test).
    previous = trigger.get_next_fire_time(
        None, now - timedelta(days=1) + timedelta(microseconds=1)
    )
    if previous is None or previous > now:
        return RunTrigger.SCHEDULED
    return RunTrigger.CATCH_UP if (now - previous) > CATCHUP_SKEW else RunTrigger.SCHEDULED


def find_run_in_progress(session) -> Run | None:
    """RUN-03's authoritative check: is ANY run currently RUNNING?

    Deliberately DB-level rather than APScheduler's max_instances=1. max_instances
    only prevents this scheduler's own job from re-entering itself; it cannot see a
    `huntloop run` the user started by hand in a separate process, which for a
    single-user deployment is the likelier overlap.

    A plain SELECT, not SELECT ... FOR UPDATE. The residual race (two processes
    checking at the same instant) is accepted for this phase; the portable
    partial-unique-index idiom already used for uq_criteria_single_active in
    db/models.py is the documented upgrade path if it ever bites.
    """
    return session.execute(
        select(Run).where(Run.status == RunStatus.RUNNING).limit(1)
    ).scalars().first()


def record_skipped_run(session, trigger: RunTrigger, reason: str) -> Run:
    """Write the skip as its own terminal Run row.

    A skip is a first-class queryable fact, not a log line -- that is the whole
    point of the phase goal: a quiet week must never be ambiguous.
    """
    repo = RunRepository(session)
    run = repo.start(trigger)
    repo.finish(run.id, status=RunStatus.SKIPPED, error_summary=reason, **_ZERO_COUNTERS)
    session.commit()
    return run


def execute_scheduled_run(
    sessionmaker, cfg: Config, *, now: datetime | None = None
) -> None:
    """The body of the scheduled job. Separated from the entrypoint so tests can
    drive it with an injected sessionmaker and clock."""
    trigger = classify_trigger(cfg, now=now)

    session = sessionmaker()
    try:
        in_progress = find_run_in_progress(session)
        if in_progress is not None:
            reason = (
                "skipped: a previous run was still in progress "
                f"(run {in_progress.id}, started {in_progress.started_at.isoformat()})"
            )
            logger.warning("%s", reason)
            record_skipped_run(session, trigger, reason)
            return
    finally:
        session.close()

    logger.info("starting %s discovery run", trigger.value)
    try:
        # Imported lazily: keeps huntloop.scheduler.jobs cheap to import and
        # keeps the OPS-02 import-boundary test's surface small. The from-import
        # resolves at call time, so tests monkeypatch huntloop.graph.build.run_discovery.
        from huntloop.graph.build import run_discovery

        summary = run_discovery(sessionmaker=sessionmaker, trigger=trigger)
    except Exception:  # noqa: BLE001
        # run_discovery already marks its own Run row FAILED before re-raising.
        # Swallow here so one bad day does not kill the daemon -- tomorrow's fire
        # must still happen.
        logger.exception("scheduled discovery run failed")
        return
    logger.info(
        "run %s finished: status=%s written=%s cost=%s",
        summary.run_id, summary.status, summary.new_jobs_written, summary.cost_usd,
    )


def run_scheduled_discovery() -> None:
    """Zero-argument, top-level, picklable entrypoint APScheduler stores by reference."""
    cfg = load_config()
    sessionmaker = make_session_factory(get_engine())
    execute_scheduled_run(sessionmaker, cfg)
