"""StateGraph assembly and run entrypoint (02-10).

Synchronous. 02-RESEARCH.md's example uses the asynchronous invoke API. 02-01
locked the phase to synchronous I/O because the persistence layer is a
synchronous SQLAlchemy `Session`. The graph runs via the synchronous invoke;
node functions are plain `def`.
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
from langgraph.types import RetryPolicy, Send
from sqlalchemy import select

from huntloop.config import load_config
from huntloop.criteria.loader import get_active_criteria
from huntloop.db.models import Company, Job, RunStatus, RunTrigger
from huntloop.db.repository import RunRepository
from huntloop.discovery.ats.base import make_client
from huntloop.discovery.fetch.page import StaticPageFetcher
from huntloop.graph.state import DiscoveryState
from huntloop.graph.nodes import (
    fan_out_to_employers,
    finalize_run,
    load_employers,
    process_employer,
    run_status,
)

if TYPE_CHECKING:
    from sqlalchemy.orm import sessionmaker

logger = logging.getLogger(__name__)


class NoActiveCriteria(RuntimeError):
    """Raised when no active criteria exist.

    A run with nothing to score against is not a run that failed -- it is a run
    that should not have started, so this raises BEFORE the Run row is created.
    """
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
    top_listings: tuple[dict, ...] = ()   # {"title", "url", "score"} best-first


def _route_from_load(state) -> list[Send] | list[str]:
    """Routing wrapper around fan_out_to_employers.

    langgraph 1.2.11: a conditional edge whose router returns an empty list
    takes NO edge at all, so load_employers would dead-end and finalize_run
    (and the Run row's terminal status) would never execute. When there are no
    enabled employers, route explicitly to finalize_run instead.
    """
    sends = fan_out_to_employers(state)
    return sends if sends else ["finalize_run"]


def build_graph(
    *,
    sessionmaker: sessionmaker,
    llm_client,
    http_client,
    static_fetcher=None,
    rendered_fetcher=None,
    now=None,
):
    """Build and compile the discovery StateGraph.

    Retries the whole employer branch on a transient failure. This is deliberately
    layered on top of the tenacity retry inside fetch_json (02-02): tenacity
    handles a flaky HTTP call, RetryPolicy handles a flaky branch. Keep
    max_attempts small -- three attempts against a genuinely-down ATS is already
    three times the rate-limit exposure.
    """
    builder = StateGraph(DiscoveryState)

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
        retry_policy=RetryPolicy(max_attempts=3),
    )

    builder.add_node("finalize_run", partial(finalize_run, sessionmaker=sessionmaker))

    builder.add_edge(START, "load_employers")
    builder.add_conditional_edges(
        "load_employers", _route_from_load, ["process_employer", "finalize_run"]
    )
    builder.add_edge("process_employer", "finalize_run")
    builder.add_edge("finalize_run", END)

    return builder.compile()


def run_discovery(
    *,
    sessionmaker: sessionmaker,
    llm_client=None,
    http_client=None,
    static_fetcher=None,
    rendered_fetcher=None,
    trigger: RunTrigger = RunTrigger.MANUAL,
    no_score: bool = False,
    now=None,
    concurrency: int | None = None,
) -> RunSummary:
    """Run discovery end to end and return the summary the CLI renders.

    The graph runs via its synchronous invoke method -- the phase is locked to
    synchronous I/O (02-01) and so is every node beneath it.
    """
    from huntloop.credentials.base import get_credentials_engine, make_credentials_session_factory
    from huntloop.llm.client import get_llm_client

    now = now or datetime.now(timezone.utc)

    # 1. Active criteria must exist BEFORE a Run row is created: a run with
    #    nothing to score against should not have started.
    session = sessionmaker()
    try:
        active = get_active_criteria(session)
        if active is None:
            raise NoActiveCriteria(
                "no active criteria: run `huntloop criteria load <file>` first"
            )
        criteria_version, _criteria = active
    finally:
        session.close()

    # 2. Create the Run row and commit so the run id is durable before any work.
    session = sessionmaker()
    try:
        run = RunRepository(session).start(trigger)
        session.commit()
        run_id = run.id
    finally:
        session.close()

    # 3. Build clients if not injected.
    if http_client is None:
        http_client = make_client()
    if static_fetcher is None:
        static_fetcher = StaticPageFetcher(http_client)

    # Only build the LLM client when scoring: --no-score must work with no API
    # key configured at all. (A crawl employer under --no-score records an
    # employer-level error naming the missing client -- DISC-03 isolation.)
    if llm_client is None and not no_score:
        credentials_session = make_credentials_session_factory(get_credentials_engine())()
        try:
            llm_client = get_llm_client(credentials_session)
        finally:
            credentials_session.close()

    # 4-5. Build, invoke, aggregate.
    try:
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

        final = graph.invoke(
            initial_state,
            config={"max_concurrency": concurrency or load_config().max_employer_concurrency},
        )

        results = final.get("employer_results", [])
        errored = [r for r in results if r.get("error")]

        summary = RunSummary(
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
            tokens_in=sum(r.get("tokens_in", 0) for r in results),
            tokens_out=sum(r.get("tokens_out", 0) for r in results),
            cost_usd=None,  # Phase 2 has no pricing model; tokens are the auditable unit
            errors=tuple(
                {
                    "company": r.get("company_name", "Unknown"),
                    "stage": r.get("stage", "unknown"),
                    "message": r.get("error", ""),
                }
                for r in errored
            ),
            status=run_status(results).value,
            top_listings=_load_top_listings(sessionmaker, run_id),
        )

        return summary

    except Exception as exc:
        # 6. A crashed run must not leave a row stuck in 'running' forever.
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
                error_summary=f"run aborted: {type(exc).__name__}: {exc}",
            )
            session.commit()
        finally:
            session.close()
        raise


def _load_top_listings(sessionmaker: sessionmaker, run_id: uuid.UUID, limit: int = 10) -> tuple[dict, ...]:
    """Load this run's best-scored listings, best first, for CLI rendering."""
    session = sessionmaker()
    try:
        rows = session.execute(
            select(Job, Company.name)
            .join(Company, Job.company_id == Company.id)
            .where(Job.source_run_id == run_id, Job.score_overall.is_not(None))
            .order_by(Job.score_overall.desc())
            .limit(limit)
        ).all()
        return tuple(
            {
                "title": job.title,
                "url": job.url,
                "company": company_name,
                "score": str(job.score_overall),
            }
            for job, company_name in rows
        )
    finally:
        session.close()