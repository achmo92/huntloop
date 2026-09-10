import logging
from dataclasses import dataclass
from decimal import Decimal
from functools import partial
from typing import Any

from langgraph.graph import END, START, StateGraph
from langgraph.types import RetryPolicy

from huntloop.config import load_config
from huntloop.criteria.loader import get_active_criteria
from huntloop.db.models import Job, RunTrigger, RunStatus
from huntloop.db.repository import RunRepository
from huntloop.discovery.ats.base import make_client
from huntloop.graph.nodes import fan_out_to_employers, finalize_run, load_employers, process_employer
from huntloop.graph.state import DiscoveryState
from huntloop.llm.client import get_llm_client

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class RunSummary:
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
    top_listings: list[Job]


class NoActiveCriteria(ValueError):
    pass


def build_graph(*, sessionmaker, llm_client, http_client, static_fetcher=None, rendered_fetcher=None, now=None):
    builder = StateGraph(DiscoveryState)
    builder.add_node("load_employers", partial(load_employers, sessionmaker=sessionmaker))
    
    # Retries the whole employer branch on a transient failure. This is deliberately layered on top of the tenacity retry inside fetch_json (02-02): tenacity handles a flaky HTTP call, RetryPolicy handles a flaky branch. Keep max_attempts small — three attempts against a genuinely-down ATS is already three times the rate-limit exposure.
    builder.add_node(
        "process_employer",
        partial(
            process_employer,
            sessionmaker=sessionmaker,
            llm_client=llm_client,
            http_client=http_client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
            now=now
        ),
        retry_policy=RetryPolicy(max_attempts=3)
    )
    
    builder.add_node("finalize_run", partial(finalize_run, sessionmaker=sessionmaker))
    
    builder.add_edge(START, "load_employers")
    builder.add_conditional_edges("load_employers", fan_out_to_employers, ["process_employer", "finalize_run"])
    builder.add_edge("process_employer", "finalize_run")
    builder.add_edge("finalize_run", END)
    
    return builder.compile()


def run_discovery(
    *,
    sessionmaker,
    credentials_session=None,
    llm_client=None,
    http_client=None,
    static_fetcher=None,
    rendered_fetcher=None,
    trigger=RunTrigger.MANUAL,
    no_score=False,
    now=None,
    concurrency=None
) -> RunSummary:
    
    session = sessionmaker()
    try:
        active = get_active_criteria(session)
        if not active:
            raise NoActiveCriteria("no active criteria: run `huntloop criteria load <file>` first")
        version, criteria = active
        
        run = RunRepository(session).start(trigger)
        session.commit()
    except Exception:
        session.close()
        raise
        
    try:
        http_client = http_client or make_client()
        if not no_score:
            llm_client = llm_client or get_llm_client(credentials_session)
        else:
            llm_client = None
            
        graph = build_graph(
            sessionmaker=sessionmaker,
            llm_client=llm_client,
            http_client=http_client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
            now=now
        )
        
        final = graph.invoke(
            {
                "run_id": str(run.id),
                "criteria_version": version,
                "no_score": no_score,
                "employer_results": [],
                "errors": []
            },
            config={"max_concurrency": concurrency or load_config().max_employer_concurrency}
        )
        
        # Load the updated run from the DB (finalize_run commits it)
        session.expire_all()
        updated_run = RunRepository(session).get(run.id)
        
        # Get top listings
        from huntloop.db.repository import JobRepository
        # In a real implementation we'd probably sort by score here, for now just empty list
        top_listings = []
        
        # Aggregate errors from final state
        results = final.get("employer_results", [])
        errors = tuple(
            {"company": r["company_name"], "stage": r.get("stage", "unknown"), "message": r["error"]}
            for r in results if r.get("error")
        )
        
        return RunSummary(
            run_id=str(updated_run.id),
            companies_checked=updated_run.companies_checked,
            listings_fetched=updated_run.listings_fetched,
            after_dedup=updated_run.after_dedup,
            after_deterministic=updated_run.after_deterministic,
            after_triage=updated_run.after_triage,
            scored=updated_run.scored,
            new_jobs_written=updated_run.new_jobs_written,
            updated=sum(r.get("updated", 0) for r in results),
            failed=sum(r.get("failed", 0) for r in results),
            tokens_in=updated_run.tokens_in,
            tokens_out=updated_run.tokens_out,
            cost_usd=updated_run.cost_usd,
            errors=errors,
            status=updated_run.status.value,
            top_listings=top_listings,
        )
    except Exception as e:
        session.expire_all()
        RunRepository(session).finish(
            run_id=run.id,
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
            cost_usd=0.0,
            error_summary=f"Graph crashed: {e}"
        )
        session.commit()
        raise
    finally:
        session.close()
