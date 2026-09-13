"""GAP-4: manual stop of runs — cooperative cancellation contracts.

The stop is DB-level (a ``Setting`` row naming the run id) because the API
daemon thread and the scheduler run in different processes; the run path checks
the marker between employers and between stages and finishes the Run as the
first-class terminal ``STOPPED`` status, carrying the real spend ledger and
clearing the in-progress marker so the next scheduled run is not blocked.

This file owns all six behavior contracts from 04-14-PLAN.md Task 1.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

import pytest
import sqlalchemy
from sqlalchemy import select

from huntloop.criteria.loader import save_new_criteria_version
from huntloop.criteria.schema import (
    CompensationFloor,
    CriteriaPayload,
    DimensionWeights,
    LocationCriteria,
)
from huntloop.db.base import make_session_factory
from huntloop.db.models import Company, Run, RunStatus
from huntloop.discovery.ats.base import FetchResult, FetchStatus, RawListing
from huntloop.graph.build import run_discovery
from huntloop.scheduler.jobs import find_run_in_progress

NOW = datetime.now(UTC)


# ---------------------------------------------------------------------------
# Fixtures / helpers
# ---------------------------------------------------------------------------


@pytest.fixture
def sessionmaker(main_engine):
    return make_session_factory(main_engine)


def seed_criteria(sessionmaker) -> int:
    session = sessionmaker()
    try:
        version = save_new_criteria_version(
            session,
            CriteriaPayload(
                profile_summary="Senior Python backend developer",
                seniority_min="senior",
                seniority_max="principal",
                dimension_weights=DimensionWeights(
                    role_fit=0.4,
                    seniority_fit=0.2,
                    employer_fit=0.2,
                    trajectory=0.2,
                ),
                locations=LocationCriteria(eligible_countries=["US"]),
                compensation_floor=CompensationFloor(
                    amount="120000", currency="USD", period="annual"
                ),
            ),
        )
        session.commit()
        return version
    finally:
        session.close()


def make_company(sessionmaker, name: str, slug: str) -> uuid.UUID:
    session = sessionmaker()
    try:
        company = Company(
            id=uuid.uuid4(),
            name=name,
            enabled=True,
            ats="greenhouse",
            ats_identifier=slug,
            resolved_at=NOW,
        )
        session.add(company)
        session.commit()
        return company.id
    finally:
        session.close()


def make_listing(external_id: str) -> RawListing:
    return RawListing(
        external_id=external_id,
        url=f"https://example.com/jobs/{external_id}",
        title="Senior Software Engineer",
        description_plain="We are looking for a Python engineer with distributed systems experience.",
        description_html="<p>We are looking for a Python engineer.</p>",
        location_raw="New York, NY",
        posted_at=NOW,
        comp_raw="$130k - $150k",
    )


TRIAGE_KEEP = {"keep": True, "reason": "relevant"}


def dims_response(score: int) -> dict:
    return {
        "role_fit": {"score": score, "reason": "matches profile"},
        "seniority_fit": {"score": score, "reason": "level matches"},
        "employer_fit": {"score": score, "reason": "stable company"},
        "trajectory": {"score": score, "reason": "clear growth"},
        "summary": "solid match",
    }


class _RecordingClient:
    """Minimal OpenAI-shaped fake: queues responses by system prompt."""

    def __init__(self, *, triage: list | None = None, scoring: list | None = None) -> None:
        self._queues = {
            "triaging job listings": list(triage or []),
            "scoring job listings": list(scoring or []),
        }
        self.calls: list[dict] = []

    class _Completions:
        def __init__(self, parent) -> None:
            self.parent = parent

        def create(self, **kwargs):
            import json
            from collections import namedtuple

            parent = self.parent
            system = kwargs["messages"][0]["content"]
            parent.calls.append(kwargs)
            queue = next(
                (q for marker, q in parent._queues.items() if marker in system), None
            )
            assert queue is not None, f"unknown system prompt: {system[:80]!r}"
            assert queue, f"no queued response for prompt: {system[:80]!r}"
            resp = queue.pop(0)

            Choice = namedtuple("Choice", ["message"])
            Message = namedtuple("Message", ["content"])
            Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])
            choice = Choice(message=Message(content=json.dumps(resp)))

            class FakeCompletion:
                def __init__(self) -> None:
                    self.choices = [choice]
                    self.usage = Usage(prompt_tokens=10, completion_tokens=20)
                    self.model = kwargs.get("model", "fake-model")

            return FakeCompletion()

    @property
    def chat(self):
        class Chat:
            completions = self._Completions(self)

        return Chat()


class _StopSeedingAdapter:
    """Adapter that seeds the DB stop marker when the SECOND employer is fetched.

    Deterministic with ``concurrency=1``: the first employer completes fully
    (recording real spend), the second observes the marker at its next boundary
    and aborts the graph.
    """

    platform = "greenhouse"

    def __init__(self, sessionmaker, listings: list[RawListing]) -> None:
        self.sessionmaker = sessionmaker
        self.listings = listings
        self.fetch_calls = 0

    def fetch(self, slug: str, *, client=None) -> FetchResult:
        self.fetch_calls += 1
        if self.fetch_calls == 2:
            from huntloop.graph.cancellation import request_stop

            session = self.sessionmaker()
            try:
                running = find_run_in_progress(session)
                assert running is not None, "no RUNNING run to address the stop to"
                request_stop(session, running.id)
                session.commit()
            finally:
                session.close()
        return FetchResult(status=FetchStatus.OK, listings=list(self.listings))


# ---------------------------------------------------------------------------
# Behavior 1: STOPPED is a first-class status that fits the column
# ---------------------------------------------------------------------------


def test_stopped_status_is_a_member_fitting_the_column():
    assert RunStatus.STOPPED.value == "stopped"
    # "stopped" is 7 chars, like RUNNING/PARTIAL/SKIPPED, so the rendered
    # VARCHAR length is unchanged and no migration is needed (Phase 3 precedent).
    rendered = sqlalchemy.Enum(RunStatus, native_enum=False).length
    assert rendered == 7
    assert len(RunStatus.STOPPED.value) <= rendered


# ---------------------------------------------------------------------------
# Behavior 2: the Setting-backed cancellation marker
# ---------------------------------------------------------------------------


def test_request_stop_marks_only_the_named_run(sessionmaker):
    from huntloop.db.repository import SettingsRepository
    from huntloop.graph.cancellation import (
        STOP_REQUEST_SETTING_KEY,
        clear_stop_request,
        is_stop_requested,
        request_stop,
    )

    run_a = uuid.uuid4()
    run_b = uuid.uuid4()

    session = sessionmaker()
    try:
        request_stop(session, run_a)
        session.commit()
        # The marker is a Setting row naming the run id.
        assert SettingsRepository(session).get_value(STOP_REQUEST_SETTING_KEY) == str(run_a)
    finally:
        session.close()

    assert is_stop_requested(sessionmaker, run_a) is True
    assert is_stop_requested(sessionmaker, run_b) is False

    session = sessionmaker()
    try:
        clear_stop_request(session)
        session.commit()
    finally:
        session.close()

    assert is_stop_requested(sessionmaker, run_a) is False


# ---------------------------------------------------------------------------
# Behavior 3: the node-level check helper
# ---------------------------------------------------------------------------


def test_check_helper_raises_only_when_marker_seeded(sessionmaker):
    from huntloop.graph.cancellation import RunStoppedByUser, request_stop
    from huntloop.graph.nodes import _raise_if_stop_requested

    run_id = uuid.uuid4()

    # No marker: the check returns cleanly.
    assert _raise_if_stop_requested(sessionmaker, str(run_id)) is None

    session = sessionmaker()
    try:
        request_stop(session, run_id)
        session.commit()
    finally:
        session.close()

    with pytest.raises(RunStoppedByUser):
        _raise_if_stop_requested(sessionmaker, str(run_id))


# ---------------------------------------------------------------------------
# Behaviors 4-6: the end-to-end cooperative stop
# ---------------------------------------------------------------------------


def test_run_discovery_stops_cooperatively_and_clears_marker(sessionmaker, monkeypatch):
    import huntloop.graph.nodes as nodes_mod
    from huntloop.graph.cancellation import is_stop_requested

    seed_criteria(sessionmaker)
    make_company(sessionmaker, "StopOne", "stopone")
    make_company(sessionmaker, "StopTwo", "stoptwo")

    adapter = _StopSeedingAdapter(sessionmaker, [make_listing("stop-1")])
    monkeypatch.setattr(nodes_mod, "get_adapter", lambda platform: adapter)

    client = _RecordingClient(triage=[TRIAGE_KEEP], scoring=[dims_response(4)])

    summary = run_discovery(
        sessionmaker=sessionmaker,
        llm_client=client,
        http_client=object(),
        concurrency=1,
    )

    # The stop is honest: STOPPED, not FAILED, with the exact human reason.
    assert summary.status == RunStatus.STOPPED.value

    session = sessionmaker()
    try:
        runs = list(session.execute(select(Run)).scalars().all())
        assert len(runs) == 1, "exactly one Run row — never a stuck RUNNING row"
        run = runs[0]
        assert run.status is RunStatus.STOPPED
        assert run.status is not RunStatus.FAILED
        assert run.finished_at is not None
        assert run.error_summary == "stopped by user request"

        # The real SpendTracker ledger is recorded (the first employer scored
        # before the second observed the stop): a stopped run still spent money.
        assert run.tokens_in > 0
        assert run.tokens_out > 0
        assert run.cost_usd is not None

        # Behavior 5 / GAP-4 overlap guard: the stop released the only RUNNING
        # row, so the next scheduled fire is not blocked.
        assert find_run_in_progress(session) is None
        run_id = run.id
    finally:
        session.close()

    # The marker is cleared once honored — a later run cannot inherit this stop.
    assert is_stop_requested(sessionmaker, run_id) is False
