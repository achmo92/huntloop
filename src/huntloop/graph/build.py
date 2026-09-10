"""StateGraph assembly and run entrypoint (02-10).

Synchronous. 02-RESEARCH.md's example uses `await graph.ainvoke(...)`. 02-01
locked the phase to synchronous I/O because the persistence layer is a
synchronous SQLAlchemy `Session`. Use `graph.invoke(...)`. Node functions
are plain `def`.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from functools import partial
from typing import TYPE_CHECKING

from langgraph.graph import StateGraph, START, END
from langgraph.types import RetryPolicy

from huntloop.config import load_config
from huntloop.criteria.loader import get_active_criteria
from huntloop.db.models import RunStatus, RunTrigger
from huntloop.db.repository import RunRepository
from huntloop.discovery.ats.base import make_client
from huntloop.graph.state import DiscoveryState
from huntloop.graph.nodes import (
    load_employers,
    fan_out_to_employers,
    process_employer,
    finalize_run,
)
from huntloop.llm.client import get_llm_client

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


class NoActiveCriteria(RuntimeError):
    """Raised when no active criteria exist."""
    pass


@dataclass(frozen=True)
class RunSummary:
    """Summary of a completed discovery run.
    
    These are the exact fields 02-11 renders.
    """
    run_id: str
    companies_checked: int
    listings_fetched: int
    after_dedup: int
    after_deterministic: int
    after_triage: int
    scored: int
    new_jobs_written: int
    updated: int
    failed: int
    tokens_in: int
    tokens_out: int
    cost_usd: Decimal | None
    errors: tuple[dict, ...]          # {"company": name, "stage": s, "message": m}
    status: str


def build_graph(
    *,
    sessionmaker: sessionmaker,
    llm_client,
    http_client,
    static_fetcher,
    rendered_fetcher,
    now=None,
):
    """Build and compile the discovery StateGraph.
    
    Retries the whole employer branch on a transient failure. This is deliberately
    layered on top of the tenacity retry inside fetch_json (02-02): tenacity
    handles a flaky HTTP call, RetryPolicy handles a flaky branch. Keep
    max_attempts small — three attempts against a genuinely-down ATS is already
    three times the rate-limit exposure.
    """
    builder = StateGraph(DiscoveryState)
    
    # Nodes
    builder.add_node("load_employers", partial(load_employers, sessionmaker=sessionmaker))
    
    builder.add_node(
        "process_employer",
        partial(
            process_employer,
            sessionmaker=sessionmaker,
            llm_client=llm_client,
            http_client=http_client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
            now=now,
        ),
        retry_policy=RetryPolicy(max_attempts=3)
    )
    
    builder.add_node("finalize_run", partial(finalize_run, sessionmaker=sessionmaker))
    
    # Edges
    builder.add_edge(START, "load_employers")
    builder.add_conditional_edges("load_employers", fan_out_to_employers, ["process_employer"])
    builder.add_edge("process_employer", "finalize_run")
    builder.add_edge("finalize_run", END)
    
    return builder.compile()


def run_discovery(
    *,
    sessionmaker: sessionmaker,
    llm_client=None,
    http_client=None,
    trigger: RunTrigger = RunTrigger.MANUAL,
    no_score: bool = False,
    now=None,
    concurrency=None,
) -> RunSummary:
    """Run discovery end-to-end.
    
    Synchronous invoke, not async. The graph is invoked with graph.invoke(),
    not graph.ainvoke().
    """
    from huntloop.credentials.store import CredentialStore
    
    now = now or datetime.now(timezone.utc)
    
    # 1. Check active criteria BEFORE creating a Run
    session = sessionmaker()
    try:
        active = get_active_criteria(session)
        if active is None:
            raise NoActiveCriteria(
                "no active criteria: run `huntloop criteria load <file>` first"
            )
        criteria_version, criteria = active
    finally:
        session.close()
    
    # 2. Create Run row
    session = sessionmaker()
    try:
        run = RunRepository(session).start(trigger)
        session.commit()
        run_id = run.id
    finally:
        session.close()
    
    # 3. Build clients if not injected
    if http_client is None:
        http_client = make_client()
    
    # Only build LLM client if scoring
    if llm_client is None and not no_score:
        cred_store = CredentialStore()
        llm_client = get_llm_client(cred_store)
    
    # Build fetchers (will be injected by caller in real use)
    static_fetcher = None
    rendered_fetcher = None
    
    # 4. Build and invoke graph
    try:
        config = load_config()
        
        graph = build_graph(
            sessionmaker=sessionmaker,
            llm_client=llm_client,
            http_client=http_client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
            now=now,
        )
        
        initial_state: DiscoveryState = {
            "run_id": str(run_id),
            "criteria_version": criteria_version,
            "no_score": no_score,
            "employer_results": [],
            "errors": [],
        }
        
        max_concurrency = concurrency or config.max_employer_concurrency
        
        final = graph.invoke(
            initial_state,
            config={"max_concurrency": max_concurrency}
        )
        
        # 5. Aggregate and finish
        results = final.get("employer_results", [])
        
        # Calculate cost (simplified - would need actual pricing)
        tokens_in = sum(r.get("tokens_in", 0) for r in results)
        tokens_out = sum(r.get("tokens_out", 0) for r in results)
        cost_usd = None  # Would calculate from token counts + pricing
        
        # Determine status
        errors = [r for r in results if r.get("error")]
        if errors and len(errors) == len(results):
            status = RunStatus.FAILED
        elif errors:
            status = RunStatus.PARTIAL
        else:
            status = RunStatus.SUCCESS
        
        # Format errors for summary
        error_tuples = tuple(
            {
                "company": r.get("company_name", "Unknown"),
                "stage": r.get("stage", "unknown"),
                "message": r.get("error", ""),
            }
            for r in errors
        )
        
        return RunSummary(
            run_id=str(run_id),
            companies_checked=len(results),
            listings_fetched=sum(r.get("fetched", 0) for r in results),
            after_dedup=sum(r.get("after_dedup", 0) for r in results),
            after_deterministic=sum(r.get("after_deterministic", 0) for r in results),
            after_triage=sum(r.get("after_triage", 0) for r in results),
            scored=sum(r.get("scored", 0) for r in results),
            new_jobs_written=sum(r.get("written", 0) for r in results),
            updated=sum(r.get("updated", 0) for r in results),
            failed=sum(r.get("failed", 0) for r in results),
            tokens_in=tokens_in,
            tokens_out=tokens_out,
            cost_usd=cost_usd,
            errors=error_tuples,
            status=status.value,
        )
        
    except Exception as exc:
        # Ensure run is finished even on error
        session = sessionmaker()
        try:
            RunRepository(session).finish(
                run_id,
                status=RunStatus.FAILED,
                companies_checked=0,
                listings_fetched=0,
                after_dedup=0,
                after_deterministic=0,
                after_triage=0,
                scored=0,
                new_jobs_written=0,
                tokens_in=0,
                tokens_out=0,
                cost_usd=0,
                error_summary=f"Run aborted: {exc}",
            )
            session.commit()
        finally:
            session.close()
        raise
