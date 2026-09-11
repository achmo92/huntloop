"""RUN-01 / RUN-04 / OPS-02 scheduler-build tests. Owned by plan 03-03.

Owning test names (03-VALIDATION.md): test_cron_trigger_fires_at_configured_local_time,
test_catchup_fires_once_after_multi_day_gap, test_existing_job_not_readded_on_restart,
test_scheduler_module_has_no_playwright_import.
"""
import subprocess
import sys
import time
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine

from huntloop.config import load_config
from huntloop.scheduler.build import JOB_ID, build_scheduler, build_trigger


def make_cfg(**overrides):
    """A Config with the scheduler knobs overridden; everything else from the env."""
    return replace(load_config(), **overrides)


def probe_store(jobstore_url):
    """A manually-started jobstore for inspecting persistence between builds."""
    store = SQLAlchemyJobStore(url=jobstore_url)
    store.start(BackgroundScheduler(), "default")
    return store


def test_apscheduler_is_pinned_to_the_locked_version():
    import apscheduler

    assert apscheduler.__version__ == "3.11.3"


def test_cron_trigger_fires_at_configured_local_time():
    cfg = make_cfg(run_at="08:00", timezone="America/New_York")
    trigger = build_trigger(cfg)

    assert str(trigger) == "cron[hour='8', minute='0']"
    assert trigger.timezone == ZoneInfo("America/New_York")

    # Fixed reference instant: 2026-09-10 10:00 UTC is 06:00 EDT; the next
    # 08:00 America/New_York occurrence is 12:00 UTC (EDT == UTC-4 in September).
    reference = datetime(2026, 9, 10, 10, 0, 0, tzinfo=UTC)
    next_fire = trigger.get_next_fire_time(None, reference)
    assert next_fire is not None
    assert next_fire.astimezone(UTC) == datetime(2026, 9, 10, 12, 0, 0, tzinfo=UTC)


def test_existing_job_not_readded_on_restart(jobstore_url):
    cfg = make_cfg(run_at="08:00", timezone="UTC")

    # Build #1: starts, persists the job, stops.
    engine = create_engine(jobstore_url)
    s1 = build_scheduler(cfg, engine=engine, scheduler_factory=BackgroundScheduler)
    s1.start()
    s1.shutdown()

    store = probe_store(jobstore_url)
    job = store.lookup_job(JOB_ID)
    assert job is not None, "first build must persist the daily job"
    assert job.next_run_time is not None
    store.shutdown()

    # Simulate downtime: force the persisted next_run_time into the past.
    past = datetime.now(UTC) - timedelta(days=2)
    store = probe_store(jobstore_url)
    job = store.lookup_job(JOB_ID)
    job._modify(next_run_time=past)
    store.update_job(job)
    store.shutdown()

    # Build #2 over the same persisted store: must NOT rewrite next_run_time.
    engine2 = create_engine(jobstore_url)
    build_scheduler(cfg, engine=engine2, scheduler_factory=BackgroundScheduler)

    store = probe_store(jobstore_url)
    job2 = store.lookup_job(JOB_ID)
    assert job2 is not None, "job must survive the second build"
    assert job2.next_run_time == past, (
        "second build rewrote the persisted next_run_time -- catch-up is now "
        "silently impossible (03-RESEARCH.md Pattern 3)"
    )
    store.shutdown()


def test_catchup_fires_once_after_multi_day_gap(jobstore_url, monkeypatch):
    import huntloop.scheduler.jobs as jobs_module

    cfg = make_cfg(run_at="08:00", timezone="UTC")
    fires: list[datetime] = []

    def _record(*args, **kwargs):
        fires.append(datetime.now(UTC))

    monkeypatch.setattr(jobs_module, "execute_scheduled_run", _record)
    monkeypatch.setattr(jobs_module, "load_config", lambda: cfg)
    monkeypatch.setattr(jobs_module, "get_engine", lambda: None)

    # Build #1 persists the job normally.
    engine = create_engine(jobstore_url)
    s1 = build_scheduler(cfg, engine=engine, scheduler_factory=BackgroundScheduler)
    s1.start()
    s1.shutdown()

    # Three days of downtime: the persisted next_run_time is now far overdue.
    overdue = datetime.now(UTC) - timedelta(days=3)
    store = probe_store(jobstore_url)
    job = store.lookup_job(JOB_ID)
    assert job is not None
    job._modify(next_run_time=overdue)
    store.update_job(job)
    store.shutdown()

    # Build #2 over the same store and start it: exactly ONE catch-up fire.
    engine2 = create_engine(jobstore_url)
    s2 = build_scheduler(cfg, engine=engine2, scheduler_factory=BackgroundScheduler)
    s2.start()
    try:
        deadline = datetime.now(UTC) + timedelta(seconds=5)
        while not fires and datetime.now(UTC) < deadline:
            time.sleep(0.05)
        assert fires, "no catch-up fired within 5s of restart over a 3-day-old next_run_time"
        time.sleep(0.5)  # grace window to catch an erroneous second fire
        assert len(fires) == 1, f"expected exactly one catch-up fire, saw {len(fires)}"
    finally:
        s2.shutdown()

    # After the catch-up, next_run_time must have advanced to the normal next
    # occurrence (i.e. into the future), not stayed stuck in the past.
    store = probe_store(jobstore_url)
    advanced = store.lookup_job(JOB_ID).next_run_time
    store.shutdown()
    assert advanced is not None
    assert advanced > datetime.now(UTC)


def test_run_at_change_reschedules_persisted_job(jobstore_url):
    cfg = make_cfg(run_at="08:00", timezone="UTC")
    engine = create_engine(jobstore_url)
    s1 = build_scheduler(cfg, engine=engine, scheduler_factory=BackgroundScheduler)
    s1.start()
    s1.shutdown()

    cfg2 = make_cfg(run_at="09:30", timezone="UTC")
    engine2 = create_engine(jobstore_url)
    s2 = build_scheduler(cfg2, engine=engine2, scheduler_factory=BackgroundScheduler)
    s2.start()
    s2.shutdown()

    store = probe_store(jobstore_url)
    job = store.lookup_job(JOB_ID)
    assert job is not None
    assert str(job.trigger) == "cron[hour='9', minute='30']", (
        "changing HUNTLOOP_RUN_AT between restarts must take effect"
    )
    assert job.next_run_time is not None
    store.shutdown()


def test_timezone_change_reschedules_persisted_job(jobstore_url):
    cfg = make_cfg(run_at="08:00", timezone="UTC")
    engine = create_engine(jobstore_url)
    s1 = build_scheduler(cfg, engine=engine, scheduler_factory=BackgroundScheduler)
    s1.start()
    s1.shutdown()

    cfg2 = make_cfg(run_at="08:00", timezone="Asia/Tokyo")
    engine2 = create_engine(jobstore_url)
    s2 = build_scheduler(cfg2, engine=engine2, scheduler_factory=BackgroundScheduler)
    s2.start()
    s2.shutdown()

    store = probe_store(jobstore_url)
    job = store.lookup_job(JOB_ID)
    assert job is not None
    assert job.trigger.timezone == ZoneInfo("Asia/Tokyo"), (
        "changing only HUNTLOOP_TIMEZONE between restarts must take effect"
    )
    store.shutdown()


def test_scheduler_module_has_no_playwright_import():
    """OPS-02: run in a real subprocess so pytest's own imports cannot lie."""
    code = (
        "import sys;"
        "import huntloop.scheduler.build, huntloop.scheduler.jobs;"
        "bad=[m for m in sys.modules if m.split('.')[0]=='playwright'];"
        "print(bad);"
        "sys.exit(1 if bad else 0)"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stdout + proc.stderr


def test_page_fetcher_still_headless():
    """OPS-02 regression guard: Phase 2's fetcher must stay headless."""
    page_src = Path("src/huntloop/discovery/fetch/page.py").read_text()
    assert "headless=True" in page_src
