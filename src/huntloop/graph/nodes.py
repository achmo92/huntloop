"""LangGraph node functions (02-10).

Each node is a plain def function (not async). SQLAlchemy Session objects are not
thread-safe; per-employer nodes must NOT share one session across concurrent
branches. Each employer branch opens its own session from a sessionmaker, and
the final write/aggregation happens in the single-threaded finalize_run node.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timezone
from functools import partial
from typing import TYPE_CHECKING

from langgraph.types import Send
from sqlalchemy.orm import Session

from huntloop.config import load_config
from huntloop.criteria.loader import get_active_criteria
from huntloop.db.models import Company, FilterTier
from huntloop.db.repository import CompanyRepository, RunRepository
from huntloop.discovery.ats.registry import get_adapter, ADAPTERS
from huntloop.discovery.crawl.careers import crawl_careers, load_crawl_hash, save_crawl_hash
from huntloop.discovery.crawl.extract import extract_listings, to_fetch_result
from huntloop.discovery.dedup import compute_dedup_key, DedupKeyError
from huntloop.discovery.write import write_batch, WriteOutcome
from huntloop.registry.staleness import record_fetch_outcome
from huntloop.scoring.pipeline import score_listing, ScoredListing
from huntloop.scoring.filters import apply_deterministic_filters

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


def load_employers(state, *, sessionmaker: sessionmaker) -> dict:
    """Load enabled employers and return their company_ids."""
    session = sessionmaker()
    try:
        repo = CompanyRepository(session)
        companies = repo.list_enabled()
        return {"company_ids": [str(c.id) for c in companies]}
    finally:
        session.close()


def fan_out_to_employers(state) -> list[Send]:
    """Return one Send per enabled employer.
    
    Empty list is valid and routes to finalize_run. LangGraph handles empty Send
    lists correctly (no branches to execute, graph proceeds to next edge).
    """
    return [
        Send("process_employer",
             {"company_id": cid, "run_id": state["run_id"],
              "criteria_version": state["criteria_version"], "no_score": state.get("no_score", False)})
        for cid in state.get("company_ids", [])
    ]


def process_employer(
    state,
    *,
    sessionmaker: sessionmaker,
    llm_client,
    http_client,
    static_fetcher=None,
    rendered_fetcher=None,
    now=None,
) -> dict:
    """Process a single employer.
    
    DISC-03. This except is the requirement. An exception escaping this node
    aborts the whole graph invocation and loses every other employer's work —
    including employers that already completed. Catching here, recording against
    this company_id, and returning a result is what makes a run survivable.
    """
    session = sessionmaker()
    result: EmployerResult = {"company_id": state["company_id"], "failed": 0}
    stage = "load"
    
    try:
        company = session.get(Company, uuid.UUID(state["company_id"]))
        result["company_name"] = company.name
        
        # Determine fetch path
        stage = "fetch"
        fetch_result, path = _fetch_for_employer(
            company, session, http_client, static_fetcher, rendered_fetcher
        )
        result["path"] = path
        
        # Record fetch outcome (REG-05)
        record_fetch_outcome(session, company, fetch_result)
        result["fetched"] = len(fetch_result.listings)
        
        # Dedup within batch
        stage = "dedup"
        listings, dup_dropped = _dedupe_within_batch(company, fetch_result.listings)
        result["after_dedup"] = len(listings)
        result["failed"] += dup_dropped
        
        # Get criteria
        criteria_data = get_active_criteria(session)
        if criteria_data is None:
            # Should not happen - run_discovery checks this first
            raise RuntimeError("No active criteria")
        criteria_version, criteria = criteria_data
        
        # Score pipeline
        stage = "score"
        scored = []
        tokens_in = 0
        tokens_out = 0
        
        for listing in listings:
            if state.get("no_score"):
                # --no-score: deterministic filters only, no model calls
                outcome = apply_deterministic_filters(listing, criteria, now=now)
                if not outcome.passed:
                    scored_list = ScoredListing(
                        listing=listing,
                        scored=False,
                        tier_reached=outcome.tier_reached,
                        drop_reason=outcome.first_drop.detail if outcome.first_drop else "dropped by filters",
                    )
                else:
                    scored_list = ScoredListing(
                        listing=listing,
                        scored=False,
                        tier_reached=FilterTier.DETERMINISTIC,
                    )
            else:
                # Full scoring
                scored_list = score_listing(
                    llm_client, listing, criteria,
                    criteria_version=state["criteria_version"],
                    now=now
                )
                if scored_list.usage:
                    for usage in scored_list.usage:
                        tokens_in += usage.prompt_tokens
                        tokens_out += usage.completion_tokens
            
            scored.append(scored_list)
        
        # Count per-stage
        result["after_deterministic"] = sum(
            1 for s in scored 
            if s.tier_reached == FilterTier.DETERMINISTIC or s.scored
        )
        result["after_triage"] = sum(
            1 for s in scored 
            if s.tier_reached == FilterTier.TRIAGE or s.scored
        )
        result["scored"] = sum(1 for s in scored if s.scored)
        
        result["tokens_in"] = tokens_in
        result["tokens_out"] = tokens_out
        
        # Write
        stage = "write"
        outcome = write_batch(
            session, company, scored,
            run_id=state["run_id"],
            now=now
        )
        result["written"] = outcome.inserted
        result["updated"] = outcome.updated
        result["failed"] += outcome.failed
        
        session.commit()
        
    except Exception as exc:
        session.rollback()
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["stage"] = stage
        _record_run_error(
            sessionmaker, state["run_id"], state["company_id"], stage, result["error"]
        )
    finally:
        session.close()
    
    return {"employer_results": [result]}


def finalize_run(state, *, sessionmaker: sessionmaker) -> dict:
    """Aggregate all employer results and finish the run."""
    session = sessionmaker()
    try:
        # Sum all counters
        results = state.get("employer_results", [])
        
        totals = {
            "companies_checked": len(results),
            "listings_fetched": sum(r.get("fetched", 0) for r in results),
            "after_dedup": sum(r.get("after_dedup", 0) for r in results),
            "after_deterministic": sum(r.get("after_deterministic", 0) for r in results),
            "after_triage": sum(r.get("after_triage", 0) for r in results),
            "scored": sum(r.get("scored", 0) for r in results),
            "new_jobs_written": sum(r.get("written", 0) for r in results),
            "tokens_in": sum(r.get("tokens_in", 0) for r in results),
            "tokens_out": sum(r.get("tokens_out", 0) for r in results),
        }
        
        # Build error summary
        errors = [r for r in results if r.get("error")]
        error_summary = None
        if errors:
            error_summary = "\n".join(
                f"{r.get('company_name', 'Unknown')}: {r.get('stage')} - {r.get('error')}"
                for r in errors
            )
        
        # Determine status
        from huntloop.db.models import RunStatus
        if errors and len(errors) == len(results):
            status = RunStatus.FAILED
        elif errors:
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.SUCCESS
        
        # Finish run
        repo = RunRepository(session)
        repo.finish(
            uuid.UUID(state["run_id"]),
            status=status,
            error_summary=error_summary,
            **totals
        )
        session.commit()
        
    finally:
        session.close()
    
    return {}


def _fetch_for_employer(company, session, http_client, static_fetcher, rendered_fetcher):
    """Fetch listings for an employer via ATS or crawl.
    
    Returns (FetchResult, path) where path is "ats", "crawl", or "skipped".
    """
    from huntloop.discovery.ats.base import FetchResult, FetchStatus
    
    # Check if resolved ATS
    if company.ats and company.ats_identifier:
        adapter = get_adapter(company.ats)
        if adapter:
            fetch_result = adapter.fetch(company.ats_identifier, client=http_client)
            return fetch_result, "ats"
    
    # Check if unresolved with careers_url
    if company.careers_url:
        # Crawl path
        existing_hash = load_crawl_hash(session, company)
        
        pages = crawl_careers(
            company.careers_url,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
        )
        
        if not pages:
            return FetchResult(status=FetchStatus.EMPTY, listings=[]), "crawl"
        
        # Check if changed
        from huntloop.discovery.crawl.careers import content_hash
        new_hash = content_hash(pages)
        
        if existing_hash == new_hash:
            # No change, return empty
            return FetchResult(status=FetchStatus.EMPTY, listings=[]), "crawl"
        
        # Extract listings
        listings = extract_listings(pages, company.careers_url)
        fetch_result = to_fetch_result(listings)
        
        # Save new hash
        save_crawl_hash(session, company, new_hash)
        
        return fetch_result, "crawl"
    
    # No ATS and no careers_url
    return FetchResult(
        status=FetchStatus.EMPTY,
        listings=[],
        error="No ATS and no careers URL"
    ), "skipped"


def _dedupe_within_batch(company, listings):
    """Dedupe within a single batch, counting failures."""
    seen = set()
    deduped = []
    failed = 0
    
    for listing in listings:
        try:
            key = compute_dedup_key(company.name, listing)
            if key not in seen:
                seen.add(key)
                deduped.append(listing)
        except DedupKeyError:
            failed += 1
            logger.warning(f"DedupKeyError for listing: {listing.title}")
    
    return deduped, failed


def _record_run_error(sessionmaker, run_id, company_id, stage, message):
    """Record an error against the run."""
    session = sessionmaker()
    try:
        repo = RunRepository(session)
        repo.record_error(
            uuid.UUID(run_id),
            uuid.UUID(company_id),
            stage,
            message
        )
        session.commit()
    finally:
        session.close()


# Import EmployerResult for type hint
from huntloop.graph.state import EmployerResult
