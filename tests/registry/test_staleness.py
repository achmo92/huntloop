"""Tests for REG-05 staleness tracking."""

from __future__ import annotations

import pytest

from huntloop.db.models import Company
from huntloop.db.repository import CompanyRepository
from huntloop.discovery.ats.base import ErrorKind, FetchResult, FetchStatus, RawListing
from huntloop.registry.staleness import is_possibly_stale, record_fetch_outcome, staleness_message


@pytest.fixture
def mock_company(main_session):
    repo = CompanyRepository(main_session)
    comp_id = repo.upsert_by_name("Acme Corp")
    return repo.get_by_name("Acme Corp")


def test_empty_increments_staleness(main_session, mock_company):
    assert mock_company.consecutive_empty_runs == 0
    
    res = FetchResult(status=FetchStatus.EMPTY, listings=[])
    record_fetch_outcome(main_session, mock_company, res)
    
    assert mock_company.consecutive_empty_runs == 1
    assert mock_company.last_job_count == 0


def test_ok_resets_staleness(main_session, mock_company):
    mock_company.consecutive_empty_runs = 2
    main_session.flush()
    
    listing = RawListing(
        external_id="123", url="https://acme.com/1", title="Eng", location_raw="NY",
        description_plain="Desc", description_html=None, posted_at=None, comp_raw=None,
        comp_min=None, comp_max=None, raw={}
    )
    res = FetchResult(status=FetchStatus.OK, listings=[listing, listing])
    record_fetch_outcome(main_session, mock_company, res)
    
    assert mock_company.consecutive_empty_runs == 0
    assert mock_company.last_job_count == 2


def test_error_does_not_increment_staleness(main_session, mock_company):
    mock_company.consecutive_empty_runs = 2
    main_session.flush()
    
    res = FetchResult(status=FetchStatus.ERROR, error_kind=ErrorKind.HTTP_ERROR, message="429")
    record_fetch_outcome(main_session, mock_company, res)
    
    # Still 2, not 3
    assert mock_company.consecutive_empty_runs == 2


def test_is_possibly_stale_on_counter(mock_company):
    mock_company.consecutive_empty_runs = 2
    assert not is_possibly_stale(mock_company, threshold=3)
    
    mock_company.consecutive_empty_runs = 3
    assert is_possibly_stale(mock_company, threshold=3)


def test_is_possibly_stale_on_reverification(mock_company):
    mock_company.consecutive_empty_runs = 0
    mock_company.ats_config = {"resolution": {"status": "needs_reverification"}}
    
    assert is_possibly_stale(mock_company)


def test_staleness_message_differs_by_cause(mock_company):
    mock_company.consecutive_empty_runs = 3
    mock_company.ats_config = {"resolution": {"status": "resolved"}}
    
    msg_empty = staleness_message(mock_company)
    assert "0 listings" in msg_empty
    assert "stopped hiring" in msg_empty
    
    mock_company.consecutive_empty_runs = 0
    mock_company.ats_config = {"resolution": {"status": "needs_reverification"}}
    
    msg_reverify = staleness_message(mock_company)
    assert "404/410" in msg_reverify
    assert "moved or been renamed" in msg_reverify
    
    assert msg_empty != msg_reverify


def test_staleness_message_none_when_fresh(mock_company):
    mock_company.consecutive_empty_runs = 0
    mock_company.ats_config = {"resolution": {"status": "resolved"}}
    assert staleness_message(mock_company) is None


def test_record_fetch_outcome_sets_checked_at(main_session, mock_company):
    assert mock_company.last_checked_at is None
    
    res = FetchResult(status=FetchStatus.ERROR, error_kind=ErrorKind.HTTP_ERROR, message="429")
    record_fetch_outcome(main_session, mock_company, res)
    
    assert mock_company.last_checked_at is not None


def test_three_consecutive_empties(main_session, mock_company):
    res = FetchResult(status=FetchStatus.EMPTY, listings=[])
    record_fetch_outcome(main_session, mock_company, res)
    record_fetch_outcome(main_session, mock_company, res)
    record_fetch_outcome(main_session, mock_company, res)
    
    assert mock_company.consecutive_empty_runs == 3


def test_error_after_empty_preserves_count(main_session, mock_company):
    res_empty = FetchResult(status=FetchStatus.EMPTY, listings=[])
    record_fetch_outcome(main_session, mock_company, res_empty)
    record_fetch_outcome(main_session, mock_company, res_empty)
    
    res_err = FetchResult(status=FetchStatus.ERROR, error_kind=ErrorKind.HTTP_ERROR, message="500")
    record_fetch_outcome(main_session, mock_company, res_err)
    
    assert mock_company.consecutive_empty_runs == 2


def test_unknown_status_no_raise(main_session, mock_company):
    class FakeRes:
        status = "BOGUS"
        
    # Should not raise, just sets last_checked_at
    record_fetch_outcome(main_session, mock_company, FakeRes())
    assert mock_company.last_checked_at is not None
