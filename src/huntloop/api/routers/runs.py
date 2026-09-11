"""Run API: on-demand trigger, run history, and per-run detail (RUN-02,
RUN-06, RUN-09, D-17).

The API is a thin reshape of Phase 2/3's existing run model: history carries
exactly the field set ``huntloop.cli.render.RUN_HISTORY_FIELDS`` defines, detail
carries the same shape plus the seven-counter funnel and every recorded error,
and the trigger composes Phase 3's own overlap guard rather than inventing a
second one. Nothing here starts a run synchronously — the request accepts (202)
and the run happens on a daemon thread via ``huntloop.api.background`` (D-17).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.api.background import run_in_background
from huntloop.api.deps import get_session
from huntloop.db.base import get_engine, make_session_factory
from huntloop.db.models import Company, Run, RunError, RunTrigger
from huntloop.db.repository import RunRepository
from huntloop.graph.build import run_discovery
from huntloop.scheduler.jobs import find_run_in_progress

router = APIRouter(prefix="/api/runs", tags=["runs"])

# cost_usd is Numeric(12,6) -> Decimal. Quantizing to four places keeps a
# six-place value from surfacing as 0.012300000000000001 (the CLI's lesson);
# four places is where real per-run spend is meaningful.
_COST_QUANTUM = Decimal("0.0001")


def _quantize_cost(value) -> float | None:
    if value is None:
        return None
    return float(Decimal(value).quantize(_COST_QUANTUM))


# ---------------------------------------------------------------------------
# Response models (colocated here — mirrors criteria/companies/jobs)
# ---------------------------------------------------------------------------


class RunOut(BaseModel):
    """A run-history row: the ``RUN_HISTORY_FIELDS`` shape plus identity."""

    id: uuid.UUID
    started_at: datetime
    finished_at: datetime | None
    trigger: str
    status: str
    companies_checked: int
    listings_fetched: int
    after_deterministic: int
    after_triage: int
    scored: int
    new_jobs_written: int
    tokens_in: int
    tokens_out: int
    cost_usd: float | None
    error_summary: str | None


class RunErrorOut(BaseModel):
    company_name: str | None
    stage: str | None
    message: str | None


class RunDetailOut(RunOut):
    after_dedup: int
    errors: list[RunErrorOut]


class TriggerOut(BaseModel):
    status: str


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _run_out(run: Run) -> RunOut:
    return RunOut(
        id=run.id,
        started_at=run.started_at,
        finished_at=run.finished_at,
        trigger=run.trigger.value,
        status=run.status.value,
        companies_checked=run.companies_checked or 0,
        listings_fetched=run.listings_fetched or 0,
        after_deterministic=run.after_deterministic or 0,
        after_triage=run.after_triage or 0,
        scored=run.scored or 0,
        new_jobs_written=run.new_jobs_written or 0,
        tokens_in=run.tokens_in or 0,
        tokens_out=run.tokens_out or 0,
        cost_usd=_quantize_cost(run.cost_usd),
        error_summary=run.error_summary,
    )


def _run_manual_discovery() -> None:
    """The background body of POST /api/runs.

    Builds its own sessionmaker (the request session is closed the moment the
    response returns) and calls the identical ``run_discovery`` entrypoint the
    CLI and scheduler use. No try/except that swallows: ``run_discovery`` marks
    its own Run FAILED on abort (Phase 3 precedent), so re-raising here is
    correct — the daemon thread dies, the Run row does not lie.
    """
    sessionmaker = make_session_factory(get_engine())
    run_discovery(sessionmaker=sessionmaker, trigger=RunTrigger.MANUAL)


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[RunOut])
def list_runs(
    limit: int = Query(default=20, ge=1, le=200),
    session: Session = Depends(get_session),
) -> list[RunOut]:
    """Run history, newest first (RUN-09). An empty database is a valid answer."""
    return [_run_out(run) for run in RunRepository(session).list_recent(limit)]


@router.get("/{run_id}", response_model=RunDetailOut)
def get_run(
    run_id: uuid.UUID, session: Session = Depends(get_session)
) -> RunDetailOut:
    """One run's full detail incl. errors with employer and stage (RUN-06)."""
    run = RunRepository(session).get(run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="run not found")

    rows = session.execute(
        select(RunError, Company.name)
        .outerjoin(Company, RunError.company_id == Company.id)
        .where(RunError.run_id == run_id)
        .order_by(RunError.id)
    ).all()

    base = _run_out(run)
    return RunDetailOut(
        **base.model_dump(),
        after_dedup=run.after_dedup or 0,
        errors=[
            RunErrorOut(company_name=name, stage=err.stage, message=err.message)
            for err, name in rows
        ],
    )


@router.post("", status_code=202, response_model=TriggerOut)
def trigger_run(session: Session = Depends(get_session)):
    """Start a run in the background — the request never blocks (RUN-02, D-17).

    Phase 3's ``find_run_in_progress`` is the overlap guard, reused verbatim so
    a manual trigger and a scheduled fire cannot both run. When one is already
    running the page is told exactly which run, instead of erroring opaquely.
    The 202 deliberately carries no run_id: the Run row is created inside
    ``run_discovery`` a moment later, and the UI watches the newest RUNNING row
    appear via GET /api/runs.
    """
    in_progress = find_run_in_progress(session)
    if in_progress is not None:
        return JSONResponse(
            status_code=409,
            content={
                "run_id": str(in_progress.id),
                "detail": (
                    "a run is already in progress "
                    f"(started {in_progress.started_at.isoformat()})"
                ),
            },
        )

    run_in_background(_run_manual_discovery)
    return TriggerOut(status="accepted")
