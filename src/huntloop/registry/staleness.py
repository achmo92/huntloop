"""REG-05 consecutive-empty gating, driven only by confirmed-empty results.

REG-05 must only ever count a CONFIRMED empty response. An employer that was
rate-limited twice is not "possibly stale" — it is "possibly unreachable".
Those need opposite user actions: one says check whether this employer has
moved on, the other says check your network or the ATS's health.
"""

from __future__ import annotations

from datetime import datetime, timezone

from huntloop.config import load_config
from huntloop.db.repository import CompanyRepository
from huntloop.discovery.ats.base import FetchResult, FetchStatus

DEFAULT_STALE_AFTER_EMPTY_RUNS = 3    # Three consecutive genuinely-empty runs. Two is noisy for a
                                      # small employer between hiring waves; three is a signal.


def record_fetch_outcome(session, company, result: FetchResult, *, now=None) -> None:
    """Record the outcome of a discovery fetch for an employer.
    
    Only EMPTY increments the counter. Only OK resets it.
    """
    repo = CompanyRepository(session)
    now = now or datetime.now(timezone.utc)
    
    if result.status is FetchStatus.EMPTY:
        repo.mark_empty_run(company.id)
    elif result.status is FetchStatus.OK:
        repo.mark_listings_found(company.id, len(result.listings))
    # FetchStatus.ERROR: deliberately NEITHER branch.
    # REG-05 must only ever count a CONFIRMED empty response. An employer that was 
    # rate-limited twice is not 'possibly stale' — it is 'possibly unreachable'. 
    # Those need opposite user actions: one says check whether this employer has 
    # moved on, the other says check your network or the ATS's health. Incrementing 
    # on error is the single most consequential looks-done-but-isn't bug available 
    # in this phase, because the counter still moves, the flag still appears, and 
    # the message is simply wrong. 02-RESEARCH.md, DISC-04/REG-05 Interaction.
    
    company.last_checked_at = now
    session.flush()


def is_possibly_stale(company, *, threshold=None) -> bool:
    """Return True if the company's resolution is broken or it has been empty for a while."""
    res_status = (company.ats_config or {}).get("resolution", {}).get("status")
    if res_status == "needs_reverification":
        return True
        
    threshold = threshold or load_config().stale_after_empty_runs
    return company.consecutive_empty_runs >= threshold


def staleness_message(company) -> str | None:
    """Return a worded message explaining why the employer is flagged as stale."""
    res_status = (company.ats_config or {}).get("resolution", {}).get("status")
    if res_status == "needs_reverification":
        return "Employer board returned 404/410. The careers page may have moved or been renamed."
        
    threshold = load_config().stale_after_empty_runs
    if company.consecutive_empty_runs >= threshold:
        return f"Employer returned 0 listings for {company.consecutive_empty_runs} consecutive runs. They may have stopped hiring."
        
    return None
