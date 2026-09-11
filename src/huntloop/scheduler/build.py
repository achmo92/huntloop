"""RUN-01 / RUN-04. The daily discovery job and the scheduler that owns it.

This module must never import the browser renderer, the rendered-page fetcher,
or anything that would require a display (OPS-02). It only builds a scheduler;
the work happens inside huntloop.scheduler.jobs, resolved by APScheduler from a
textual reference.
"""

from __future__ import annotations

import logging
from datetime import datetime, timezone as dt_timezone

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from huntloop.config import Config, load_config, parse_run_at

logger = logging.getLogger(__name__)

JOB_ID = "daily_discovery"
# A TEXTUAL reference, not a callable. apscheduler.job.Job.__getstate__ refuses to
# pickle anything it cannot name as module:qualname, which rules out lambdas,
# functools.partial and bound methods -- and is why run_scheduled_discovery takes
# no arguments and builds its own sessionmaker.
JOB_FUNC_REF = "huntloop.scheduler.jobs:run_scheduled_discovery"


def build_trigger(cfg: Config) -> CronTrigger:
    """One fire per day at cfg.run_at, interpreted in cfg.timezone.

    CronTrigger.timezone accepts a plain IANA string (apscheduler.util.astimezone
    converts it via zoneinfo). DST transitions are its problem, not ours -- which
    is the entire reason PITFALLS.md Pitfall 6 forbids a hand-rolled sleep loop.
    """
    hour, minute = parse_run_at(cfg.run_at)
    return CronTrigger(hour=hour, minute=minute, timezone=cfg.timezone)


def _trigger_fingerprint(trigger) -> tuple[str, str]:
    """Comparable identity of a trigger: its fields AND its timezone.

    str(CronTrigger) renders only the field expressions, so two triggers at the
    same clock time in different zones stringify identically -- the timezone must
    be compared separately or a HUNTLOOP_TIMEZONE change would go undetected.
    """
    return (str(trigger), str(getattr(trigger, "timezone", "")))


def build_scheduler(
    cfg: Config | None = None,
    *,
    engine=None,
    scheduler_factory=BlockingScheduler,
):
    """Build a scheduler with the daily discovery job installed, ready to .start().

    The add_job call is GUARDED. Unconditionally re-adding the job on every
    process boot (the shape that reads as idiomatic APScheduler) silently
    destroys RUN-04: _real_add_job recomputes next_run_time as "the next
    occurrence after right now" for any Job that has no explicit next_run_time,
    and then update_job overwrites the persisted, possibly-past-due value. After
    a three-day gap the job would quietly reschedule for tomorrow and no
    catch-up would ever run.

    scheduler.get_job() cannot be used for the guard: while the scheduler is
    STOPPED it only looks in _pending_jobs and never consults the jobstore, so it
    always returns None and the guard never fires. Query the jobstore directly.
    """
    cfg = cfg or load_config()
    if engine is None:
        from huntloop.db.base import get_engine

        engine = get_engine()

    jobstore = SQLAlchemyJobStore(engine=engine)
    # The SCHEDULER's own clock stays UTC; the TRIGGER carries the user's zone.
    scheduler = scheduler_factory(jobstores={"default": jobstore}, timezone="UTC")

    # Start the store by hand so it can be queried before scheduler.start().
    # Idempotent: scheduler.start() starts it again, and SQLAlchemyJobStore.start
    # is Table.create(engine, checkfirst=True) plus attribute assignment.
    jobstore.start(scheduler, "default")

    wanted = build_trigger(cfg)
    existing = jobstore.lookup_job(JOB_ID)

    if existing is not None and _trigger_fingerprint(existing.trigger) != _trigger_fingerprint(
        wanted
    ):
        # The operator deliberately changed HUNTLOOP_RUN_AT/HUNTLOOP_TIMEZONE.
        # Honour it: drop the stale job so it is re-added below with a fresh
        # next_run_time. This forfeits a pending catch-up, which is the right
        # trade -- the schedule the catch-up belonged to no longer exists.
        logger.info(
            "schedule changed (%s -> %s); rescheduling %s",
            _trigger_fingerprint(existing.trigger),
            _trigger_fingerprint(wanted),
            JOB_ID,
        )
        jobstore.remove_job(JOB_ID)
        existing = None

    if existing is None:
        scheduler.add_job(
            JOB_FUNC_REF,
            trigger=wanted,
            id=JOB_ID,
            name="HuntLoop daily discovery",
            misfire_grace_time=None,  # INFINITE. Any finite value drops the
                                      # catch-up once the gap exceeds it, and
                                      # "laptop closed for days" is in scope.
            coalesce=True,            # N missed fires collapse to exactly ONE.
            max_instances=1,          # Same-process re-entry guard. Not the
                                      # RUN-03 mechanism -- see jobs.py.
        )
    else:
        logger.info(
            "%s already scheduled for %s; leaving its next_run_time untouched",
            JOB_ID,
            existing.next_run_time,
        )

    return scheduler


def next_fire_time(cfg: Config | None = None, *, after: datetime | None = None) -> datetime:
    """Next UTC fire instant for the configured schedule. Used by tests and docs."""
    cfg = cfg or load_config()
    after = after or datetime.now(dt_timezone.utc)
    return build_trigger(cfg).get_next_fire_time(None, after)
