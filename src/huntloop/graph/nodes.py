from __future__ import annotations

import logging
from typing import Any

from langgraph.types import Send

from huntloop.criteria.loader import get_active_criteria
from huntloop.db.models import Company, FilterTier, RunStatus, RunTrigger
from huntloop.db.repository import CompanyRepository, RunRepository
from huntloop.discovery.ats.base import ErrorKind, FetchResult, FetchStatus
from huntloop.discovery.ats.registry import get_adapter
from huntloop.discovery.crawl.careers import crawl_careers
from huntloop.discovery.crawl.extract import extract_listings, to_fetch_result
from huntloop.discovery.dedup import compute_dedup_key, DedupKeyError
from huntloop.discovery.write import write_batch
from huntloop.graph.state import DiscoveryState, EmployerResult
from huntloop.registry.resolve import ResolutionStatus
from huntloop.registry.staleness import record_fetch_outcome
from huntloop.scoring.pipeline import ScoredListing, score_listing, apply_deterministic_filters

logger = logging.getLogger(__name__)


def load_employers(state: DiscoveryState, *, sessionmaker) -> dict[str, Any]:
    session = sessionmaker()
    try:
        companies = CompanyRepository(session).list_enabled()
        return {"company_ids": [str(c.id) for c in companies]}
    finally:
        session.close()


def fan_out_to_employers(state: DiscoveryState) -> list[Send] | str:
    company_ids = state.get("company_ids", [])
    if not company_ids:
        return "finalize_run"
        
    return [
        Send(
            "process_employer",
            {
                "company_id": cid,
                "run_id": state["run_id"],
                "criteria_version": state["criteria_version"],
                "no_score": state.get("no_score", False),
            },
        )
        for cid in company_ids
    ]


def _filters_only(listing, criteria, now) -> ScoredListing:
    # Build ScoredListing-shaped result using only apply_deterministic_filters
    from huntloop.scoring.pipeline import ScoredListing
    
    outcome = apply_deterministic_filters(listing, criteria, now=now)
    tier = outcome.tier_reached
    drop = outcome.first_drop
    reason = f"{drop.rule}: {drop.detail}" if drop else None
    return ScoredListing(
        listing=listing,
        scored=False,
        tier_reached=tier,
        overall=None,
        dimensions=None,
        flags=None,
        summary="",
        criteria_version=None,
        rubric_version=None,
        model=None,
        error="",
        drop_reason=reason or "",
        note="",
        usage=()
    )


def _dedupe_within_batch(company, listings) -> tuple[list, int]:
    # drop within-batch duplicates keeping the first, count DedupKeyError as failed
    seen = set()
    result = []
    failed = 0
    for l in listings:
        try:
            k = compute_dedup_key(company.name, l)
            if k not in seen:
                seen.add(k)
                result.append(l)
        except DedupKeyError:
            failed += 1
    return result, failed


def process_employer(
    state: dict[str, Any],
    *,
    sessionmaker,
    llm_client,
    http_client,
    static_fetcher=None,
    rendered_fetcher=None,
    now=None
) -> dict[str, Any]:
    session = sessionmaker()
    result: EmployerResult = {"company_id": state["company_id"], "failed": 0}
    stage = "load"
    try:
        import uuid
        company_id = uuid.UUID(state["company_id"])
        run_id = uuid.UUID(state["run_id"])
        
        company = session.get(Company, company_id)
        result["company_name"] = company.name
        
        # Load criteria
        active = get_active_criteria(session)
        if not active:
            raise ValueError("No active criteria found")
        _, criteria = active
        
        stage = "fetch"
        ats_config = company.ats_config or {}
        res_status = ats_config.get("resolution", {}).get("status")
        
        fetch_result = None
        path = "skipped"
        
        if res_status == ResolutionStatus.RESOLVED.value:
            path = "ats"
            adapter = get_adapter(company.ats)
            fetch_result = adapter.fetch_jobs(company, http_client)
        elif company.careers_url:
            path = "crawl"
            crawl_res = crawl_careers(
                company.careers_url, 
                static_fetcher=static_fetcher, 
                rendered_fetcher=rendered_fetcher
            )
            if crawl_res.hash_hex:
                ext_res = extract_listings(llm_client, company.name, crawl_res.markdown, now=now)
                fetch_result = to_fetch_result(ext_res)
            else:
                fetch_result = FetchResult(status=FetchStatus.ERROR, error_kind=ErrorKind.UNKNOWN, message="Empty crawl", listings=[])
        else:
            result["path"] = "skipped"
            result["fetched"] = 0
            result["after_dedup"] = 0
            result["after_deterministic"] = 0
            result["after_triage"] = 0
            result["scored"] = 0
            result["written"] = 0
            result["updated"] = 0
            result["tokens_in"] = 0
            result["tokens_out"] = 0
            return {"employer_results": [result]}
            
        result["path"] = path
        record_fetch_outcome(session, company, fetch_result, now=now)
        result["fetched"] = len(fetch_result.listings)
        
        stage = "dedup"
        listings, dup_dropped = _dedupe_within_batch(company, fetch_result.listings)
        result["after_dedup"] = len(listings)
        result["failed"] += dup_dropped
        
        stage = "score"
        scored = []
        for l in listings:
            if not state.get("no_score"):
                s = score_listing(llm_client, l, criteria, criteria_version=state["criteria_version"], now=now)
            else:
                s = _filters_only(l, criteria, now=now)
            scored.append(s)
            
        result["after_deterministic"] = sum(1 for s in scored if s.tier_reached != FilterTier.DETERMINISTIC or s.scored)
        result["after_triage"] = sum(1 for s in scored if s.scored or s.tier_reached == FilterTier.TRIAGE or s.tier_reached == FilterTier.FULL)
        result["scored"] = sum(1 for s in scored if s.scored)
        
        tokens_in = sum(u.prompt_tokens for s in scored for u in s.usage)
        tokens_out = sum(u.completion_tokens for s in scored for u in s.usage)
        result["tokens_in"] = tokens_in
        result["tokens_out"] = tokens_out
        
        stage = "write"
        outcome = write_batch(session, company, scored, run_id=run_id, now=now)
        result["written"] = outcome.inserted
        result["updated"] = outcome.updated
        result["failed"] += outcome.failed
        session.commit()
    except Exception as exc:
        # DISC-03. This except is the requirement. An exception escaping this node aborts the whole graph invocation and loses every other employer's work — including employers that already completed. Catching here, recording against this company_id, and returning a result is what makes a run survivable.
        session.rollback()
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["stage"] = stage
        RunRepository(session).record_error(run_id, company_id, stage, result["error"])
        session.commit()
    finally:
        session.close()
    return {"employer_results": [result]}


def finalize_run(state: DiscoveryState, *, sessionmaker) -> dict[str, Any]:
    session = sessionmaker()
    try:
        import uuid
        run_id = uuid.UUID(state["run_id"])
        results = state.get("employer_results", [])
        
        companies_checked = len(results)
        listings_fetched = sum(r.get("fetched", 0) for r in results)
        after_dedup = sum(r.get("after_dedup", 0) for r in results)
        after_deterministic = sum(r.get("after_deterministic", 0) for r in results)
        after_triage = sum(r.get("after_triage", 0) for r in results)
        scored = sum(r.get("scored", 0) for r in results)
        new_jobs_written = sum(r.get("written", 0) for r in results)
        tokens_in = sum(r.get("tokens_in", 0) for r in results)
        tokens_out = sum(r.get("tokens_out", 0) for r in results)
        
        errors = [f"{r['company_name']}: {r['error']}" for r in results if r.get("error")]
        error_summary = "; ".join(errors) if errors else None
        
        status = RunStatus.SUCCESS
        if errors:
            if len(errors) == companies_checked and companies_checked > 0:
                status = RunStatus.FAILED
            else:
                status = RunStatus.PARTIAL
                
        # We don't have cost models per token yet
        cost_usd = 0.0
        
        RunRepository(session).finish(
            run_id=run_id,
            status=status,
            companies_checked=companies_checked,
            listings_fetched=listings_fetched,
            after_dedup=after_dedup,
            after_deterministic=after_deterministic,
            after_triage=after_triage,
            scored=scored,
            new_jobs_written=new_jobs_written,
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            error_summary=error_summary,
        )
        session.commit()
        return {}
    finally:
        session.close()
