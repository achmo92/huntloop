"""Dashboard API: the one-request answer to "what happened, what's next, where
do things stand" (UI-02, D-14).

A quiet week must be explainable without poking at a terminal, so this endpoint
aggregates the last run + funnel, the next scheduled fire, and the listings
count per pipeline stage in a single response. The stage map always carries
every ``JobStatus`` key — a stage with zero listings is information, and a
stable shape means the UI never has to defend against a missing key.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from huntloop.api.deps import get_session
from huntloop.api.routers.runs import RunOut, _run_out
from huntloop.config import load_config
from huntloop.db.models import Job, JobStatus
from huntloop.db.repository import JobRepository, RunRepository
from huntloop.scheduler.build import next_fire_time

router = APIRouter(prefix="/api/dashboard", tags=["dashboard"])


class FunnelOut(BaseModel):
    """Last run's seven per-stage counters (the pipeline funnel)."""

    companies_checked: int
    listings_fetched: int
    after_dedup: int
    after_deterministic: int
    after_triage: int
    scored: int
    new_jobs_written: int


class DashboardOut(BaseModel):
    last_run: RunOut | None
    funnel: FunnelOut | None
    next_scheduled_run: datetime
    listings_by_status: dict[str, int]
    total_listings: int


@router.get("", response_model=DashboardOut)
def get_dashboard(session: Session = Depends(get_session)) -> DashboardOut:
    recent = RunRepository(session).list_recent(1)
    last_run = recent[0] if recent else None

    funnel = None
    if last_run is not None:
        funnel = FunnelOut(
            companies_checked=last_run.companies_checked or 0,
            listings_fetched=last_run.listings_fetched or 0,
            after_dedup=last_run.after_dedup or 0,
            after_deterministic=last_run.after_deterministic or 0,
            after_triage=last_run.after_triage or 0,
            scored=last_run.scored or 0,
            new_jobs_written=last_run.new_jobs_written or 0,
        )

    # One grouped query; every enum key seeded to zero so absent stages are
    # explicit rather than missing.
    listings_by_status = {status.value: 0 for status in JobStatus}
    rows = session.execute(
        select(Job.status, func.count()).group_by(Job.status)
    ).all()
    for status, count in rows:
        listings_by_status[status.value] = count

    return DashboardOut(
        last_run=_run_out(last_run) if last_run is not None else None,
        funnel=funnel,
        next_scheduled_run=next_fire_time(load_config()),
        listings_by_status=listings_by_status,
        total_listings=JobRepository(session).count(),
    )
