"""Tests for DISC-06 and TRAK-06: writing fetched listings to the database."""

from __future__ import annotations

import threading
import uuid
from collections import namedtuple
from datetime import UTC, datetime
from decimal import Decimal

import pytest
from sqlalchemy import select

from huntloop.db.models import Company, FilterTier, Job, RunTrigger
from huntloop.db.repository import CompanyRepository, JobRepository
from huntloop.discovery.ats.base import RawListing
from huntloop.discovery.dedup import DedupKeyError
from huntloop.discovery.write import write_batch, write_listing
from huntloop.scoring.pipeline import ScoredListing


@pytest.fixture
def mock_company(main_session):
    repo = CompanyRepository(main_session)
    comp_id = repo.upsert_by_name("Acme Corp")
    return repo.get_by_name("Acme Corp")

@pytest.fixture
def mock_run(main_session):
    from huntloop.db.models import RunTrigger
    from huntloop.db.repository import RunRepository
    return RunRepository(main_session).start(trigger=RunTrigger.MANUAL)


def test_write_low_scorer_preserves_filter_tier(main_session, mock_company, mock_run):
    listing = RawListing(
        external_id="123", url="https://acme.com/1", title="Eng", location_raw="NY",
        description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
        comp_min=None, comp_max=None, raw={}
    )
    scored = ScoredListing(
        listing=listing, scored=False, tier_reached=FilterTier.DETERMINISTIC,
        overall=None, dimensions=None, flags=None, summary="",
        criteria_version=None, rubric_version=None, model=None,
        drop_reason="deterministic filter failed", error="", note="", usage=()
    )
    
    res = write_listing(main_session, mock_company, scored, run_id=mock_run.id)
    main_session.commit()
    
    assert res == "inserted"
    repo = JobRepository(main_session)
    job = repo.get_by_dedup_key("acmecorp:123")
    
    assert job is not None
    assert job.filter_tier_reached == FilterTier.DETERMINISTIC
    assert job.score_overall is None
    assert job.scored_at is None


def test_write_high_scorer_persists_score(main_session, mock_company, mock_run):
    listing = RawListing(
        external_id="456", url="https://acme.com/2", title="Eng", location_raw="NY",
        description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
        comp_min=None, comp_max=None, raw={}
    )
    scored = ScoredListing(
        listing=listing, scored=True, tier_reached=FilterTier.FULL,
        overall=4.5, dimensions={"a": 1}, flags={"f": True}, summary="Great",
        criteria_version=1, rubric_version="v2", model="test-model",
        drop_reason="", error="", note="", usage=()
    )
    
    write_listing(main_session, mock_company, scored, run_id=mock_run.id)
    main_session.commit()
    
    repo = JobRepository(main_session)
    job = repo.get_by_dedup_key("acmecorp:456")
    
    assert job is not None
    assert job.score_overall == 4.5
    assert job.scored_criteria_version == 1
    assert job.scored_rubric_version == "v2"
    assert job.scored_with_model == "test-model"


def test_write_batch_counts(main_session, mock_company, mock_run):
    l1 = RawListing(
        external_id="b1", url="https://acme.com/1", title="Eng", location_raw="NY",
        description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
        comp_min=None, comp_max=None, raw={}
    )
    l2 = RawListing(
        external_id="b2", url="https://acme.com/2", title="Eng", location_raw="NY",
        description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
        comp_min=None, comp_max=None, raw={}
    )
    
    s1 = ScoredListing(listing=l1, scored=False, tier_reached=FilterTier.DETERMINISTIC)
    s2 = ScoredListing(listing=l2, scored=False, tier_reached=FilterTier.DETERMINISTIC)
    
    out1 = write_batch(main_session, mock_company, [s1, s2], run_id=mock_run.id)
    assert out1.inserted == 2
    assert out1.updated == 0
    assert out1.failed == 0
    
    out2 = write_batch(main_session, mock_company, [s1, s2], run_id=mock_run.id)
    assert out2.inserted == 0
    assert out2.updated == 2
    assert out2.failed == 0


def test_write_batch_isolates_failures(main_session, mock_company, mock_run):
    good = RawListing(
        external_id="good", url="https://acme.com/g", title="Eng", location_raw="NY",
        description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
        comp_min=None, comp_max=None, raw={}
    )
    bad = RawListing(
        external_id=None, url=None, title="Bad", location_raw="NY",
        description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
        comp_min=None, comp_max=None, raw={}
    )
    
    s1 = ScoredListing(listing=good, scored=False, tier_reached=FilterTier.DETERMINISTIC)
    s2 = ScoredListing(listing=bad, scored=False, tier_reached=FilterTier.DETERMINISTIC)
    
    out = write_batch(main_session, mock_company, [s1, s2], run_id=mock_run.id)
    assert out.inserted == 1
    assert out.failed == 1
    assert "Bad:" in out.errors[0]
    
    assert JobRepository(main_session).count() == 1


def test_run_repository(main_session, mock_company):
    from huntloop.db.models import RunStatus, RunTrigger
    from huntloop.db.repository import RunRepository
    
    repo = RunRepository(main_session)
    run = repo.start(trigger=RunTrigger.MANUAL)
    assert run.status == RunStatus.RUNNING
    assert run.started_at is not None
    
    repo.record_error(run.id, mock_company.id, "discovery", "fetch failed")
    main_session.flush()
    
    repo.finish(
        run.id,
        status=RunStatus.SUCCESS,
        companies_checked=1,
        listings_fetched=2,
        after_dedup=2,
        after_deterministic=1,
        after_triage=1,
        scored=1,
        new_jobs_written=1,
        tokens_in=10,
        tokens_out=20,
        cost_usd=0.01,
        error_summary="1 error"
    )
    
    assert run.status == RunStatus.SUCCESS
    assert run.finished_at is not None
    assert run.error_summary == "1 error"
    
    recent = repo.list_recent(limit=5)
    assert len(recent) == 1
    assert recent[0].id == run.id

def test_writing_orphan_raises(main_session, mock_run):
    # Company not inserted
    company = Company(id=uuid.uuid4(), name="Orphan")
    listing = RawListing(
        external_id="123", url="https://acme.com/1", title="Eng", location_raw="NY",
        description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
        comp_min=None, comp_max=None, raw={}
    )
    scored = ScoredListing(
        listing=listing, scored=False, tier_reached=FilterTier.DETERMINISTIC,
        overall=None, dimensions=None, flags=None, summary="",
        criteria_version=None, rubric_version=None, model=None,
        drop_reason="", error="", note="", usage=()
    )

    with pytest.raises(Exception):
        write_listing(main_session, company, scored, run_id=mock_run.id)


# ---------------------------------------------------------------------------
# RUN-08 (03-04 Task 3): a cap-blocked listing must never reach the jobs table
# ---------------------------------------------------------------------------

class _PricedFakeClient:
    """OpenAI-shaped fake whose every call reports a fixed priced usage.

    Unbounded queues (it never runs out of responses) because the point of
    this test is where the run STOPS, not the responses themselves. Every
    call is recorded as (kind, user_message) so the blocked listing can be
    derived from the call log rather than a hardcoded index.
    """

    def __init__(self, *, prompt_tokens: int, completion_tokens: int) -> None:
        self.calls: list[tuple[str, str]] = []
        self._lock = threading.Lock()
        self._pt = prompt_tokens
        self._ct = completion_tokens

    class _Completions:
        def __init__(self, parent) -> None:
            self.parent = parent

        def create(self, **kwargs):
            return self.parent._create(**kwargs)

    class _Chat:
        def __init__(self, parent) -> None:
            self.completions = _PricedFakeClient._Completions(parent)

    @property
    def chat(self):
        return self._Chat(self)

    def _create(self, **kwargs):
        import json

        system = kwargs["messages"][0]["content"]
        user = kwargs["messages"][1]["content"]
        if "triaging job listings" in system:
            kind, payload = "triage", {"keep": True, "reason": "relevant"}
        elif "scoring job listings" in system:
            kind = "scoring"
            payload = {
                "role_fit": {"score": 4, "reason": "matches profile"},
                "seniority_fit": {"score": 4, "reason": "level matches"},
                "employer_fit": {"score": 4, "reason": "stable company"},
                "trajectory": {"score": 4, "reason": "clear growth"},
                "summary": "solid match",
            }
        else:
            raise AssertionError(f"unknown system prompt: {system[:80]!r}")

        with self._lock:
            self.calls.append((kind, user))

        Choice = namedtuple("Choice", ["message"])
        Message = namedtuple("Message", ["content"])
        Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])

        class FakeCompletion:
            choices = [Choice(message=Message(content=json.dumps(payload)))]
            usage = Usage(prompt_tokens=self._pt, completion_tokens=self._ct)

        return FakeCompletion()


def test_cap_blocked_listing_not_written(main_engine, monkeypatch):
    """RUN-08: with HUNTLOOP_RUN_SPEND_CAP_USD tripping mid-batch, a full
    run_discovery writes Job rows for the listings scored before the cap and
    ZERO rows for the listing the cap blocked -- write_listing persists every
    ScoredListing it is handed, so the blocked listing must be dropped before
    the write path ever sees it."""
    from unittest.mock import MagicMock

    import huntloop.graph.nodes as nodes_mod
    from huntloop.criteria.loader import save_new_criteria_version
    from huntloop.criteria.schema import (
        CompensationFloor,
        CriteriaPayload,
        DimensionWeights,
        LocationCriteria,
    )
    from huntloop.db.base import make_session_factory
    from huntloop.graph.build import run_discovery
    from huntloop.pricing.table import cost_for_usage

    now = datetime(2026, 9, 10, 8, 5, 0, tzinfo=UTC)
    session_factory = make_session_factory(main_engine)

    # Active criteria (run_discovery refuses to start without one).
    session = session_factory()
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
        company = Company(
            id=uuid.uuid4(), name="CapWriteCo", enabled=True,
            ats="greenhouse", ats_identifier="capwrite", resolved_at=now,
        )
        session.add(company)
        session.commit()
    finally:
        session.close()

    # Four distinguishable fresh listings; the title doubles as the marker the
    # call log is matched on.
    listings = [
        RawListing(
            external_id=f"cw-{i}", url=f"https://example.com/jobs/cw-{i}",
            title=f"cw-{i}", location_raw="New York, NY",
            description_plain="Python distributed systems role.",
            description_html=None, posted_at=now, comp_raw="$130k - $150k",
            comp_min=None, comp_max=None, raw={},
        )
        for i in range(4)
    ]

    class FakeAdapter:
        platform = "greenhouse"

        def fetch(self, slug, *, client=None):
            from huntloop.discovery.ats.base import FetchResult, FetchStatus

            return FetchResult(status=FetchStatus.OK, listings=listings)

    monkeypatch.setattr(nodes_mod, "get_adapter", lambda platform: FakeAdapter())

    # Both model calls priced as gpt-4o-mini at 1M/1M tokens = $0.75 per call,
    # $1.50 per listing. The cap is derived from the price table: room for
    # exactly two listings ($3.00) plus a hair, so listing 3's pre-dimension
    # check is what trips.
    per_call = cost_for_usage("gpt-4o-mini", 1_000_000, 1_000_000)
    cap = per_call * 4 + Decimal("0.000001")
    monkeypatch.setenv("HUNTLOOP_SCORING_MODEL", "gpt-4o-mini")
    monkeypatch.setenv("HUNTLOOP_RUN_SPEND_CAP_USD", str(cap))

    client = _PricedFakeClient(prompt_tokens=1_000_000, completion_tokens=1_000_000)

    run_discovery(
        sessionmaker=session_factory,
        llm_client=client,
        http_client=MagicMock(),
        trigger=RunTrigger.MANUAL,
        now=now,
    )

    # Derive the blocked listing from the call log: triaged (its cheap call
    # ran) but never dimension-called. A hardcoded index would silently pass
    # if ordering changed.
    triaged = {user.split("\n")[0] for kind, user in client.calls if kind == "triage"}
    dimensioned = {user.split("\n")[0] for kind, user in client.calls if kind == "scoring"}
    blocked_titles = triaged - dimensioned
    assert len(blocked_titles) == 1, f"expected exactly one cap-blocked listing, got {blocked_titles}"
    blocked_id = blocked_titles.pop().removeprefix("TITLE: ").strip()

    session = session_factory()
    try:
        jobs = session.scalars(select(Job)).all()
        written_ids = {j.external_id for j in jobs}

        # Fewer rows than the 4 fetched listings.
        assert len(jobs) < 4
        # The cap-blocked listing has NO row at all.
        assert blocked_id not in written_ids
        # Every listing that WAS fully scored before the cap got its row.
        assert written_ids == {f"cw-{i}" for i in range(2)}
    finally:
        session.close()
