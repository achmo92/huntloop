"""GAP-15: schema-neutral run liveness + stale-run reconciliation contracts.

A RUNNING Run row can outlive the process that owned it (crash or container
restart). Liveness is a per-run ``Setting`` lease refreshed by a serial daemon
heartbeat; a RUNNING row whose lease (falling back to ``started_at`` for
pre-deploy rows) is older than a configurable threshold is stale. Reconciliation
finalizes stale rows terminal and clears their lease so a dead run can never
block a trigger or a scheduled fire.

This file owns every Task 1 behavior contract from 04-22-PLAN.md. Modules that
do not exist yet are imported lazily inside each test, so RED fails on a real
assertion/module error rather than a collection crash (the 04-05/04-14
precedent).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from time import monotonic, sleep

import pytest

from huntloop.config import ConfigError, load_config
from huntloop.db.base import make_session_factory
from huntloop.db.models import Run, RunStatus, RunTrigger
from huntloop.db.repository import RunRepository


@pytest.fixture
def sessionmaker(main_engine):
    return make_session_factory(main_engine)


def _seed_run(sessionmaker, *, started_at=None, status: RunStatus = RunStatus.RUNNING):
    """Seed a Run row, optionally backdating ``started_at``. Returns its id."""
    session = sessionmaker()
    try:
        repo = RunRepository(session)
        run = repo.start(RunTrigger.MANUAL)
        if started_at is not None:
            run.started_at = started_at
        if status is not RunStatus.RUNNING:
            repo.finish(
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
            )
        session.commit()
        return run.id
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Config: the stale threshold (D-15c)
# ---------------------------------------------------------------------------


def test_run_stale_after_seconds_defaults_to_120(main_engine):
    assert load_config().run_stale_after_seconds == 120


def test_run_stale_after_seconds_is_env_overridable(main_engine, monkeypatch):
    monkeypatch.setenv("HUNTLOOP_RUN_STALE_AFTER_SECONDS", "5")
    assert load_config().run_stale_after_seconds == 5


def test_run_stale_after_seconds_rejects_non_integer(main_engine, monkeypatch):
    monkeypatch.setenv("HUNTLOOP_RUN_STALE_AFTER_SECONDS", "soon")
    with pytest.raises(ConfigError):
        load_config()


# ---------------------------------------------------------------------------
# Liveness: the no-lease fallback (D-15d)
# ---------------------------------------------------------------------------


def test_fresh_running_run_without_lease_is_live(sessionmaker):
    from huntloop.db.run_liveness import run_is_live

    run_id = _seed_run(sessionmaker)

    session = sessionmaker()
    try:
        run = session.get(Run, run_id)
        assert run_is_live(session, run) is True
    finally:
        session.close()


def test_running_run_with_day_old_started_at_and_no_lease_is_stale(sessionmaker):
    from huntloop.db.run_liveness import run_is_live

    run_id = _seed_run(sessionmaker, started_at=datetime.now(UTC) - timedelta(days=1))

    session = sessionmaker()
    try:
        run = session.get(Run, run_id)
        assert run_is_live(session, run) is False
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Heartbeat: a fresh lease revives a stale-by-started_at row
# ---------------------------------------------------------------------------


def test_heartbeat_makes_an_old_run_live(sessionmaker):
    from huntloop.db.run_liveness import (
        record_run_heartbeat,
        run_is_live,
    )

    run_id = _seed_run(sessionmaker, started_at=datetime.now(UTC) - timedelta(days=1))

    session = sessionmaker()
    try:
        run = session.get(Run, run_id)
        assert run_is_live(session, run) is False

        record_run_heartbeat(session, run_id)
        session.commit()

        session.expire_all()
        run = session.get(Run, run_id)
        assert run_is_live(session, run) is True
    finally:
        session.close()


def test_terminal_run_is_never_live_even_with_a_fresh_lease(sessionmaker):
    from huntloop.db.run_liveness import record_run_heartbeat, run_is_live

    run_id = _seed_run(sessionmaker, status=RunStatus.SUCCESS)

    session = sessionmaker()
    try:
        record_run_heartbeat(session, run_id)
        session.commit()

        run = session.get(Run, run_id)
        assert run.status is RunStatus.SUCCESS
        assert run_is_live(session, run) is False
    finally:
        session.close()


# ---------------------------------------------------------------------------
# Reconciliation: only stale RUNNING rows are finalized (D-15e)
# ---------------------------------------------------------------------------


def test_reconcile_finalizes_only_stale_running_rows(sessionmaker):
    from huntloop.db.models import Setting
    from huntloop.db.repository import SettingsRepository
    from huntloop.db.run_liveness import (
        INTERRUPTED_RUN_REASON_PREFIX,
        lease_key,
        reconcile_stale_runs,
        record_run_heartbeat,
    )

    stale_id = _seed_run(sessionmaker, started_at=datetime.now(UTC) - timedelta(days=1))
    live_id = _seed_run(sessionmaker)
    done_id = _seed_run(sessionmaker, status=RunStatus.SUCCESS)

    session = sessionmaker()
    try:
        # The stale row carries a lease that is itself old — reconcile must clear it.
        record_run_heartbeat(
            session, stale_id, now=datetime.now(UTC) - timedelta(days=1)
        )
        session.commit()

        finalized = reconcile_stale_runs(session)
        assert finalized == [stale_id]

        session.expire_all()
        stale = session.get(Run, stale_id)
        assert stale.status is RunStatus.FAILED
        assert stale.finished_at is not None
        assert stale.error_summary is not None
        assert stale.error_summary.startswith(INTERRUPTED_RUN_REASON_PREFIX)

        # The lease is cleared so it cannot be mistaken for a live row later.
        assert session.get(Setting, lease_key(stale_id)) is None

        # The live RUNNING row and the terminal row are untouched.
        assert session.get(Run, live_id).status is RunStatus.RUNNING
        assert session.get(Run, done_id).status is RunStatus.SUCCESS
        assert (
            SettingsRepository(session).get_value(lease_key(stale_id)) is None
        )
    finally:
        session.close()


def test_reconcile_returns_empty_when_nothing_is_stale(sessionmaker):
    from huntloop.db.run_liveness import reconcile_stale_runs

    _seed_run(sessionmaker)
    _seed_run(sessionmaker, status=RunStatus.SUCCESS)

    session = sessionmaker()
    try:
        assert reconcile_stale_runs(session) == []
    finally:
        session.close()


# ---------------------------------------------------------------------------
# The heartbeat thread
# ---------------------------------------------------------------------------


def test_heartbeat_thread_refreshes_lease_until_stopped(sessionmaker):
    from huntloop.db.repository import SettingsRepository
    from huntloop.db.run_liveness import (
        lease_key,
        start_run_heartbeat,
    )

    run_id = _seed_run(sessionmaker)

    heartbeat = start_run_heartbeat(sessionmaker, run_id, interval=0.05)
    try:
        # Bounded wait for the first beat (well under 5s).
        deadline = monotonic() + 5.0
        lease = None
        while monotonic() < deadline:
            session = sessionmaker()
            try:
                lease = SettingsRepository(session).get_value(lease_key(run_id))
            finally:
                session.close()
            if lease is not None:
                break
            sleep(0.02)
        assert lease is not None, "the heartbeat never wrote a lease"
    finally:
        heartbeat.stop()
        heartbeat.join(timeout=5)

    assert not heartbeat.thread.is_alive(), "the heartbeat thread must stop"

    # The last write is durable after the thread stops.
    session = sessionmaker()
    try:
        assert SettingsRepository(session).get_value(lease_key(run_id)) is not None
    finally:
        session.close()


# ---------------------------------------------------------------------------
# GAP-16: the stop grace window (config, timestamped marker, durable sweep)
# ---------------------------------------------------------------------------


def test_run_stop_grace_seconds_defaults_to_60(main_engine):
    assert load_config().run_stop_grace_seconds == 60


def test_run_stop_grace_seconds_is_env_overridable(main_engine, monkeypatch):
    monkeypatch.setenv("HUNTLOOP_RUN_STOP_GRACE_SECONDS", "5")
    assert load_config().run_stop_grace_seconds == 5


def test_run_stop_grace_seconds_rejects_non_integer(main_engine, monkeypatch):
    monkeypatch.setenv("HUNTLOOP_RUN_STOP_GRACE_SECONDS", "soon")
    with pytest.raises(ConfigError):
        load_config()


def test_request_stop_writes_a_timestamp_and_clear_removes_both(sessionmaker):
    from huntloop.db.models import Setting
    from huntloop.db.run_liveness import stop_requested_at_key
    from huntloop.graph.cancellation import (
        STOP_REQUEST_SETTING_KEY,
        clear_stop_request,
        request_stop,
        stop_requested_at,
    )

    run_id = uuid.uuid4()
    now = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)

    session = sessionmaker()
    try:
        request_stop(session, run_id, now=now)
        session.commit()
    finally:
        session.close()

    session = sessionmaker()
    try:
        assert stop_requested_at(session, run_id) == now
        clear_stop_request(session, run_id)
        session.commit()
        assert session.get(Setting, STOP_REQUEST_SETTING_KEY) is None
        assert session.get(Setting, stop_requested_at_key(run_id)) is None
    finally:
        session.close()

    # The no-arg clear keeps deleting only the marker (GAP-4 unchanged).
    session = sessionmaker()
    try:
        request_stop(session, run_id, now=now)
        session.commit()
        clear_stop_request(session)
        session.commit()
        assert session.get(Setting, STOP_REQUEST_SETTING_KEY) is None
        assert session.get(Setting, stop_requested_at_key(run_id)) is not None
    finally:
        session.close()


def test_finalize_grace_expired_stops_finalizes_expired_stop(sessionmaker):
    from huntloop.db.models import Setting
    from huntloop.db.repository import SettingsRepository
    from huntloop.db.run_liveness import (
        GRACE_EXPIRED_STOP_REASON,
        finalize_grace_expired_stops,
        lease_key,
        record_run_heartbeat,
        stop_requested_at_key,
    )
    from huntloop.graph.cancellation import STOP_REQUEST_SETTING_KEY, request_stop

    run_id = _seed_run(sessionmaker)
    now = datetime.now(UTC)

    session = sessionmaker()
    try:
        record_run_heartbeat(session, run_id)
        request_stop(session, run_id, now=now - timedelta(seconds=120))
        session.commit()
    finally:
        session.close()

    session = sessionmaker()
    try:
        finalized = finalize_grace_expired_stops(session, now=now, grace_seconds=60)
        assert finalized == [run_id]

        session.expire_all()
        run = session.get(Run, run_id)
        assert run.status is RunStatus.STOPPED
        assert run.finished_at is not None
        assert run.error_summary == GRACE_EXPIRED_STOP_REASON
        # Marker + timestamp + lease are all cleared so the overlap guard releases.
        assert session.get(Setting, lease_key(run_id)) is None
        assert session.get(Setting, stop_requested_at_key(run_id)) is None
        assert SettingsRepository(session).get_value(STOP_REQUEST_SETTING_KEY) is None
    finally:
        session.close()


def test_finalize_grace_expired_stops_leaves_fresh_stop_untouched(sessionmaker):
    from huntloop.db.run_liveness import finalize_grace_expired_stops
    from huntloop.graph.cancellation import request_stop

    run_id = _seed_run(sessionmaker)
    now = datetime.now(UTC)

    session = sessionmaker()
    try:
        request_stop(session, run_id, now=now - timedelta(seconds=10))
        session.commit()
    finally:
        session.close()

    session = sessionmaker()
    try:
        assert finalize_grace_expired_stops(session, now=now, grace_seconds=60) == []
        assert session.get(Run, run_id).status is RunStatus.RUNNING
    finally:
        session.close()


def test_finalize_grace_expired_stops_ignores_runs_without_a_stop(sessionmaker):
    from huntloop.db.run_liveness import finalize_grace_expired_stops

    run_id = _seed_run(sessionmaker)

    session = sessionmaker()
    try:
        assert (
            finalize_grace_expired_stops(
                session, now=datetime.now(UTC), grace_seconds=60
            )
            == []
        )
        assert session.get(Run, run_id).status is RunStatus.RUNNING
    finally:
        session.close()


def test_finalize_grace_expired_stops_never_touches_a_terminal_row(sessionmaker):
    from huntloop.db.run_liveness import finalize_grace_expired_stops
    from huntloop.graph.cancellation import request_stop

    run_id = _seed_run(sessionmaker, status=RunStatus.SUCCESS)
    now = datetime.now(UTC)

    session = sessionmaker()
    try:
        request_stop(session, run_id, now=now - timedelta(seconds=120))
        session.commit()
    finally:
        session.close()

    session = sessionmaker()
    try:
        assert finalize_grace_expired_stops(session, now=now, grace_seconds=60) == []
        assert session.get(Run, run_id).status is RunStatus.SUCCESS
    finally:
        session.close()


def test_grace_finalized_run_is_never_overwritten(sessionmaker):
    from huntloop.db.run_liveness import finalize_grace_expired_stops
    from huntloop.graph.cancellation import request_stop

    run_id = _seed_run(sessionmaker)
    now = datetime.now(UTC)

    session = sessionmaker()
    try:
        request_stop(session, run_id, now=now - timedelta(seconds=120))
        session.commit()
    finally:
        session.close()

    session = sessionmaker()
    try:
        assert finalize_grace_expired_stops(session, now=now, grace_seconds=60) == [run_id]
        applied = RunRepository(session).finish_if_running(
            run_id,
            status=RunStatus.SUCCESS,
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
        )
        assert applied is None
        session.expire_all()
        assert session.get(Run, run_id).status is RunStatus.STOPPED
    finally:
        session.close()
