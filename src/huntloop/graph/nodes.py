"""LangGraph node functions (02-10).

Each node is a plain def function (not async). SQLAlchemy Session objects are not
thread-safe; per-employer nodes must NOT share one session across concurrent
branches. Each employer branch opens its own session from a sessionmaker, and
the final write/aggregation happens in the single-threaded finalize_run node.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass

from langgraph.types import Send
from sqlalchemy.orm import Session, sessionmaker

from huntloop.criteria.loader import get_active_criteria
from huntloop.db.models import Company, FilterTier, RunStatus
from huntloop.db.repository import CompanyRepository, RunRepository
from huntloop.discovery.ats.base import FetchResult, FetchStatus
from huntloop.discovery.ats.registry import get_adapter
from huntloop.discovery.crawl.careers import crawl_careers, load_crawl_hash, save_crawl_hash
from huntloop.discovery.crawl.extract import extract_listings, to_fetch_result
from huntloop.discovery.dedup import compute_dedup_key, DedupKeyError
from huntloop.discovery.write import write_batch
from huntloop.graph.state import EmployerResult
from huntloop.registry.staleness import record_fetch_outcome
from huntloop.scoring.filters import apply_deterministic_filters
from huntloop.scoring.pipeline import score_listing, ScoredListing

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class _ScoreBatchOut:
    """Companion numbers _score_batch produces alongside the scored listings."""
    listings: list
    tokens_in: int = 0
    tokens_out: int = 0
    after_deterministic: int = 0
    after_triage: int = 0
    scored: int = 0


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
    """Return one Send per enabled employer; an empty list when there are none.

    NOTE: a conditional edge returning an empty list does NOT advance the graph
    (verified against langgraph 1.2.11) -- build.py's routing wrapper explicitly
    routes to finalize_run in that case so a zero-employer run still finishes its
    Run row with a terminal status.
    """
    return [
        Send(
            "process_employer",
            {"company_id": cid, "run_id": state["run_id"],
             "criteria_version": state["criteria_version"], "no_score": state.get("no_score", False)},
        )
        for cid in state.get("company_ids", [])
    ]


def run_status(results: list[EmployerResult]) -> RunStatus:
    """Derive the run's terminal status from its per-employer results.

    All employers errored -> FAILED. Some -> PARTIAL. None -> SUCCESS.
    A run with zero employers is a clean SUCCESS, not an error (DISC-03 truth:
    'a run with zero enabled employers finishes cleanly rather than erroring').
    """
    errors = [r for r in results if r.get("error")]
    if results and len(errors) == len(results):
        return RunStatus.FAILED
    if errors:
        return RunStatus.PARTIAL
    return RunStatus.SUCCESS


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
    """Process a single employer end to end: fetch -> dedup -> score -> write.

    DISC-03. This except is the requirement. An exception escaping this node
    aborts the whole graph invocation and loses every other employer's work --
    including employers that already completed. Catching here, recording against
    this company_id, and returning a result is what makes a run survivable.
    """
    session: Session = sessionmaker()
    result: EmployerResult = {
        "company_id": state["company_id"], "failed": 0,
        "error": None, "stage": None,
    }
    stage = "load"
    try:
        company = session.get(Company, uuid.UUID(state["company_id"]))
        if company is None:
            raise LookupError(f"company {state['company_id']} not found")
        result["company_name"] = company.name

        stage = "fetch"
        fetch_result, path = _fetch_for_employer(
            company, session, llm_client=llm_client, http_client=http_client,
            static_fetcher=static_fetcher, rendered_fetcher=rendered_fetcher,
        )
        result["path"] = path

        # REG-05: the real FetchResult on every path -- the crawl path feeds it
        # through to_fetch_result so the empty-vs-error distinction is identical.
        record_fetch_outcome(session, company, fetch_result)
        result["fetched"] = len(fetch_result.listings)

        stage = "dedup"
        listings, dup_failed = _dedupe_within_batch(company, fetch_result.listings)
        result["after_dedup"] = len(listings)
        result["failed"] += dup_failed

        criteria_data = get_active_criteria(session)
        if criteria_data is None:
            # run_discovery checks this before starting; reaching here means the
            # active criteria row vanished mid-run. Fail this employer, not the run.
            raise RuntimeError("no active criteria")
        _, criteria = criteria_data

        stage = "score"
        scored_batch = _score_batch(
            listings, criteria, state, llm_client=llm_client, now=now,
        )
        result["after_deterministic"] = scored_batch.after_deterministic
        result["after_triage"] = scored_batch.after_triage
        result["scored"] = scored_batch.scored
        result["tokens_in"] = scored_batch.tokens_in
        result["tokens_out"] = scored_batch.tokens_out

        stage = "write"
        outcome = write_batch(
            session, company, scored_batch.listings,
            run_id=uuid.UUID(state["run_id"]), now=now,
        )
        result["written"] = outcome.inserted
        result["updated"] = outcome.updated
        result["failed"] += outcome.failed

        session.commit()
    except Exception as exc:
        # DISC-03: catch, attribute to THIS employer, keep going. See docstring.
        session.rollback()
        result["error"] = f"{type(exc).__name__}: {exc}"
        result["stage"] = stage
        _record_run_error(sessionmaker, state["run_id"], state["company_id"], stage, result["error"])
    finally:
        session.close()

    return {"employer_results": [result]}


def finalize_run(state, *, sessionmaker: sessionmaker) -> dict:
    """Aggregate all employer results, finish the Run row with terminal status."""
    session: Session = sessionmaker()
    try:
        results = state.get("employer_results", [])

        # Sum every counter across employer_results. Missing keys default to 0:
        # an employer that failed early legitimately produced a partial result.
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

        errored = [r for r in results if r.get("error")]
        error_summary = None
        if errored:
            error_summary = "\n".join(
                f"{r.get('company_name', 'Unknown')}@{r.get('stage')}: {r.get('error')}"
                for r in errored
            )

        repo = RunRepository(session)
        repo.finish(
            uuid.UUID(state["run_id"]),
            status=run_status(results),
            cost_usd=0,  # Phase 2 has no pricing model; tokens are the auditable unit
            error_summary=error_summary,
            **totals,
        )
        session.commit()
    finally:
        session.close()

    return {}


def _fetch_for_employer(
    company, session, *, llm_client, http_client, static_fetcher, rendered_fetcher,
):
    """Fetch listings for an employer via its ATS adapter, else the crawl fallback.

    Returns (FetchResult, path) where path is "ats" | "crawl" | "skipped".
    """
    # Resolved ATS employer: the cheap, structured, primary path.
    if company.ats and company.ats_identifier:
        adapter = get_adapter(company.ats)
        fetch_result = adapter.fetch(company.ats_identifier, client=http_client)
        return fetch_result, "ats"

    # Unresolved employer with a careers page: the DISC-05 fallback crawl.
    if company.careers_url:
        if static_fetcher is None:
            raise RuntimeError("crawl path requires a page fetcher")
        crawl_result = crawl_careers(
            static_fetcher, company.careers_url,
            previous_hash=load_crawl_hash(company),
            rendered_fetcher=rendered_fetcher,
        )
        if crawl_result.skipped:
            # Unchanged page since last run: genuinely nothing new.
            return to_fetch_result([], crawl_result), "crawl"

        if llm_client is None:
            # Extraction is an LLM call (DISC-05). Without a client there is no
            # honest way to produce listings; fail this employer at this stage.
            raise RuntimeError("crawl path requires an LLM client for extraction")

        listings = extract_listings(llm_client, crawl_result)
        fetch_result = to_fetch_result(listings, crawl_result)
        if crawl_result.page_hash:
            save_crawl_hash(session, company, crawl_result.page_hash)
        return fetch_result, "crawl"

    # No ATS and no careers page: nothing to fetch, nothing to fail.
    return FetchResult(
        status=FetchStatus.EMPTY,
        listings=[],
        message="no ATS identifier and no careers_url",
    ), "skipped"


def _dedupe_within_batch(company, listings):
    """Remove within-batch duplicates, keeping the first of each dedup key.

    Cross-run dedup is write_batch's job (unique index on jobs.dedup_key); this
    only removes duplicates a single fetch produced. A DedupKeyError on one
    listing is counted in `failed` and must not fail the employer (DISC-03 at
    the listing level).
    """
    seen: set[str] = set()
    deduped = []
    failed = 0
    for listing in listings:
        try:
            key = compute_dedup_key(company.name, listing)
        except DedupKeyError:
            failed += 1
            continue
        if key not in seen:
            seen.add(key)
            deduped.append(listing)
    return deduped, failed


def _score_batch(listings, criteria, state, *, llm_client, now=None) -> _ScoreBatchOut:
    """Run every listing through the scoring pipeline (or filters-only mode).

    The per-stage survivor counts are computed here because their meaning
    differs between modes: in --no-score mode the pipeline stops after the
    deterministic filters, so survival past deterministic is outcome.passed
    (tier_reached stays DETERMINISTIC by definition -- the listing never
    reached a further tier), while in scoring mode a DETERMINISTIC tier means
    the filters dropped it.
    """
    scored: list[ScoredListing] = []
    tokens_in = 0
    tokens_out = 0
    after_deterministic = 0
    after_triage = 0
    scored_count = 0

    for listing in listings:
        if state.get("no_score"):
            s = _filters_only(listing, criteria, now=now)
            if not s.drop_reason:
                # passed the deterministic filters (no drop_reason == not dropped)
                after_deterministic += 1
        else:
            s = score_listing(
                llm_client, listing, criteria,
                criteria_version=state["criteria_version"], now=now,
            )
            for usage in s.usage:
                tokens_in += usage.prompt_tokens
                tokens_out += usage.completion_tokens
            if s.tier_reached is not FilterTier.DETERMINISTIC:
                after_deterministic += 1
            if s.tier_reached is FilterTier.FULL or (s.tier_reached is FilterTier.TRIAGE and s.error):
                # FULL: survived triage and scored. TRIAGE+error: survived triage
                # but the scoring call itself failed (pipeline.py fail-visible path).
                after_triage += 1
            if s.scored:
                scored_count += 1
        scored.append(s)

    return _ScoreBatchOut(
        listings=scored,
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        after_deterministic=after_deterministic,
        after_triage=after_triage,
        scored=scored_count,
    )


def _filters_only(listing, criteria, *, now=None) -> ScoredListing:
    """The --no-score path: deterministic filters only, never the LLM client.

    02-CONTEXT.md: fetch -> dedup -> deterministic filter, stopping before any
    model call. A listing that passes carries no score (scored=False) and its
    tier_reached stays DETERMINISTIC -- that tier is factually where it stopped.
    """
    outcome = apply_deterministic_filters(listing, criteria, now=now)
    return ScoredListing(
        listing=listing,
        scored=False,
        tier_reached=outcome.tier_reached,
        drop_reason=outcome.first_drop.detail if not outcome.passed and outcome.first_drop else "",
    )


def _record_run_error(sessionmaker, run_id, company_id, stage, message) -> None:
    """Record the error against THIS employer's company_id, not the run generally.

    Opens its own session: the caller's session is rolled back at this point.
    """
    session = sessionmaker()
    try:
        repo = RunRepository(session)
        repo.record_error(uuid.UUID(run_id), uuid.UUID(company_id), stage, message)
        session.commit()
    finally:
        session.close()