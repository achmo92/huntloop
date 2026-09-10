"""Tests for DISC-06 and TRAK-06: writing fetched listings to the database."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest

from huntloop.db.models import Company, FilterTier
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
