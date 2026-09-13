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
from huntloop.db.run_liveness import INTERRUPTED_RUN_REASON_PREFIX
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


def test_run_status_capped_with_reason(main_engine, monkeypatch):
    """RUN-08: a scheduled run whose cap trips mid-batch is recorded as
    RunStatus.CAPPED with a human-readable 'spend cap' reason naming the cap
    amount, a real positive cost, and an undisturbed trigger classification."""
    import uuid as uuid_mod
    from decimal import Decimal
    from unittest.mock import MagicMock

    import huntloop.graph.nodes as nodes_mod
    import huntloop.llm.client as llm_client_mod
    from huntloop.criteria.loader import save_new_criteria_version
    from huntloop.criteria.schema import (
        CompensationFloor,
        CriteriaPayload,
        DimensionWeights,
        LocationCriteria,
    )
    from huntloop.db.models import Company
    from huntloop.discovery.ats.base import FetchResult, FetchStatus, RawListing
    from huntloop.pricing.table import cost_for_usage

    factory = make_session_factory(main_engine)
    now = datetime(2026, 9, 10, 8, 5, 0, tzinfo=UTC)

    session = factory()
    try:
        save_new_criteria_version(session, CriteriaPayload(
            profile_summary="Senior Python backend developer",
            seniority_min="senior",
            seniority_max="principal",
            dimension_weights=DimensionWeights(
                role_fit=0.4, seniority_fit=0.2, employer_fit=0.2, trajectory=0.2,
            ),
            locations=LocationCriteria(eligible_countries=["US"]),
            compensation_floor=CompensationFloor(amount="120000", currency="USD", period="annual"),
        ))
        session.add(Company(
            id=uuid_mod.uuid4(), name="CapSchedCo", enabled=True,
            ats="greenhouse", ats_identifier="capsched", resolved_at=now,
        ))
        session.commit()
    finally:
        session.close()

    listings = [
        RawListing(
            external_id=f"cs-{i}", url=f"https://example.com/jobs/cs-{i}",
            title=f"cs-{i}", location_raw="New York, NY",
            description_plain="Python distributed systems role.",
            description_html=None, posted_at=now, comp_raw="$130k - $150k",
            comp_min=None, comp_max=None, raw={},
        )
        for i in range(4)
    ]

    class FakeAdapter:
        platform = "greenhouse"

        def fetch(self, slug, *, client=None):
            return FetchResult(status=FetchStatus.OK, listings=listings)

    monkeypatch.setattr(nodes_mod, "get_adapter", lambda platform: FakeAdapter())

    # Same cap shape as test_cap_blocked_listing_not_written: both calls on
    # gpt-4o-mini at 1M/1M tokens ($0.75/call, $1.50/listing); the cap leaves
    # room for exactly two listings plus a hair.
    per_call = cost_for_usage("gpt-4o-mini", 1_000_000, 1_000_000)
    per_listing = per_call * 2
    cap = per_call * 4 + Decimal("0.000001")
    monkeypatch.setenv("HUNTLOOP_SCORING_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("HUNTLOOP_RUN_SPEND_CAP_USD", str(cap))

    # execute_scheduled_run goes through the real run_discovery; the LLM
    # client is the only thing injected (it builds its own otherwise).
    class _FakeCompletions:
        def create(self, **kwargs):
            import json
            from collections import namedtuple

            system = kwargs["messages"][0]["content"]
            if "triaging job listings" in system:
                payload = {"keep": True, "reason": "relevant"}
            elif "scoring job listings" in system:
                payload = {
                    "role_fit": {"score": 4, "reason": "matches"},
                    "seniority_fit": {"score": 4, "reason": "matches"},
                    "employer_fit": {"score": 4, "reason": "matches"},
                    "trajectory": {"score": 4, "reason": "matches"},
                    "summary": "solid match",
                }
            else:
                raise AssertionError(f"unknown system prompt: {system[:80]!r}")

            Choice = namedtuple("Choice", ["message"])
            Message = namedtuple("Message", ["content"])
            Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])

            class FakeCompletion:
                choices = [Choice(message=Message(content=json.dumps(payload)))]
                usage = Usage(prompt_tokens=1_000_000, completion_tokens=1_000_000)

            return FakeCompletion()

    class _FakeChat:
        completions = _FakeCompletions()

    class _FakeOpenAI:
        chat = _FakeChat()

    monkeypatch.setattr(llm_client_mod, "get_llm_client", lambda session: _FakeOpenAI())

    cfg = make_cfg(run_at="08:00", timezone="UTC")
    execute_scheduled_run(factory, cfg, now=now)

    session = factory()
    try:
        run = session.execute(select(Run)).scalars().one()
        assert run.status is RunStatus.CAPPED
        assert "spend cap" in run.error_summary
        # The reason names the configured cap amount.
        assert f"${cap:.2f}" in run.error_summary
        # Real money was spent, and at most one listing's calls of overshoot
        # past the cap (the cap is checked before each call).
        assert run.cost_usd > 0
        assert run.cost_usd <= cap + per_listing
        # The cap must not disturb trigger classification.
        assert run.trigger is RunTrigger.SCHEDULED
    finally:
        session.close()


def test_scheduler_reconciles_stale_run_and_proceeds(main_engine, monkeypatch):
    """GAP-15: a dead RUNNING row is finalized so the fire is not skipped."""
    from types import SimpleNamespace

    import huntloop.graph.build as graph_build

    factory = make_session_factory(main_engine)

    session = factory()
    try:
        stale = RunRepository(session).start(RunTrigger.MANUAL)
        stale.started_at = datetime(2026, 1, 1, tzinfo=UTC)
        session.commit()
        stale_id = stale.id
    finally:
        session.close()

    calls = {"ran": False}

    def _recording_run_discovery(*, sessionmaker, trigger, **kwargs):
        calls["ran"] = True
        return SimpleNamespace(
            run_id="fake", status="success", new_jobs_written=0, cost_usd=0
        )

    monkeypatch.setattr(graph_build, "run_discovery", _recording_run_discovery)

    cfg = make_cfg(run_at="08:00", timezone="UTC")
    execute_scheduled_run(factory, cfg, now=datetime(2026, 9, 10, 8, 5, 0, tzinfo=UTC))

    # The dead run did not block the fire: discovery actually ran.
    assert calls["ran"] is True

    session = factory()
    try:
        runs = session.execute(select(Run)).scalars().all()
        assert all(r.status is not RunStatus.SKIPPED for r in runs)
        reconciled = session.get(Run, stale_id)
        assert reconciled.status is RunStatus.FAILED
        assert (reconciled.error_summary or "").startswith(
            INTERRUPTED_RUN_REASON_PREFIX
        )
    finally:
        session.close()
