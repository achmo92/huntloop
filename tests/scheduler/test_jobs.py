"""RUN-03 / RUN-05 / RUN-08 scheduled-job tests. Owned by plans 03-03 and 03-04.

Owning test names (03-VALIDATION.md): test_overlap_skip_records_dedicated_run_row,
test_scheduled_run_populates_stage_counts, test_run_status_capped_with_reason
(the last is authored by 03-04).
"""
import inspect
from dataclasses import replace
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

from huntloop.config import load_config
from huntloop.db.base import make_session_factory
from huntloop.db.models import Run, RunStatus, RunTrigger
from huntloop.db.repository import RunRepository
from huntloop.scheduler.jobs import (
    classify_trigger,
    execute_scheduled_run,
    run_scheduled_discovery,
)


def make_cfg(**overrides):
    return replace(load_config(), **overrides)


def test_scheduled_job_entrypoint_takes_no_arguments():
    """APScheduler persists a textual func ref and cannot pass a sessionmaker."""
    assert list(inspect.signature(run_scheduled_discovery).parameters) == []


def test_classify_trigger_on_time_is_scheduled():
    cfg = make_cfg(run_at="08:00", timezone="UTC")

    # Exactly on the occurrence and 5 minutes late both count as on-time.
    assert classify_trigger(cfg, now=datetime(2026, 9, 10, 8, 0, 0, tzinfo=UTC)) == (
        RunTrigger.SCHEDULED
    )
    assert classify_trigger(cfg, now=datetime(2026, 9, 10, 8, 5, 0, tzinfo=UTC)) == (
        RunTrigger.SCHEDULED
    )


def test_classify_trigger_after_gap_is_catch_up():
    cfg = make_cfg(run_at="08:00", timezone="UTC")

    # 11 minutes after the occurrence: past the 10-minute skew window.
    assert classify_trigger(cfg, now=datetime(2026, 9, 10, 8, 11, 0, tzinfo=UTC)) == (
        RunTrigger.CATCH_UP
    )
    # Multi-day downtime, restarted BEFORE today's fire: the most recent
    # occurrence is yesterday's, so the fire is a catch-up even though the gap
    # since the *missed* fire (three days ago) is longer still.
    assert classify_trigger(cfg, now=datetime(2026, 9, 13, 7, 30, 0, tzinfo=UTC)) == (
        RunTrigger.CATCH_UP
    )


def test_overlap_skip_records_dedicated_run_row(main_engine, monkeypatch):
    """RUN-03: a fire landing on a RUNNING run must skip AND record the skip."""
    import huntloop.graph.build as graph_build

    factory = make_session_factory(main_engine)

    def _must_not_run(*args, **kwargs):
        raise AssertionError("run_discovery must not be called")

    monkeypatch.setattr(graph_build, "run_discovery", _must_not_run)

    # Seed an in-flight manual run (the likelier overlap: a hand-run `huntloop run`).
    session = factory()
    try:
        seeded = RunRepository(session).start(RunTrigger.MANUAL)
        session.commit()
        running_id = seeded.id
    finally:
        session.close()

    cfg = make_cfg(run_at="08:00", timezone="UTC")
    execute_scheduled_run(factory, cfg, now=datetime(2026, 9, 10, 8, 5, 0, tzinfo=UTC))

    session = factory()
    try:
        runs = session.execute(select(Run)).scalars().all()
        assert len(runs) == 2, "the seeded RUNNING row plus exactly one new row"
        skipped = next(r for r in runs if r.id != running_id)
        assert skipped.status == RunStatus.SKIPPED
        assert skipped.trigger == RunTrigger.SCHEDULED
        assert skipped.finished_at is not None
        assert skipped.companies_checked == 0
        assert "still in progress" in (skipped.error_summary or "")
    finally:
        session.close()


def test_scheduled_run_populates_stage_counts(main_engine, monkeypatch):
    """RUN-05: every per-stage counter must be individually readable off the Run row."""
    factory = make_session_factory(main_engine)

    def _fake_run_discovery(*, sessionmaker, trigger, **kwargs):
        from types import SimpleNamespace

        session = sessionmaker()
        try:
            repo = RunRepository(session)
            run = repo.start(trigger)
            repo.finish(
                run.id,
                status=RunStatus.SUCCESS,
                companies_checked=2,
                listings_fetched=9,
                after_dedup=8,
                after_deterministic=5,
                after_triage=3,
                scored=3,
                new_jobs_written=3,
                tokens_in=1200,
                tokens_out=400,
                cost_usd=0.01,
            )
            session.commit()
            return SimpleNamespace(
                run_id=str(run.id),
                status="success",
                new_jobs_written=3,
                cost_usd=0.01,
            )
        finally:
            session.close()

    monkeypatch.setattr("huntloop.graph.build.run_discovery", _fake_run_discovery)

    cfg = make_cfg(run_at="08:00", timezone="UTC")
    execute_scheduled_run(factory, cfg, now=datetime(2026, 9, 10, 8, 5, 0, tzinfo=UTC))

    session = factory()
    try:
        run = session.execute(select(Run)).scalars().one()
        assert run.status == RunStatus.SUCCESS
        assert run.trigger == RunTrigger.SCHEDULED
        assert run.companies_checked == 2
        assert run.listings_fetched == 9
        assert run.after_dedup == 8
        assert run.after_deterministic == 5
        assert run.after_triage == 3
        assert run.scored == 3
        assert run.new_jobs_written == 3
        assert run.tokens_in == 1200
        assert run.tokens_out == 400
        assert float(run.cost_usd) == 0.01
    finally:
        session.close()


def test_run_discovery_exception_is_swallowed(main_engine, monkeypatch):
    """One bad day must not kill the daemon -- tomorrow's fire still happens."""
    factory = make_session_factory(main_engine)

    def _boom(*args, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr("huntloop.graph.build.run_discovery", _boom)

    cfg = make_cfg(run_at="08:00", timezone="UTC")
    # Must not raise.
    execute_scheduled_run(factory, cfg, now=datetime(2026, 9, 10, 8, 5, 0, tzinfo=UTC))
