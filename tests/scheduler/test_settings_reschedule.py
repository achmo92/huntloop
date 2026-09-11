"""UI-04: settings take effect on runs and in the live scheduler.

Two halves:
1. Run paths resolve config through the overlay — a spend cap or per-stage model
   set in the UI applies to the very next run with no restart.
2. The scheduler follows a schedule change live — a run_at/timezone Setting moves
   the next fire time without restarting the daemon.
"""

from __future__ import annotations

import json
import threading
import time
import uuid
from collections import namedtuple
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import MagicMock
from zoneinfo import ZoneInfo

import pytest
from apscheduler.schedulers.background import BackgroundScheduler
from sqlalchemy import create_engine

import huntloop.graph.build as build_mod
import huntloop.graph.nodes as nodes_mod
from huntloop.config import load_config
from huntloop.criteria.loader import save_new_criteria_version
from huntloop.criteria.schema import (
    CompensationFloor,
    CriteriaPayload,
    DimensionWeights,
    LocationCriteria,
)
from huntloop.db.base import make_session_factory
from huntloop.db.models import Company
from huntloop.db.repository import SettingsRepository
from huntloop.discovery.ats.base import FetchResult, FetchStatus, RawListing
from huntloop.graph.build import run_discovery
from huntloop.scheduler.build import JOB_ID, build_scheduler, build_trigger
from huntloop.scoring.spend_cap import SpendTracker

NOW = datetime.now(UTC)

_DIMENSIONS = {
    "role_fit": {"score": 4, "reason": "matches profile"},
    "seniority_fit": {"score": 4, "reason": "level matches"},
    "employer_fit": {"score": 4, "reason": "stable company"},
    "trajectory": {"score": 4, "reason": "clear growth"},
    "summary": "solid match",
}


@pytest.fixture(autouse=True)
def _clean_settings_env(monkeypatch):
    for name in (
        "HUNTLOOP_OPENAI_BASE_URL",
        "HUNTLOOP_TRIAGE_MODEL",
        "HUNTLOOP_SCORING_MODEL",
        "HUNTLOOP_EXTRACTION_MODEL",
        "HUNTLOOP_RUN_AT",
        "HUNTLOOP_TIMEZONE",
        "HUNTLOOP_RUN_SPEND_CAP_USD",
    ):
        monkeypatch.delenv(name, raising=False)


class _Completions:
    def __init__(self, calls: list) -> None:
        self._calls = calls

    def create(self, **kwargs):
        self._calls.append(kwargs)
        system = kwargs["messages"][0]["content"]
        if "triaging job listings" in system:
            payload = {"keep": True, "reason": "relevant"}
        elif "scoring job listings" in system:
            payload = _DIMENSIONS
        else:
            raise AssertionError(f"unknown system prompt: {system[:80]!r}")

        Choice = namedtuple("Choice", ["message"])
        Message = namedtuple("Message", ["content"])
        Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])

        class FakeCompletion:
            choices = [Choice(message=Message(content=json.dumps(payload)))]
            usage = Usage(prompt_tokens=10, completion_tokens=20)

        return FakeCompletion()


class _Chat:
    def __init__(self, completions) -> None:
        self.completions = completions


class FakeClient:
    def __init__(self) -> None:
        self.calls: list = []
        self.chat = _Chat(_Completions(self.calls))


class FakeAdapter:
    platform = "greenhouse"

    def fetch(self, slug: str, *, client=None) -> FetchResult:
        listing = RawListing(
            external_id="s-1",
            url="https://example.com/jobs/s-1",
            title="Senior Software Engineer",
            description_plain="Python distributed systems role.",
            description_html=None,
            location_raw="New York, NY",
            posted_at=NOW,
            comp_raw="$130k - $150k",
            comp_min=None,
            comp_max=None,
            raw={},
        )
        return FetchResult(status=FetchStatus.OK, listings=[listing])


def _criteria() -> CriteriaPayload:
    return CriteriaPayload(
        profile_summary="Senior Python backend developer",
        seniority_min="senior",
        seniority_max="principal",
        dimension_weights=DimensionWeights(
            role_fit=0.4, seniority_fit=0.2, employer_fit=0.2, trajectory=0.2
        ),
        locations=LocationCriteria(eligible_countries=["US"]),
        compensation_floor=CompensationFloor(amount="120000", currency="USD", period="annual"),
    )


def _seed_criteria(factory) -> None:
    session = factory()
    try:
        save_new_criteria_version(session, _criteria())
        session.commit()
    finally:
        session.close()


def _seed_company(factory, name: str = "Co") -> None:
    session = factory()
    try:
        session.add(
            Company(
                id=uuid.uuid4(),
                name=name,
                enabled=True,
                ats="greenhouse",
                ats_identifier=name.lower(),
                resolved_at=NOW,
            )
        )
        session.commit()
    finally:
        session.close()


def _seed_setting(factory, **values) -> None:
    session = factory()
    try:
        repo = SettingsRepository(session)
        for key, value in values.items():
            repo.set_value(key, value)
        session.commit()
    finally:
        session.close()


def _run_and_capture_triage_models(main_engine, monkeypatch) -> tuple[list[str], int]:
    factory = make_session_factory(main_engine)
    _seed_criteria(factory)
    _seed_company(factory)
    client = FakeClient()
    monkeypatch.setattr("huntloop.llm.client.get_llm_client", lambda session: client)
    monkeypatch.setattr(nodes_mod, "get_adapter", lambda platform: FakeAdapter())
    summary = run_discovery(sessionmaker=factory, http_client=MagicMock())
    models = [
        call["model"]
        for call in client.calls
        if "triaging job listings" in call["messages"][0]["content"]
    ]
    return models, summary.scored


# ---------------------------------------------------------------------------
# Run paths resolve through the overlay
# ---------------------------------------------------------------------------


def test_run_discovery_uses_overlay_spend_cap(main_engine, monkeypatch):
    factory = make_session_factory(main_engine)
    _seed_criteria(factory)
    _seed_setting(factory, run_spend_cap_usd="3.00")

    captured: dict = {}
    RealTracker = SpendTracker

    class SpyTracker(RealTracker):
        def __init__(self, cap_usd=None):
            captured["cap_usd"] = cap_usd
            super().__init__(cap_usd=cap_usd)

    monkeypatch.setattr(build_mod, "SpendTracker", SpyTracker)

    run_discovery(sessionmaker=factory, llm_client=object(), http_client=MagicMock())

    assert captured["cap_usd"] == Decimal("3.00")


def test_run_discovery_uses_overlay_triage_model(main_engine, monkeypatch):
    _seed_setting(make_session_factory(main_engine), triage_model="test-model")
    models, scored = _run_and_capture_triage_models(main_engine, monkeypatch)
    assert models == ["test-model"]
    assert scored == 1


def test_run_discovery_without_rows_uses_env_model(main_engine, monkeypatch):
    """Regression net: no Setting rows -> the env path, exactly as before."""
    monkeypatch.setenv("HUNTLOOP_TRIAGE_MODEL", "env-model")
    models, scored = _run_and_capture_triage_models(main_engine, monkeypatch)
    assert models == ["env-model"]
    assert scored == 1


# ---------------------------------------------------------------------------
# Live scheduler reschedule
# ---------------------------------------------------------------------------


def _watcher():
    import huntloop.scheduler.build as build

    assert hasattr(build, "watch_settings_and_reschedule"), (
        "huntloop.scheduler.build.watch_settings_and_reschedule must exist (UI-04)"
    )
    return build.watch_settings_and_reschedule


class FakeScheduler:
    """Minimal scheduler surface: get_job/reschedule_job (the watcher contract)."""

    def __init__(self, job) -> None:
        self._job = job
        self.reschedules: list = []

    def get_job(self, job_id):
        return self._job

    def reschedule_job(self, job_id, trigger):
        self.reschedules.append(trigger)
        self._job = SimpleNamespace(trigger=trigger)


def test_schedule_change_reschedules_live(main_engine, jobstore_url):
    watch = _watcher()
    factory = make_session_factory(main_engine)
    base_cfg = replace(load_config(), run_at="08:00", timezone="UTC")

    engine = create_engine(jobstore_url)
    scheduler = build_scheduler(base_cfg, engine=engine, scheduler_factory=BackgroundScheduler)
    scheduler.start()
    stop = threading.Event()
    watcher = threading.Thread(
        target=watch,
        args=(scheduler, factory),
        kwargs={"poll_seconds": 0.05, "stop": stop},
        daemon=True,
    )
    try:
        watcher.start()
        _seed_setting(factory, run_at="06:30", timezone="UTC")

        deadline = time.time() + 5
        job = scheduler.get_job(JOB_ID)
        while time.time() < deadline:
            job = scheduler.get_job(JOB_ID)
            if str(job.trigger) == "cron[hour='6', minute='30']":
                break
            time.sleep(0.02)
        assert str(job.trigger) == "cron[hour='6', minute='30']"
        assert job.next_run_time is not None
    finally:
        stop.set()
        scheduler.shutdown(wait=False)


def test_timezone_change_reschedules_live(main_engine, jobstore_url):
    watch = _watcher()
    factory = make_session_factory(main_engine)
    base_cfg = replace(load_config(), run_at="08:00", timezone="UTC")

    engine = create_engine(jobstore_url)
    scheduler = build_scheduler(base_cfg, engine=engine, scheduler_factory=BackgroundScheduler)
    scheduler.start()
    stop = threading.Event()
    watcher = threading.Thread(
        target=watch,
        args=(scheduler, factory),
        kwargs={"poll_seconds": 0.05, "stop": stop},
        daemon=True,
    )
    try:
        watcher.start()
        _seed_setting(factory, run_at="08:00", timezone="Europe/Berlin")

        deadline = time.time() + 5
        job = scheduler.get_job(JOB_ID)
        while time.time() < deadline:
            job = scheduler.get_job(JOB_ID)
            if job.trigger.timezone == ZoneInfo("Europe/Berlin"):
                break
            time.sleep(0.02)
        assert job.trigger.timezone == ZoneInfo("Europe/Berlin")
    finally:
        stop.set()
        scheduler.shutdown(wait=False)


def test_no_settings_change_does_not_reschedule(main_engine):
    watch = _watcher()
    factory = make_session_factory(main_engine)
    cfg = load_config()
    fake = FakeScheduler(SimpleNamespace(trigger=build_trigger(cfg)))

    stop = threading.Event()
    watcher = threading.Thread(
        target=watch,
        args=(fake, factory),
        kwargs={"poll_seconds": 0.02, "stop": stop},
        daemon=True,
    )
    watcher.start()
    try:
        time.sleep(0.15)
    finally:
        stop.set()
        watcher.join(timeout=2)

    assert fake.reschedules == []
