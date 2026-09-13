"""GAP-4: manual stop of runs — cooperative cancellation contracts.

The stop is DB-level (a ``Setting`` row naming the run id) because the API
daemon thread and the scheduler run in different processes; the run path checks
the marker between employers and between stages and finishes the Run as the
first-class terminal ``STOPPED`` status, carrying the real spend ledger and
clearing the in-progress marker so the next scheduled run is not blocked.

This file owns all six behavior contracts from 04-14-PLAN.md Task 1.
"""

from __future__ import annotations

import threading
import uuid
from datetime import UTC, datetime, timedelta

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
from huntloop.db.models import Company, Job, Run, RunStatus, RunTrigger
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


# ---------------------------------------------------------------------------
# GAP-16: finer substage checkpoints + race-safe terminal writes
# ---------------------------------------------------------------------------

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


def _counters(**overrides):
    return {**_ZERO_COUNTERS, **overrides}


def _seed_running_run(sessionmaker) -> uuid.UUID:
    from huntloop.db.repository import RunRepository

    session = sessionmaker()
    try:
        run = RunRepository(session).start(RunTrigger.MANUAL)
        session.commit()
        return run.id
    finally:
        session.close()


class _ListingAdapter:
    """Greenhouse-shaped fake returning a fixed listing set (no stop seeding)."""

    platform = "greenhouse"

    def __init__(self, listings: list[RawListing]) -> None:
        self.listings = listings

    def fetch(self, slug: str, *, client=None) -> FetchResult:
        return FetchResult(status=FetchStatus.OK, listings=list(self.listings))


class _StopOnScoreClient:
    """Queues triage/scoring responses and seeds the stop marker on the first
    scoring call — so the marker appears mid-batch, after a model call, exactly
    where the finer GAP-16 checkpoints must observe it."""

    def __init__(self, sessionmaker, *, triage: list, scoring: list) -> None:
        self.sessionmaker = sessionmaker
        self._triage = list(triage)
        self._scoring = list(scoring)
        self.calls = 0
        self._seeded = False

    def _seed_stop(self) -> None:
        from huntloop.graph.cancellation import request_stop

        self._seeded = True
        session = self.sessionmaker()
        try:
            running = find_run_in_progress(session)
            assert running is not None, "no RUNNING run to address the stop to"
            request_stop(session, running.id)
            session.commit()
        finally:
            session.close()

    class _Completions:
        def __init__(self, parent) -> None:
            self.parent = parent

        def create(self, **kwargs):
            import json
            from collections import namedtuple

            parent = self.parent
            system = kwargs["messages"][0]["content"]
            parent.calls += 1
            if "triaging job listings" in system:
                payload = parent._triage.pop(0)
            elif "scoring job listings" in system:
                if not parent._seeded:
                    parent._seed_stop()
                payload = parent._scoring.pop(0)
            else:
                raise AssertionError(f"unknown system prompt: {system[:80]!r}")

            Choice = namedtuple("Choice", ["message"])
            Message = namedtuple("Message", ["content"])
            Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])

            class FakeCompletion:
                def __init__(self) -> None:
                    self.choices = [Choice(message=Message(content=json.dumps(payload)))]
                    self.usage = Usage(prompt_tokens=10, completion_tokens=20)
                    self.model = kwargs.get("model", "fake-model")

            return FakeCompletion()

    @property
    def chat(self):
        parent = self

        class Chat:
            completions = _StopOnScoreClient._Completions(parent)

        return Chat()


def test_raise_if_stop_requested_or_finished_contracts(sessionmaker):
    from huntloop.db.repository import RunRepository
    from huntloop.graph.cancellation import RunStoppedByUser, clear_stop_request, request_stop
    from huntloop.graph.nodes import _raise_if_stop_requested_or_finished

    run_id = _seed_running_run(sessionmaker)

    # A RUNNING run with no marker returns cleanly.
    assert _raise_if_stop_requested_or_finished(sessionmaker, str(run_id)) is None

    # A marker naming this RUNNING run raises.
    session = sessionmaker()
    try:
        request_stop(session, run_id)
        session.commit()
    finally:
        session.close()
    with pytest.raises(RunStoppedByUser):
        _raise_if_stop_requested_or_finished(sessionmaker, str(run_id))

    # A terminal row raises even after the marker is cleared.
    session = sessionmaker()
    try:
        clear_stop_request(session, run_id)
        RunRepository(session).finish(
            run_id, status=RunStatus.STOPPED, **_counters()
        )
        session.commit()
    finally:
        session.close()
    with pytest.raises(RunStoppedByUser):
        _raise_if_stop_requested_or_finished(sessionmaker, str(run_id))

    # An absent run raises too.
    with pytest.raises(RunStoppedByUser):
        _raise_if_stop_requested_or_finished(sessionmaker, str(uuid.uuid4()))


def test_raise_if_stop_requested_stays_marker_only(sessionmaker):
    """The GAP-4 helper is unchanged: no marker is clean even on a terminal row."""
    from huntloop.db.repository import RunRepository
    from huntloop.graph.nodes import _raise_if_stop_requested

    run_id = _seed_running_run(sessionmaker)
    session = sessionmaker()
    try:
        RunRepository(session).finish(run_id, status=RunStatus.STOPPED, **_counters())
        session.commit()
    finally:
        session.close()

    assert _raise_if_stop_requested(sessionmaker, str(run_id)) is None


def test_finish_if_running_never_overwrites_terminal_row(sessionmaker):
    from huntloop.db.repository import RunRepository

    run_id = _seed_running_run(sessionmaker)
    session = sessionmaker()
    try:
        repo = RunRepository(session)

        applied = repo.finish_if_running(
            run_id, status=RunStatus.STOPPED, error_summary="stopped", **_counters()
        )
        assert applied is not None
        assert applied.status is RunStatus.STOPPED
        finished_at = applied.finished_at
        session.commit()

        overwritten = repo.finish_if_running(
            run_id,
            status=RunStatus.SUCCESS,
            error_summary="should be ignored",
            **_counters(companies_checked=99),
        )
        assert overwritten is None

        session.expire_all()
        row = session.get(Run, run_id)
        assert row.status is RunStatus.STOPPED
        assert row.finished_at == finished_at
        assert row.companies_checked == 0

        # finish() stays unconditional: it applies even to a terminal row.
        repo.finish(
            run_id, status=RunStatus.SUCCESS, **_counters(companies_checked=99)
        )
        assert row.status is RunStatus.SUCCESS
        assert row.companies_checked == 99
    finally:
        session.close()


def test_pending_stop_blocks_write_batch(sessionmaker, monkeypatch):
    """A stop arriving after scoring but before the write gate => zero jobs.

    One listing isolates the pre-write checkpoint: the marker is seeded during
    that listing's scoring call, the batch loop then ends, and process_employer's
    full stop/terminal gate immediately before write_batch must abort.
    """
    import huntloop.graph.nodes as nodes_mod

    seed_criteria(sessionmaker)
    make_company(sessionmaker, "WriteGateCo", "writegate")

    adapter = _ListingAdapter([make_listing("wg-1")])
    monkeypatch.setattr(nodes_mod, "get_adapter", lambda platform: adapter)

    client = _StopOnScoreClient(
        sessionmaker, triage=[TRIAGE_KEEP], scoring=[dims_response(4)]
    )

    summary = run_discovery(
        sessionmaker=sessionmaker,
        llm_client=client,
        http_client=object(),
        concurrency=1,
    )

    assert summary.status == RunStatus.STOPPED.value
    session = sessionmaker()
    try:
        run = session.execute(select(Run)).scalars().one()
        assert run.status is RunStatus.STOPPED
        assert session.execute(select(Job)).scalars().all() == []
    finally:
        session.close()


def test_stop_during_scoring_aborts_at_next_listing(sessionmaker, monkeypatch):
    """A stop mid-batch aborts at the NEXT listing, not after the whole batch."""
    import huntloop.graph.nodes as nodes_mod

    seed_criteria(sessionmaker)
    make_company(sessionmaker, "PerListingCo", "perlisting")

    adapter = _ListingAdapter([make_listing("pl-1"), make_listing("pl-2")])
    monkeypatch.setattr(nodes_mod, "get_adapter", lambda platform: adapter)

    client = _StopOnScoreClient(
        sessionmaker, triage=[TRIAGE_KEEP], scoring=[dims_response(4)]
    )

    summary = run_discovery(
        sessionmaker=sessionmaker,
        llm_client=client,
        http_client=object(),
        concurrency=1,
    )

    assert summary.status == RunStatus.STOPPED.value
    # Only the first listing was triaged/scored; the second aborted at its
    # top-of-loop checkpoint before any model call.
    assert client.calls == 2

    session = sessionmaker()
    try:
        run = session.execute(select(Run)).scalars().one()
        assert run.status is RunStatus.STOPPED
        assert session.execute(select(Job)).scalars().all() == []
    finally:
        session.close()


class _HtmlFetcher:
    """A statically-returning page fetcher for the crawl checkpoint contract."""

    def __init__(self, html: str) -> None:
        self.html = html
        self.fetched: list[str] = []

    def fetch(self, url: str):
        from types import SimpleNamespace

        self.fetched.append(url)
        return SimpleNamespace(ok=True, html=self.html, status_code=200, error=None)


def test_crawl_careers_honors_should_stop_between_pages():
    """`should_stop` fires after the base fetch and at the top of the detail
    loop, and a raised stop is NOT swallowed by the crawl's broad except."""
    from huntloop.discovery.crawl.careers import crawl_careers
    from huntloop.graph.cancellation import RunStoppedByUser

    html = (
        "<html><body><p>"
        + ("careers " * 80)
        + "</p>"
        '<a href="https://acme.com/jobs/1">Engineer</a>'
        '<a href="https://acme.com/jobs/2">Designer</a>'
        "</body></html>"
    )
    fetcher = _HtmlFetcher(html)

    checks: list[int] = []

    def _should_stop() -> None:
        checks.append(len(checks))
        if len(checks) >= 2:
            raise RunStoppedByUser()

    with pytest.raises(RunStoppedByUser):
        crawl_careers(fetcher, "https://acme.com/careers", should_stop=_should_stop)

    # First check after the base fetch, second entering the detail loop — the
    # raise propagated out instead of being caught by the crawl's except.
    assert checks == [0, 1]


def test_stop_during_second_employer_fetch_writes_no_jobs_for_it(
    sessionmaker, monkeypatch
):
    """End to end (concurrency=1): the second employer aborts post-fetch, so only
    the first employer's job is written while the run finalizes STOPPED."""
    import huntloop.graph.nodes as nodes_mod

    seed_criteria(sessionmaker)
    make_company(sessionmaker, "StopFirst", "stopfirst")
    make_company(sessionmaker, "StopSecond", "stopsecond")

    adapter = _StopSeedingAdapter(sessionmaker, [make_listing("stop-z")])
    monkeypatch.setattr(nodes_mod, "get_adapter", lambda platform: adapter)

    client = _RecordingClient(triage=[TRIAGE_KEEP], scoring=[dims_response(4)])

    summary = run_discovery(
        sessionmaker=sessionmaker,
        llm_client=client,
        http_client=object(),
        concurrency=1,
    )

    assert summary.status == RunStatus.STOPPED.value
    session = sessionmaker()
    try:
        runs = session.execute(select(Run)).scalars().all()
        assert len(runs) == 1, "exactly one Run row, never a stuck RUNNING row"
        assert runs[0].status is RunStatus.STOPPED
        jobs = session.execute(select(Job)).scalars().all()
        assert len(jobs) == 1, "the aborted second employer wrote no jobs"
    finally:
        session.close()
