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

from huntloop.config import load_effective_config
from huntloop.criteria.loader import get_active_criteria
from huntloop.db.models import Company, Job, RunStatus, RunTrigger
from huntloop.db.repository import RunRepository
from huntloop.db.run_liveness import (
    clear_run_lease,
    record_run_heartbeat,
    start_run_heartbeat,
)
from huntloop.discovery.ats.base import make_client
from huntloop.discovery.fetch.page import RenderedPageFetcher, StaticPageFetcher
from huntloop.graph.cancellation import RunStoppedByUser, clear_stop_request
from huntloop.graph.state import DiscoveryState
from huntloop.graph.nodes import (
    fan_out_to_employers,
    finalize_run,
    load_employers,
    process_employer,
    run_status,
)
from huntloop.loop.generate import generate_proposals
from huntloop.scoring.spend_cap import SpendTracker

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


def _retry_unless_stopped(exc: Exception) -> bool:
    """GAP-4: never retry a user stop.

    ``process_employer`` carries a RetryPolicy for transient branch failures.
    A ``RunStoppedByUser`` is not transient — retrying it would delay the stop
    by the retry budget and re-enter an employer the user asked to stop.
    """
    return not isinstance(exc, RunStoppedByUser)


def build_graph(
    *,
    sessionmaker: sessionmaker,
    llm_client,
    http_client,
    static_fetcher=None,
    rendered_fetcher=None,
    now=None,
    spend_tracker=None,
    triage_model=None,
    scoring_model=None,
    extraction_model=None,
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
            spend_tracker=spend_tracker,
            triage_model=triage_model,
            scoring_model=scoring_model,
            extraction_model=extraction_model,
        ),
        retry_policy=RetryPolicy(max_attempts=3, retry_on=_retry_unless_stopped),
    )

    builder.add_node(
        "finalize_run",
        partial(finalize_run, sessionmaker=sessionmaker, spend_tracker=spend_tracker),
    )

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

    # D-15: the run resolves config through the overlay so a Setting row written
    # in the UI (spend cap, per-stage models) applies to the very next run — no
    # restart, no file edit. run_discovery's signature is unchanged; the overlay
    # is internal, using the sessionmaker it already holds.
    _cfg_session = sessionmaker()
    try:
        cfg = load_effective_config(_cfg_session)
    finally:
        _cfg_session.close()
    # Always construct a tracker, even with no cap: it is the run's cost ledger
    # first and its brake second. cap_usd=None means "measure, never stop".
    spend_tracker = SpendTracker(cap_usd=cfg.run_spend_cap_usd)

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
    #    GAP-15: the lease is written with the Run row so a live run is never
    #    briefly unclassifiable, for every caller (CLI/manual/scheduler).
    session = sessionmaker()
    try:
        run = RunRepository(session).start(trigger)
        record_run_heartbeat(session, run.id)
        session.commit()
        run_id = run.id
    finally:
        session.close()

    # 3. Build clients if not injected.
    if http_client is None:
        http_client = make_client()
    if static_fetcher is None:
        static_fetcher = StaticPageFetcher(http_client)
    # The crawl path (DISC-05) must be able to render SPA-style careers pages.
    # Constructing the fetcher is always safe (Playwright imports lazily inside
    # fetch()); where Playwright is absent the crawl's render step catches
    # RendererUnavailable and falls back to static. Found live at the 02-12
    # checkpoint: without this, the pipeline's crawl could NEVER render.
    if rendered_fetcher is None:
        rendered_fetcher = RenderedPageFetcher()

    # Only build the LLM client when scoring: --no-score must work with no API
    # key configured at all. (A crawl employer under --no-score records an
    # employer-level error naming the missing client -- DISC-03 isolation.)
    if llm_client is None and not no_score:
        credentials_session = make_credentials_session_factory(get_credentials_engine())()
        try:
            llm_client = get_llm_client(credentials_session)
        finally:
            credentials_session.close()

    # GAP-15: refresh the run's lease on a serial daemon thread for the life of
    # the run. One writer avoids cross-branch SQLite contention in the employer
    # fan-out; the finally below always stops it.
    heartbeat = start_run_heartbeat(sessionmaker, run_id)

    # 4-5. Build, invoke, aggregate.
    try:
        graph = build_graph(
            sessionmaker=sessionmaker,
            llm_client=llm_client,
            http_client=http_client,
            static_fetcher=static_fetcher,
            rendered_fetcher=rendered_fetcher,
            now=now,
            spend_tracker=spend_tracker,
            triage_model=cfg.triage_model,
            scoring_model=cfg.scoring_model,
            extraction_model=cfg.extraction_model,
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
            config={"max_concurrency": concurrency or cfg.max_employer_concurrency},
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
            tokens_in=spend_tracker.tokens_in,
            tokens_out=spend_tracker.tokens_out,
            cost_usd=spend_tracker.spent_usd,
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

        # D-06: proposal generation is the last, cheapest step of a run. It reads
        # only already-stored rows and must never be able to fail a successful
        # discovery run. Its own session, mirroring how finalize_run does its
        # terminal write — never the graph's (those are already closed). The
        # except below is deliberately broad: a discovered-and-scored run must
        # still return its summary even if the loop module raises, and
        # logger.exception keeps the traceback rather than swallowing it.
        gen_session = sessionmaker()
        try:
            created = generate_proposals(gen_session, run_id=run_id, llm_client=llm_client, now=now)
        except Exception:
            logger.exception(
                "run %s: proposal generation failed; run result is unaffected", run_id
            )
        else:
            if created:
                logger.info("run %s generated %d criteria proposal(s)", run_id, len(created))
        finally:
            gen_session.close()

        # GAP-15/GAP-16: the run is terminal, so its lease is no longer needed,
        # and any lingering stop request for it must not affect a later run.
        # Clear both through a fresh session (the graph's sessions are closed).
        _clear_run_artifacts(sessionmaker, run_id)
        return summary

    except RunStoppedByUser:
        # 6. GAP-4: the user stopped this run. Finalize honestly as STOPPED with
        # the real SpendTracker ledger (the crashed-run discipline: money spent
        # is money spent) but zero pipeline counters — the graph was aborted at
        # a boundary, so no final aggregate exists. Clearing the marker keeps a
        # later run from inheriting the stop and the next scheduled fire from
        # being blocked (the overlap guard keys on the RUNNING Run row, now
        # terminal).
        session = sessionmaker()
        try:
            # GAP-16: never overwrite a row the durable grace sweep already
            # finalized STOPPED. finish_if_running is a no-op in that race, so
            # the sweep's GRACE_EXPIRED_STOP_REASON survives untouched.
            RunRepository(session).finish_if_running(
                run_id,
                status=RunStatus.STOPPED,
                companies_checked=0,
                listings_fetched=0,
                after_dedup=0,
                after_deterministic=0,
                after_triage=0,
                scored=0,
                new_jobs_written=0,
                tokens_in=spend_tracker.tokens_in,
                tokens_out=spend_tracker.tokens_out,
                cost_usd=spend_tracker.spent_usd,
                error_summary="stopped by user request",
            )
            clear_stop_request(session, run_id)
            clear_run_lease(session, run_id)
            session.commit()
        finally:
            session.close()
        # A clean return: the API daemon thread and the scheduler both tolerate
        # it, and returning keeps the scheduler from logging a user stop as a
        # failure.
        return RunSummary(
            run_id=str(run_id),
            companies_checked=0,
            listings_fetched=0,
            after_dedup=0,
            after_deterministic=0,
            after_triage=0,
            scored=0,
            new_jobs_written=0,
            updated=0,
            failed=0,
            tokens_in=spend_tracker.tokens_in,
            tokens_out=spend_tracker.tokens_out,
            cost_usd=spend_tracker.spent_usd,
            errors=(),
            status=RunStatus.STOPPED.value,
        )

    except Exception as exc:
        # 6. A crashed run must not leave a row stuck in 'running' forever.
        # The tracker's values are used here too: a run that crashed after
        # spending money still spent it.
        session = sessionmaker()
        try:
            # GAP-16: never overwrite a terminal row (e.g. one the grace sweep
            # already finalized) with a late FAILED.
            RunRepository(session).finish_if_running(
                run_id,
                status=RunStatus.FAILED,
                companies_checked=0,
                listings_fetched=0,
                after_dedup=0,
                after_deterministic=0,
                after_triage=0,
                scored=0,
                new_jobs_written=0,
                tokens_in=spend_tracker.tokens_in,
                tokens_out=spend_tracker.tokens_out,
                cost_usd=spend_tracker.spent_usd,
                error_summary=f"run aborted: {type(exc).__name__}: {exc}",
            )
            clear_run_lease(session, run_id)
            session.commit()
        finally:
            session.close()
        raise
    finally:
        # GAP-15: always stop the lease heartbeat, on every exit path.
        heartbeat.stop()


def _clear_run_artifacts(sessionmaker: sessionmaker, run_id: uuid.UUID) -> None:
    """Clear a run's lease + any lingering stop request on their own session.

    Best-effort caller: the success path clears both so a stop requested after
    the last checkpoint cannot linger into a later run.
    """
    session = sessionmaker()
    try:
        clear_run_lease(session, run_id)
        clear_stop_request(session, run_id)
        session.commit()
    finally:
        session.close()


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