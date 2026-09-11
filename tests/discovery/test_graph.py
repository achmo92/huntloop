"""Tests for the LangGraph orchestration (02-10).

DISC-03 is the load-bearing test: one employer's failure does not stop the run.
`test_continues_past_failure` injects a real exception into the middle employer
of a three-employer fan-out and asserts the other two employers' work survives,
through the actual compiled graph -- not through a mock of it.
"""

from __future__ import annotations

import dataclasses
import inspect
import json
import uuid
from datetime import datetime, timedelta, timezone
from typing import get_args, get_type_hints
from unittest.mock import MagicMock

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session

import huntloop.graph.nodes as nodes_mod
from huntloop.config import load_config
from huntloop.criteria.loader import save_new_criteria_version
from huntloop.criteria.schema import (
    CompensationFloor,
    CriteriaPayload,
    DimensionWeights,
    LocationCriteria,
)
from huntloop.db.models import Company, Criteria, FilterTier, Job, Run, RunError, RunStatus, RunTrigger
from huntloop.db.repository import RunRepository
from huntloop.discovery.ats.base import FetchResult, FetchStatus, RawListing
from huntloop.discovery.fetch.page import PageResult
from huntloop.graph.nodes import (
    fan_out_to_employers,
    finalize_run,
    load_employers,
    process_employer,
    run_status,
)
from huntloop.graph.state import DiscoveryState, EmployerResult

NOW = datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Fakes
# ---------------------------------------------------------------------------

class RecordingClient:
    """A fake OpenAI-shaped client that is safe under the graph's real concurrency.

    Send-dispatched employer branches run in threads and share this client, so a
    single ordered queue would interleave (a triage call could receive a scoring
    response and silently corrupt the run). Responses are therefore queued per
    call type and dispatched on the system prompt; every call is recorded so
    tests can assert exactly how many model calls a code path made (the
    --no-score zero-call assertion).
    """

    def __init__(self, *, triage: list | None = None, scoring: list | None = None,
                 extraction: list | None = None) -> None:
        import threading

        self._lock = threading.Lock()
        self._queues = {
            "triaging job listings": list(triage or []),
            "scoring job listings": list(scoring or []),
            "extract job postings": list(extraction or []),
        }
        self.calls: list[dict] = []

    class _Completions:
        def __init__(self, parent) -> None:
            self.parent = parent

        def create(self, **kwargs):
            parent = self.parent
            system = kwargs["messages"][0]["content"]
            with parent._lock:
                parent.calls.append(kwargs)
                queue = next(
                    (q for marker, q in parent._queues.items() if marker in system),
                    None,
                )
                if queue is None:
                    raise AssertionError(f"unknown system prompt: {system[:80]!r}")
                if not queue:
                    raise AssertionError(
                        "model call made but no queued response of this type -- "
                        "code path was expected to make zero (or fewer) model calls"
                    )
                resp = queue.pop(0)

            if isinstance(resp, Exception):
                raise resp

            from collections import namedtuple

            Choice = namedtuple("Choice", ["message"])
            Message = namedtuple("Message", ["content"])
            Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])
            choice = Choice(message=Message(content=json.dumps(resp)))

            class FakeCompletion:
                choices = [choice]
                usage = Usage(prompt_tokens=10, completion_tokens=20)
                model = kwargs.get("model", "fake-model")

            return FakeCompletion()

    @property
    def chat(self):
        class Chat:
            completions = self._Completions(self)

        return Chat()


TRIAGE_KEEP = {"keep": True, "reason": "relevant"}
TRIAGE_DROP = {"keep": False, "reason": "not relevant"}


def dims_response(score: int) -> dict:
    return {
        "role_fit": {"score": score, "reason": "matches profile"},
        "seniority_fit": {"score": score, "reason": "level matches"},
        "employer_fit": {"score": score, "reason": "stable company"},
        "trajectory": {"score": score, "reason": "clear growth"},
        "summary": "solid match",
    }


EXTRACTION_ONE = {
    "listings": [
        {
            "title": "Staff Backend Engineer",
            "url": None,
            "location": "Remote",
            "description": "Distributed systems role with Python.",
            "posted": None,
            "compensation": None,
        }
    ]
}


class FakeAdapter:
    """ATS adapter stub: raises for designated slugs, returns listings otherwise."""

    platform = "greenhouse"

    def __init__(self, listings_by_slug: dict[str, list[RawListing]] | None = None,
                 fail_slugs: frozenset[str] = frozenset()) -> None:
        self.listings_by_slug = listings_by_slug or {}
        self.fail_slugs = fail_slugs

    def fetch(self, slug: str, *, client=None) -> FetchResult:
        if slug in self.fail_slugs:
            raise RuntimeError(f"simulated 500 from {slug}")
        return FetchResult(status=FetchStatus.OK, listings=self.listings_by_slug.get(slug, []))


class FakePageFetcher:
    """PageFetcher stub serving canned HTML per URL."""

    def __init__(self, html_by_url: dict[str, str]) -> None:
        self.html_by_url = html_by_url
        self.fetched: list[str] = []

    def fetch(self, url: str) -> PageResult:
        self.fetched.append(url)
        if url in self.html_by_url:
            return PageResult(ok=True, url=url, final_url=url, status_code=200,
                              html=self.html_by_url[url])
        return PageResult(ok=False, url=url, status_code=404, error="not found")


class TrackingSession(Session):
    """Session subclass that records every close(), to prove finally-block behaviour."""

    closed: list[Session] = []   # rebound per-test by the tracking_sessionmaker fixture

    def close(self):
        type(self).closed.append(self)
        super().close()


# ---------------------------------------------------------------------------
# Fixtures and helpers
# ---------------------------------------------------------------------------

@pytest.fixture
def sessionmaker(portable_engine):
    from sqlalchemy.orm import sessionmaker as sm

    return sm(bind=portable_engine)


@pytest.fixture
def tracking_sessionmaker(portable_engine):
    from sqlalchemy.orm import sessionmaker as sm

    TrackingSession.closed = []
    return sm(bind=portable_engine, class_=TrackingSession)


@pytest.fixture
def default_criteria():
    return CriteriaPayload(
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
            amount="120000",
            currency="USD",
            period="annual",
        ),
    )


def seed_criteria(sessionmaker, payload) -> int:
    """Insert the active criteria row; a run without one must not start."""
    session = sessionmaker()
    try:
        version = save_new_criteria_version(session, payload)
        session.commit()
        return version
    finally:
        session.close()


def make_company(session, name, *, slug=None, careers_url=None, enabled=True) -> Company:
    company = Company(
        id=uuid.uuid4(),
        name=name,
        enabled=enabled,
        ats="greenhouse" if slug else None,
        ats_identifier=slug,
        careers_url=careers_url,
        resolved_at=NOW if slug else None,
    )
    session.add(company)
    session.commit()
    # Load all attributes, then detach: callers keep using company.id after the
    # creating session closes (commit expires attributes by default).
    session.refresh(company)
    session.expunge(company)
    return company


def make_listing(external_id: str, *, posted_days: int = 0,
                 title: str = "Senior Software Engineer") -> RawListing:
    return RawListing(
        external_id=external_id,
        url=f"https://example.com/jobs/{external_id}",
        title=title,
        description_plain="We are looking for a Python engineer with distributed systems experience.",
        description_html="<p>We are looking for a Python engineer.</p>",
        location_raw="New York, NY",
        posted_at=NOW - timedelta(days=posted_days),
        comp_raw="$130k - $150k",
    )


def start_run(sessionmaker) -> uuid.UUID:
    session = sessionmaker()
    try:
        run = RunRepository(session).start(RunTrigger.MANUAL)
        session.commit()
        return run.id
    finally:
        session.close()


def employer_state(sessionmaker, company_id, run_id=None, *, no_score=False,
                   criteria_version=1) -> dict:
    return {
        "company_id": str(company_id),
        "run_id": str(run_id or start_run(sessionmaker)),
        "criteria_version": criteria_version,
        "no_score": no_score,
    }


def run_errors(sessionmaker, run_id) -> list[RunError]:
    session = sessionmaker()
    try:
        return list(
            session.execute(select(RunError).where(RunError.run_id == run_id)).scalars()
        )
    finally:
        session.close()


def get_run(sessionmaker, run_id) -> Run | None:
    session = sessionmaker()
    try:
        return session.get(Run, run_id)
    finally:
        session.close()


CAREERS_HTML = (
    "<html><body><h1> Careers at Acme </h1>"
    + "<p> Acme builds things. " + "We hire engineers who like distributed systems. " * 40
    + "</p></body></html>"
)


# ---------------------------------------------------------------------------
# Task 1: graph state and the per-employer node (DISC-03)
# ---------------------------------------------------------------------------

class TestNodes:
    """Node-level behaviour: state reducers, fault isolation, both fetch paths."""

    def test_discovery_state_append_reducer(self):
        """employer_results/errors use operator.add, and adding two partial states
        yields BOTH branches' results -- not just the last one to finish."""
        import operator

        hints = get_type_hints(DiscoveryState, include_extras=True)
        for key in ("employer_results", "errors"):
            annotated = hints[key]
            reducer = get_args(annotated)[-1]
            assert reducer is operator.add, f"{key} is not annotated with operator.add"

        # Direct reducer behaviour: two concurrent branches' partial states merge.
        r1: EmployerResult = {"company_id": "a", "fetched": 3}
        r2: EmployerResult = {"company_id": "b", "fetched": 5}
        merged = operator.add([r1], [r2])
        assert merged == [r1, r2]

    def test_process_employer_resolved_ats_returns_result(self, sessionmaker, default_criteria, monkeypatch):
        """A resolved ATS employer produces full per-stage counts and error=None."""
        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            company = make_company(session, "AtsCo", slug="atsco")
        finally:
            session.close()
        run_id = start_run(sessionmaker)

        listings = [
            make_listing("fresh-1", posted_days=0),   # passes deterministic filters
            make_listing("stale-1", posted_days=60),  # dropped by posting_age (30d)
        ]
        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug={"atsco": listings}),
        )
        client = RecordingClient(triage=[TRIAGE_KEEP], scoring=[dims_response(4)])

        result = process_employer(
            employer_state(sessionmaker, company.id, run_id),
            sessionmaker=sessionmaker,
            llm_client=client,
            http_client=MagicMock(),
        )["employer_results"][0]

        assert result["error"] is None
        assert result["path"] == "ats"
        assert result["fetched"] == 2
        assert result["after_dedup"] == 2
        assert result["after_deterministic"] == 1     # stale listing dropped here
        assert result["after_triage"] == 1
        assert result["scored"] == 1
        assert result["written"] == 2                  # DISC-06: drops are written too
        assert result["updated"] == 0
        assert result["tokens_in"] == 20               # 2 model calls x prompt_tokens=10
        assert result["tokens_out"] == 40
        assert len(client.calls) == 2                  # triage + scoring, nothing more

    def test_continues_past_failure(self, sessionmaker, default_criteria, monkeypatch):
        """DISC-03: the middle employer's exception is caught, recorded against
        that employer with its stage, and the other two employers still complete
        through the real compiled graph."""
        from huntloop.graph.build import build_graph

        criteria_version = seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            good1 = make_company(session, "Good1", slug="good1")
            bad = make_company(session, "Bad", slug="bad")
            good2 = make_company(session, "Good2", slug="good2")
        finally:
            session.close()
        run_id = start_run(sessionmaker)

        listings = {slug: [make_listing(f"{slug}-1")] for slug in ("good1", "good2")}
        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug=listings, fail_slugs={"bad"}),
        )
        client = RecordingClient(triage=[TRIAGE_KEEP, TRIAGE_KEEP], scoring=[dims_response(4), dims_response(4)])

        graph = build_graph(
            sessionmaker=sessionmaker,
            llm_client=client,
            http_client=MagicMock(),
            static_fetcher=MagicMock(),
        )
        final = graph.invoke({
            "run_id": str(run_id),
            "criteria_version": criteria_version,
            "no_score": False,
            "company_ids": [str(good1.id), str(bad.id), str(good2.id)],
            "employer_results": [],
            "errors": [],
        })

        results = final["employer_results"]
        # Three employers contributed through the append reducer -- not one, not two.
        assert len(results) == 3

        errored = [r for r in results if r.get("error")]
        assert len(errored) == 1
        assert errored[0]["company_id"] == str(bad.id)
        assert errored[0]["company_name"] == "Bad"
        assert errored[0]["stage"] == "fetch"
        assert "simulated 500" in errored[0]["error"]

        # The two healthy employers produced complete results with real writes.
        by_company = {r["company_id"]: r for r in results}
        for good in (good1, good2):
            r = by_company[str(good.id)]
            assert r["error"] is None
            assert r["fetched"] == 1
            assert r["scored"] == 1
            assert r["written"] == 1

        # The failure is recorded against ITS company id, not the run generally.
        errors = run_errors(sessionmaker, run_id)
        assert len(errors) == 1
        assert errors[0].company_id == bad.id
        assert errors[0].stage == "fetch"

        # finalize_run ran: the run row carries a terminal status.
        run = get_run(sessionmaker, run_id)
        assert run.status is RunStatus.PARTIAL
        assert run.finished_at is not None

    def test_error_recorded_against_correct_company(self, sessionmaker):
        """RunRepository.record_error stamps the company_id it was given (02-09
        contract the graph depends on for per-employer attribution)."""
        session = sessionmaker()
        try:
            run = RunRepository(session).start(RunTrigger.MANUAL)
            company = make_company(session, "ErrCo")
            session.commit()

            error = RunRepository(session).record_error(
                run.id, company.id, "fetch", "boom"
            )
            assert error.company_id == company.id
            assert error.stage == "fetch"
            assert error.message == "boom"
        finally:
            session.close()

    def test_process_employer_unresolved_with_careers_url(self, sessionmaker, default_criteria, monkeypatch):
        """An UNRESOLVED employer with a careers_url takes the crawl path and
        still produces counts end to end."""
        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            company = make_company(session, "CrawlCo", careers_url="https://acme.com/careers")
        finally:
            session.close()
        run_id = start_run(sessionmaker)

        fetcher = FakePageFetcher({"https://acme.com/careers": CAREERS_HTML})
        # One extraction call, then triage + scoring for the single extracted listing.
        client = RecordingClient(extraction=[EXTRACTION_ONE], triage=[TRIAGE_KEEP], scoring=[dims_response(4)])

        result = process_employer(
            employer_state(sessionmaker, company.id, run_id),
            sessionmaker=sessionmaker,
            llm_client=client,
            http_client=MagicMock(),
            static_fetcher=fetcher,
        )["employer_results"][0]

        assert result["error"] is None
        assert result["path"] == "crawl"
        assert result["fetched"] == 1
        assert result["after_dedup"] == 1
        assert result["scored"] == 1
        assert result["written"] == 1
        assert fetcher.fetched == ["https://acme.com/careers"]

    def test_process_employer_unresolved_no_careers_url(self, sessionmaker, default_criteria):
        """An UNRESOLVED employer with no careers_url gets a zero-count result
        with a named path (skipped), not an error."""
        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            company = make_company(session, "SkippedCo")
        finally:
            session.close()
        run_id = start_run(sessionmaker)

        result = process_employer(
            employer_state(sessionmaker, company.id, run_id),
            sessionmaker=sessionmaker,
            llm_client=MagicMock(),
            http_client=MagicMock(),
        )["employer_results"][0]

        assert result["error"] is None
        assert result["path"] == "skipped"
        assert result["fetched"] == 0
        assert result["after_dedup"] == 0
        assert result["written"] == 0

    def test_record_fetch_outcome_called_once_ats_path(self, sessionmaker, default_criteria, monkeypatch):
        """record_fetch_outcome is called exactly once with the real FetchResult
        on the ATS path."""
        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            company = make_company(session, "AtsCo2", slug="atsco2")
        finally:
            session.close()
        run_id = start_run(sessionmaker)

        calls: list = []
        monkeypatch.setattr(
            nodes_mod, "record_fetch_outcome",
            lambda session, comp, result: calls.append(result),
        )
        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug={"atsco2": [make_listing("a-1")]}),
        )

        process_employer(
            employer_state(sessionmaker, company.id, run_id, no_score=True),
            sessionmaker=sessionmaker,
            llm_client=RecordingClient(),
            http_client=MagicMock(),
        )

        assert len(calls) == 1
        assert isinstance(calls[0], FetchResult)
        assert calls[0].status is FetchStatus.OK

    def test_record_fetch_outcome_called_once_crawl_path(self, sessionmaker, default_criteria, monkeypatch):
        """record_fetch_outcome is called exactly once with the real FetchResult
        on the crawl path too (via to_fetch_result, DISC-04 semantics preserved)."""
        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            company = make_company(session, "CrawlCo2", careers_url="https://acme.com/careers")
        finally:
            session.close()
        run_id = start_run(sessionmaker)

        calls: list = []
        monkeypatch.setattr(
            nodes_mod, "record_fetch_outcome",
            lambda session, comp, result: calls.append(result),
        )
        fetcher = FakePageFetcher({"https://acme.com/careers": CAREERS_HTML})
        client = RecordingClient(extraction=[EXTRACTION_ONE], triage=[TRIAGE_KEEP], scoring=[dims_response(4)])

        process_employer(
            employer_state(sessionmaker, company.id, run_id),
            sessionmaker=sessionmaker,
            llm_client=client,
            http_client=MagicMock(),
            static_fetcher=fetcher,
        )

        assert len(calls) == 1
        assert isinstance(calls[0], FetchResult)

    def test_no_score_zero_model_calls(self, sessionmaker, default_criteria, monkeypatch):
        """--no-score runs fetch, dedup and deterministic filters and stops:
        zero model calls, scored == 0."""
        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            company = make_company(session, "NoScoreCo", slug="noscore")
        finally:
            session.close()
        run_id = start_run(sessionmaker)

        listings = [make_listing("ns-1", posted_days=0), make_listing("ns-2", posted_days=60)]
        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug={"noscore": listings}),
        )
        # Empty queue: any model call raises AssertionError via RecordingClient,
        # and the explicit calls assertion below is the real check.
        client = RecordingClient()

        result = process_employer(
            employer_state(sessionmaker, company.id, run_id, no_score=True),
            sessionmaker=sessionmaker,
            llm_client=client,
            http_client=MagicMock(),
        )["employer_results"][0]

        assert client.calls == []
        assert result["scored"] == 0
        assert result["after_deterministic"] == 1     # the fresh listing passed filters
        assert result["after_triage"] == 0
        assert result["written"] == 2
        assert result["error"] is None

    def test_dedup_key_error_counted_in_failed(self, sessionmaker, default_criteria, monkeypatch):
        """A DedupKeyError on one listing counts in the employer's `failed`
        and does not fail the employer."""
        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            company = make_company(session, "DedupCo", slug="dedup")
        finally:
            session.close()
        run_id = start_run(sessionmaker)

        good = make_listing("d-1")
        # Neither external id nor usable URL: unkeyable -> DedupKeyError.
        unkeyable = RawListing(external_id=None, url="", title="Broken")

        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug={"dedup": [good, unkeyable]}),
        )

        result = process_employer(
            employer_state(sessionmaker, company.id, run_id, no_score=True),
            sessionmaker=sessionmaker,
            llm_client=RecordingClient(),
            http_client=MagicMock(),
        )["employer_results"][0]

        assert result["error"] is None
        assert result["fetched"] == 2
        assert result["after_dedup"] == 1
        assert result["failed"] == 1
        assert result["written"] == 1

    def test_session_closed_even_when_employer_fails(self, tracking_sessionmaker, default_criteria, monkeypatch):
        """process_employer closes its session in a finally block even when the
        employer's work raises (DISC-03 catch path exercised)."""
        seed_criteria(tracking_sessionmaker, default_criteria)
        session = tracking_sessionmaker()
        try:
            company = make_company(session, "BoomCo", slug="boom")
        finally:
            session.close()
        run_id = start_run(tracking_sessionmaker)

        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(fail_slugs={"boom"}),
        )

        result = process_employer(
            employer_state(tracking_sessionmaker, company.id, run_id),
            sessionmaker=tracking_sessionmaker,
            llm_client=RecordingClient(),
            http_client=MagicMock(),
        )["employer_results"][0]

        assert result["error"] is not None
        assert TrackingSession.closed, "no session was ever closed"

    def test_fan_out_returns_send_per_employer(self):
        """fan_out_to_employers returns one Send per enabled employer."""
        from langgraph.types import Send

        state: DiscoveryState = {
            "run_id": "run", "criteria_version": 1, "no_score": False,
            "company_ids": ["a", "b", "c"], "employer_results": [], "errors": [],
        }
        sends = fan_out_to_employers(state)

        assert len(sends) == 3
        assert all(isinstance(s, Send) for s in sends)
        assert all(s.node == "process_employer" for s in sends)
        assert [s.arg["company_id"] for s in sends] == ["a", "b", "c"]

    def test_fan_out_empty_when_no_employers(self):
        """fan_out_to_employers returns [] when there are no enabled employers."""
        state: DiscoveryState = {
            "run_id": "run", "criteria_version": 1, "no_score": False,
            "company_ids": [], "employer_results": [], "errors": [],
        }
        assert fan_out_to_employers(state) == []

    def test_load_employers_returns_enabled_only(self, sessionmaker):
        """load_employers returns ids for enabled companies only."""
        session = sessionmaker()
        try:
            make_company(session, "Enabled1", enabled=True)
            make_company(session, "Enabled2", enabled=True)
            make_company(session, "Disabled", enabled=False)
        finally:
            session.close()

        result = load_employers({}, sessionmaker=sessionmaker)

        assert len(result["company_ids"]) == 2

    def test_run_status_zero_employers_is_success(self):
        """A run with zero employers is a clean SUCCESS, not an error."""
        assert run_status([]) is RunStatus.SUCCESS


# ---------------------------------------------------------------------------
# Task 2: StateGraph assembly, RetryPolicy, bounded concurrency, entrypoint
# ---------------------------------------------------------------------------

class TestGraph:
    """Graph-level behaviour: assembly, retry policy, concurrency, run lifecycle."""

    def test_build_graph_returns_compiled_graph(self, sessionmaker):
        """build_graph compiles with load_employers, process_employer, finalize_run."""
        from huntloop.graph.build import build_graph

        graph = build_graph(
            sessionmaker=sessionmaker,
            llm_client=RecordingClient(),
            http_client=MagicMock(),
        )

        node_names = set(graph.get_graph().nodes)
        assert {"load_employers", "process_employer", "finalize_run"} <= node_names

    def test_process_employer_registered_with_retry_policy(self, sessionmaker):
        """The process_employer node carries a RetryPolicy with max_attempts >= 2
        (registered via the compiled graph's node spec)."""
        from langgraph.types import RetryPolicy

        from huntloop.graph.build import build_graph

        graph = build_graph(
            sessionmaker=sessionmaker,
            llm_client=RecordingClient(),
            http_client=MagicMock(),
        )
        policies = graph.nodes["process_employer"].retry_policy
        assert policies, "process_employer registered without a retry policy"
        assert isinstance(policies[0], RetryPolicy)
        assert policies[0].max_attempts >= 2

    def test_run_discovery_constructs_rendered_fetcher(self, sessionmaker, default_criteria, monkeypatch):
        """run_discovery must wire a RenderedPageFetcher into the graph when the
        caller doesn't inject one — otherwise the crawl path can never render
        SPA-style careers pages (found live at the 02-12 checkpoint: Atlassian)."""
        from huntloop.graph import build as build_mod
        from huntloop.graph.build import run_discovery

        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            make_company(session, "Co0", slug="co0")
        finally:
            session.close()

        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug={"co0": [make_listing("co0-1")]}),
        )
        client = RecordingClient(triage=[TRIAGE_KEEP], scoring=[dims_response(4)])

        captured = {}
        real_build = build_mod.build_graph

        def spying_build_graph(**kwargs):
            captured.update(kwargs)
            return real_build(**kwargs)

        monkeypatch.setattr(build_mod, "build_graph", spying_build_graph)

        run_discovery(sessionmaker=sessionmaker, llm_client=client,
                      http_client=MagicMock())

        assert captured["rendered_fetcher"] is not None

    def test_run_discovery_returns_run_summary(self, sessionmaker, default_criteria, monkeypatch):
        """run_discovery over three employers: RunSummary totals equal the sum of
        the employer results, and finalize_run stamped the same totals on the Run row."""
        from huntloop.graph.build import run_discovery

        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            for i in range(3):
                make_company(session, f"Co{i}", slug=f"co{i}")
        finally:
            session.close()

        listings = {f"co{i}": [make_listing(f"co{i}-1")] for i in range(3)}
        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug=listings),
        )
        client = RecordingClient(triage=[TRIAGE_KEEP] * 3, scoring=[dims_response(4)] * 3)

        summary = run_discovery(sessionmaker=sessionmaker, llm_client=client,
                                http_client=MagicMock())

        assert summary.companies_checked == 3
        assert summary.listings_fetched == 3
        assert summary.after_dedup == 3
        assert summary.after_deterministic == 3
        assert summary.after_triage == 3
        assert summary.scored == 3
        assert summary.new_jobs_written == 3
        assert summary.updated == 0
        assert summary.failed == 0
        assert summary.errors == ()
        assert summary.status == "success"
        assert summary.tokens_in == 60 and summary.tokens_out == 120

        # finalize_run wrote the same totals to the Run row.
        run = get_run(sessionmaker, uuid.UUID(summary.run_id))
        assert run.status is RunStatus.SUCCESS
        assert run.listings_fetched == 3
        assert run.scored == 3
        assert run.new_jobs_written == 3
        assert run.finished_at is not None

    def test_max_concurrency_from_config(self, sessionmaker, default_criteria, monkeypatch):
        """run_discovery passes Config.max_employer_concurrency (or an explicit
        override) into the graph invoke's config."""
        import huntloop.graph.build as build_mod
        from huntloop.graph.build import run_discovery

        seed_criteria(sessionmaker, default_criteria)

        captured: dict = {}

        class FakeGraph:
            def invoke(self, state, config=None):
                captured["config"] = config
                return state

        monkeypatch.setattr(build_mod, "build_graph", lambda **kwargs: FakeGraph())

        # Explicit override wins.
        run_discovery(sessionmaker=sessionmaker, llm_client=object(),
                      http_client=MagicMock(), concurrency=7)
        assert captured["config"] == {"max_concurrency": 7}

        # Default: from Config.max_employer_concurrency, never hardcoded.
        run_discovery(sessionmaker=sessionmaker, llm_client=object(),
                      http_client=MagicMock())
        assert captured["config"] == {"max_concurrency": load_config().max_employer_concurrency}

    def test_run_discovery_zero_employers(self, sessionmaker, default_criteria,
                                          credentials_engine, monkeypatch):
        """A run with zero enabled employers finishes cleanly: zero counters,
        terminal status, no exception."""
        from huntloop.graph.build import run_discovery

        seed_criteria(sessionmaker, default_criteria)
        # The LLM client is still built (no_score is False) even though nothing
        # will use it -- give resolve_llm_api_key a key to find.
        monkeypatch.setenv("HUNTLOOP_OPENAI_API_KEY", "sk-test-only")

        summary = run_discovery(sessionmaker=sessionmaker, http_client=MagicMock())

        assert summary.companies_checked == 0
        assert summary.listings_fetched == 0
        assert summary.new_jobs_written == 0
        assert summary.scored == 0
        assert summary.status == "success"
        assert summary.errors == ()

        run = get_run(sessionmaker, uuid.UUID(summary.run_id))
        assert run is not None
        assert run.status is RunStatus.SUCCESS
        assert run.finished_at is not None

    def test_run_discovery_all_employers_error_finishes_run(self, sessionmaker, default_criteria, monkeypatch):
        """Even when EVERY employer errors there is exactly one Run row and it
        finishes with a terminal status -- never stuck in 'running'."""
        from huntloop.graph.build import run_discovery

        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            for i in range(2):
                make_company(session, f"Down{i}", slug=f"down{i}")
        finally:
            session.close()

        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(fail_slugs={"down0", "down1"}),
        )

        summary = run_discovery(sessionmaker=sessionmaker, llm_client=object(),
                                http_client=MagicMock())

        assert summary.companies_checked == 2
        assert len(summary.errors) == 2
        assert summary.status == "failed"

        session = sessionmaker()
        try:
            runs = list(session.execute(select(Run)).scalars())
            assert len(runs) == 1
            assert runs[0].status is RunStatus.FAILED
            assert runs[0].finished_at is not None
            assert runs[0].error_summary is not None
        finally:
            session.close()

    def test_run_discovery_partial_failure_error_summary(self, sessionmaker, default_criteria, monkeypatch):
        """A partially-failed run reports per-employer errors by name and stage."""
        from huntloop.graph.build import run_discovery

        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            make_company(session, "AliveCo", slug="alive")
            make_company(session, "DeadCo", slug="dead")
        finally:
            session.close()

        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(
                listings_by_slug={"alive": [make_listing("alive-1")]},
                fail_slugs={"dead"},
            ),
        )
        client = RecordingClient(triage=[TRIAGE_KEEP], scoring=[dims_response(4)])

        summary = run_discovery(sessionmaker=sessionmaker, llm_client=client,
                                http_client=MagicMock())

        assert summary.status == "partial"
        assert summary.scored == 1
        assert len(summary.errors) == 1
        assert summary.errors[0]["company"] == "DeadCo"
        assert summary.errors[0]["stage"] == "fetch"
        assert "simulated 500" in summary.errors[0]["message"]

    def test_run_discovery_no_active_criteria_raises(self, sessionmaker):
        """No active criteria -> named error raised BEFORE a Run row is created."""
        from huntloop.graph.build import NoActiveCriteria, run_discovery

        session = sessionmaker()
        try:
            assert session.execute(select(Criteria)).scalars().first() is None
        finally:
            session.close()

        with pytest.raises(NoActiveCriteria, match="no active criteria"):
            run_discovery(sessionmaker=sessionmaker, http_client=MagicMock())

        session = sessionmaker()
        try:
            assert list(session.execute(select(Run)).scalars()) == []
        finally:
            session.close()

    def test_run_discovery_no_score_builds_no_llm_client(self, sessionmaker, default_criteria, monkeypatch):
        """--no-score builds NO LLM client at all (works with no API key
        configured) and makes zero model calls end to end."""
        import huntloop.graph.build as build_mod
        from huntloop.graph.build import run_discovery

        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            make_company(session, "FreshCo", slug="fresh")
        finally:
            session.close()

        # Fail loudly if any code path builds an LM client under --no-score:
        # run_discovery imports get_llm_client lazily, so patch the defining module.
        import huntloop.llm.client as llm_client_mod

        monkeypatch.setattr(
            llm_client_mod, "get_llm_client",
            lambda *a, **k: pytest.fail("LLM client built under --no-score"),
        )
        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(
                listings_by_slug={"fresh": [make_listing("fresh-1")]}
            ),
        )

        summary = run_discovery(sessionmaker=sessionmaker, no_score=True,
                                http_client=MagicMock())

        assert summary.scored == 0
        assert summary.tokens_in == 0
        assert summary.tokens_out == 0
        assert summary.new_jobs_written == 1
        assert summary.status == "success"

    def test_run_summary_fields_match_cli_rendering(self):
        """RunSummary exposes the exact fields 02-11 renders, verbatim."""
        from huntloop.graph.build import RunSummary

        fields = {f.name for f in dataclasses.fields(RunSummary)}
        expected = {
            "run_id",
            "companies_checked",
            "listings_fetched",
            "after_dedup",
            "after_deterministic",
            "after_triage",
            "scored",
            "new_jobs_written",
            "updated",
            "failed",
            "tokens_in",
            "tokens_out",
            "cost_usd",
            "errors",
            "status",
            "top_listings",
        }
        assert fields == expected

    def test_graph_invoked_synchronously(self):
        """run_discovery contains no async machinery: no await, no asyncio,
        no asynchronous invoke."""
        import huntloop.graph.build as build_mod

        source = inspect.getsource(build_mod.run_discovery)
        assert "await " not in source
        assert "asyncio" not in source
        assert "ainvoke" not in source
        assert not inspect.iscoroutinefunction(build_mod.run_discovery)

    def test_consecutive_runs_write_then_update(self, sessionmaker, default_criteria, monkeypatch):
        """DISC-06 end to end: the same fixture data writes on the first run and
        updates on the second -- never duplicates."""
        from huntloop.graph.build import run_discovery

        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            make_company(session, "TwiceCo", slug="twice")
        finally:
            session.close()

        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug={"twice": [make_listing("twice-1")]}),
        )

        first = run_discovery(
            sessionmaker=sessionmaker, llm_client=RecordingClient(triage=[TRIAGE_KEEP], scoring=[dims_response(4)]),
            http_client=MagicMock(),
        )
        assert first.new_jobs_written == 1
        assert first.updated == 0

        second = run_discovery(
            sessionmaker=sessionmaker, llm_client=RecordingClient(triage=[TRIAGE_KEEP], scoring=[dims_response(4)]),
            http_client=MagicMock(),
        )
        assert second.new_jobs_written == 0
        assert second.updated == 1

    def test_top_listings_best_first(self, sessionmaker, default_criteria, monkeypatch):
        """top_listings carries this run's scored jobs, best score first."""
        from huntloop.graph.build import run_discovery

        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            make_company(session, "RankCo", slug="rank")
        finally:
            session.close()

        strong = make_listing("rank-strong", title="Principal Engineer")
        weak = make_listing("rank-weak", title="Junior Support")
        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug={"rank": [strong, weak]}),
        )
        # strong scores 5s, weak scores 3s.
        client = RecordingClient(triage=[TRIAGE_KEEP, TRIAGE_KEEP], scoring=[dims_response(5), dims_response(3)])

        summary = run_discovery(sessionmaker=sessionmaker, llm_client=client,
                                http_client=MagicMock())

        assert summary.scored == 2
        assert len(summary.top_listings) == 2
        assert summary.top_listings[0]["title"] == "Principal Engineer"
        assert float(summary.top_listings[0]["score"]) > float(summary.top_listings[1]["score"])

    def test_finalize_run_sums_counters_and_finishes(self, sessionmaker, default_criteria):
        """finalize_run sums every counter across employer_results and writes
        them to the Run row with a terminal status."""
        seed_criteria(sessionmaker, default_criteria)
        run_id = start_run(sessionmaker)

        results: list[EmployerResult] = [
            {"company_id": "a", "company_name": "A", "fetched": 3, "after_dedup": 2,
             "after_deterministic": 2, "after_triage": 1, "scored": 1, "written": 2,
             "updated": 0, "failed": 0, "tokens_in": 20, "tokens_out": 40},
            {"company_id": "b", "company_name": "B", "fetched": 1, "after_dedup": 1,
             "after_deterministic": 1, "after_triage": 1, "scored": 1, "written": 1,
             "updated": 1, "failed": 1, "tokens_in": 10, "tokens_out": 20,
             "error": "RuntimeError: x", "stage": "score"},
        ]
        finalize_run(
            {"run_id": str(run_id), "employer_results": results},
            sessionmaker=sessionmaker,
        )

        run = get_run(sessionmaker, run_id)
        assert run.status is RunStatus.PARTIAL
        assert run.companies_checked == 2
        assert run.listings_fetched == 4
        assert run.after_dedup == 3
        assert run.after_deterministic == 3
        assert run.after_triage == 2
        assert run.scored == 2
        assert run.new_jobs_written == 3
        assert run.tokens_in == 30
        assert run.tokens_out == 60
        assert run.finished_at is not None
        assert "B@score" in run.error_summary


# ---------------------------------------------------------------------------
# Plan 03-04 Task 1: the spend cap inside the graph (RUN-08)
# ---------------------------------------------------------------------------

class TestSpendCapInGraph:
    """The run-level spend cap as _score_batch/process_employer enforce it.

    The tracker's per-call usage is 10 prompt / 20 completion tokens, so a
    listing's cost is cost_for_usage(triage model) + cost_for_usage(scoring
    model). Caps are derived from the price table, never magic numbers.
    """

    def test_score_batch_stops_at_cap(self, default_criteria):
        """A tracker that trips mid-batch stops scoring: listings scored before
        the cap are returned, the blocked listing is NOT, and the model calls
        for anything after the trip never happen."""
        from huntloop.pricing.table import cost_for_usage
        from huntloop.scoring.spend_cap import SpendTracker

        per_listing = (
            cost_for_usage("gpt-4o-mini", 10, 20)   # triage call
            + cost_for_usage("gpt-4o", 10, 20)      # dimension call
        )
        # Exactly two listings fit under the cap; the third's first check trips.
        tracker = SpendTracker(cap_usd=per_listing * 2)

        listings = [make_listing(f"cap-{i}") for i in range(5)]
        client = RecordingClient(triage=[TRIAGE_KEEP] * 5, scoring=[dims_response(4)] * 5)
        state = {"criteria_version": 1, "no_score": False}

        out = nodes_mod._score_batch(
            listings, default_criteria, state,
            llm_client=client, spend_tracker=tracker,
        )

        assert len(out.listings) == 2
        assert out.capped is True
        # Two completed listings = one triage + one dimension call each. The
        # third listing's calls were refused before they were made.
        assert len(client.calls) == 4
        # The tracker saw exactly those four calls' usage.
        assert tracker.tokens_in == 40
        assert tracker.tokens_out == 80

    def test_score_batch_without_tracker_is_unchanged(self, default_criteria):
        """spend_tracker=None keeps the pre-cap behaviour identical."""
        listings = [make_listing(f"nocap-{i}") for i in range(5)]
        client = RecordingClient(triage=[TRIAGE_KEEP] * 5, scoring=[dims_response(4)] * 5)
        state = {"criteria_version": 1, "no_score": False}

        out = nodes_mod._score_batch(
            listings, default_criteria, state,
            llm_client=client, spend_tracker=None,
        )

        assert len(out.listings) == 5
        assert out.capped is False
        assert len(client.calls) == 10

    def test_capped_employer_is_not_errored(self, sessionmaker, default_criteria, monkeypatch):
        """A cap trip inside process_employer marks the employer capped, NOT
        errored -- a budget stop must not push the run to PARTIAL -- and the
        counters accumulated before the cap tripped are preserved."""
        from huntloop.pricing.table import cost_for_usage
        from huntloop.scoring.spend_cap import SpendTracker

        seed_criteria(sessionmaker, default_criteria)
        session = sessionmaker()
        try:
            company = make_company(session, "CapCo", slug="capco")
        finally:
            session.close()
        run_id = start_run(sessionmaker)

        listings = [make_listing("cap-keep"), make_listing("cap-blocked")]
        monkeypatch.setattr(
            nodes_mod, "get_adapter",
            lambda platform: FakeAdapter(listings_by_slug={"capco": listings}),
        )

        # Room for exactly one listing; the second is blocked by the cap.
        tracker = SpendTracker(
            cap_usd=cost_for_usage("gpt-4o-mini", 10, 20) + cost_for_usage("gpt-4o", 10, 20)
        )
        client = RecordingClient(triage=[TRIAGE_KEEP] * 2, scoring=[dims_response(4)] * 2)

        result = process_employer(
            employer_state(sessionmaker, company.id, run_id),
            sessionmaker=sessionmaker,
            llm_client=client,
            http_client=MagicMock(),
            spend_tracker=tracker,
        )["employer_results"][0]

        assert result["capped"] is True
        assert result.get("error") is None
        # Counters from before the cap tripped survive in the result.
        assert result["fetched"] == 2
        assert result["scored"] == 1
        # The cap-blocked listing was never written: write_listing persists
        # every ScoredListing it is handed, so dropping it is the only correct
        # mechanism (the 03-04 plan's load-bearing detail).
        assert result["written"] == 1