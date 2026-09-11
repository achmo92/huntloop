"""Listings API: composable list, explainable detail, one-action status
transitions and freeform notes (TRAK-01..05, TRAK-07, UI-06, D-10..D-13).

Reads the Phase 2 scoring output exactly as stored (``jobs.score_dimensions`` /
``score_flags``) and surfaces it honestly: list rows carry the total score
only, the detail carries the per-dimension breakdown with each dimension's
reasoning (D-11). Every write goes through the repository layer — status
transitions call ``JobRepository.set_status``, the single owner of the
``jobs.status`` + ``StatusEvent`` pair (Phase 1 contract), so the CLI and API
share one truth. Employer-supplied description text crosses the boundary only
through ``to_safe_text`` (UI-06).
"""

from __future__ import annotations

import uuid
from datetime import UTC, date, datetime, time
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import func, nullslast, select
from sqlalchemy.orm import Session

from huntloop.api.deps import get_session
from huntloop.api.sanitize import to_safe_text
from huntloop.db.models import (
    Company,
    FeedbackNote,
    FeedbackSource,
    Job,
    JobStatus,
    StatusEvent,
)

router = APIRouter(prefix="/api/jobs", tags=["jobs"])

# page_size is clamped rather than rejected: a client asking for 500 gets the
# maximum the server is willing to serve (a 422 here would be a needless
# failure for a harmless request).
MAX_PAGE_SIZE = 200


# ---------------------------------------------------------------------------
# Request / response models (colocated here — mirrors criteria/companies)
# ---------------------------------------------------------------------------


class JobRowOut(BaseModel):
    id: uuid.UUID
    company_id: uuid.UUID
    company_name: str
    title: str
    location: str | None
    is_remote: bool | None
    comp_min: float | None
    comp_max: float | None
    comp_currency: str | None
    comp_period: str | None
    score_overall: float | None
    score_flags: dict | None
    status: str
    posted_at: datetime | None
    first_seen_at: datetime
    url: str


class JobListOut(BaseModel):
    items: list[JobRowOut]
    total: int
    page: int
    page_size: int


class StatusEventOut(BaseModel):
    from_status: str | None
    to_status: str
    changed_at: datetime
    source: str


class NoteOut(BaseModel):
    id: uuid.UUID
    text: str
    created_at: datetime


class JobDetailOut(JobRowOut):
    description: str
    score_dimensions: dict | None
    score_summary: str | None
    scored_criteria_version: int | None
    scored_rubric_version: str | None
    scored_with_model: str | None
    filter_tier_reached: str | None
    open_duration_days: int | None
    repost_count: int
    status_events: list[StatusEventOut]
    notes: list[NoteOut]


class StatusPatch(BaseModel):
    status: JobStatus


class StatusUpdateOut(BaseModel):
    id: uuid.UUID
    status: str
    changed_at: datetime


class NoteCreate(BaseModel):
    text: str = Field(min_length=1, max_length=5000)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _as_float(value) -> float | None:
    return float(value) if value is not None else None


def _row_out(job: Job, company_name: str) -> JobRowOut:
    return JobRowOut(
        id=job.id,
        company_id=job.company_id,
        company_name=company_name,
        title=job.title,
        location=job.location_normalized or job.location_raw,
        is_remote=job.is_remote,
        comp_min=_as_float(job.comp_min),
        comp_max=_as_float(job.comp_max),
        comp_currency=job.comp_currency,
        comp_period=job.comp_period.value if job.comp_period is not None else None,
        score_overall=_as_float(job.score_overall),
        score_flags=job.score_flags,
        status=job.status.value,
        posted_at=job.posted_at,
        first_seen_at=job.first_seen_at,
        url=job.url,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=JobListOut)
def list_jobs(
    status: str | None = Query(default=None, description="comma-separated stages"),
    employer_id: uuid.UUID | None = Query(default=None),
    score_min: float | None = Query(default=None),
    posted_after: date | None = Query(default=None),
    posted_before: date | None = Query(default=None),
    sort: Literal["score_overall", "posted_at", "first_seen_at", "title"] = Query(
        default="score_overall"
    ),
    order: Literal["asc", "desc"] = Query(default="desc"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1),
    session: Session = Depends(get_session),
) -> JobListOut:
    """One composable query (D-12, TRAK-05) — no client-side filtering.

    Unscored rows are INCLUDED with ``score_overall: null`` (TRAK-06's
    write-every-listing philosophy on the read side) and sink to the bottom on
    a score sort via ``NULLS LAST``, never disappearing.
    """
    page_size = min(page_size, MAX_PAGE_SIZE)

    conditions = []
    if status:
        try:
            wanted = [
                JobStatus(token.strip())
                for token in status.split(",")
                if token.strip()
            ]
        except ValueError:
            raise HTTPException(status_code=422, detail="invalid status filter") from None
        conditions.append(Job.status.in_(wanted))
    if employer_id is not None:
        conditions.append(Job.company_id == employer_id)
    if score_min is not None:
        conditions.append(Job.score_overall >= score_min)
    if posted_after is not None:
        conditions.append(
            Job.posted_at >= datetime.combine(posted_after, time.min, tzinfo=UTC)
        )
    if posted_before is not None:
        conditions.append(
            Job.posted_at <= datetime.combine(posted_before, time.max, tzinfo=UTC)
        )

    total = session.execute(
        select(func.count()).select_from(Job).where(*conditions)
    ).scalar_one()

    sort_column = {
        "score_overall": Job.score_overall,
        "posted_at": Job.posted_at,
        "first_seen_at": Job.first_seen_at,
        "title": Job.title,
    }[sort]
    direction = sort_column.desc() if order == "desc" else sort_column.asc()

    stmt = (
        select(Job, Company.name)
        .join(Company, Job.company_id == Company.id)
        .where(*conditions)
        # NULLS LAST regardless of direction: an unscored row sinks on a score
        # sort (used by `nullslast`, the portable construct per the plan).
        .order_by(nullslast(direction), Job.id)
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    rows = session.execute(stmt).all()
    items = [_row_out(job, company_name) for job, company_name in rows]
    return JobListOut(items=items, total=total, page=page, page_size=page_size)


@router.get("/{job_id}", response_model=JobDetailOut)
def get_job(
    job_id: uuid.UUID, session: Session = Depends(get_session)
) -> JobDetailOut:
    """Full explainable detail (D-11, D-13, TRAK-04, TRAK-07).

    ``repost_count`` is the honest fact "identical-title postings at this same
    employer, excluding this row"; ``open_duration_days`` is the floor of
    elapsed time since first discovery. Neither is a predictive score.
    """
    job = session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="listing not found")

    company = session.get(Company, job.company_id)
    company_name = company.name if company is not None else ""

    repost_total = session.execute(
        select(func.count())
        .select_from(Job)
        .where(Job.company_id == job.company_id, Job.title == job.title)
    ).scalar_one()

    open_duration_days = None
    if job.first_seen_at is not None:
        open_duration_days = (datetime.now(UTC) - job.first_seen_at).days

    events = (
        session.execute(
            select(StatusEvent)
            .where(StatusEvent.job_id == job_id)
            .order_by(StatusEvent.changed_at.asc())
        )
        .scalars()
        .all()
    )
    notes = (
        session.execute(
            select(FeedbackNote)
            .where(
                FeedbackNote.job_id == job_id,
                FeedbackNote.source == FeedbackSource.JOB_NOTE,
            )
            .order_by(FeedbackNote.created_at.asc())
        )
        .scalars()
        .all()
    )

    base = _row_out(job, company_name)
    return JobDetailOut(
        **base.model_dump(),
        description=to_safe_text(job.description),
        score_dimensions=job.score_dimensions,
        score_summary=job.score_summary,
        scored_criteria_version=job.scored_criteria_version,
        scored_rubric_version=job.scored_rubric_version,
        scored_with_model=job.scored_with_model,
        filter_tier_reached=(
            job.filter_tier_reached.value
            if job.filter_tier_reached is not None
            else None
        ),
        open_duration_days=open_duration_days,
        repost_count=max(repost_total - 1, 0),
        status_events=[
            StatusEventOut(
                from_status=(
                    e.from_status.value if e.from_status is not None else None
                ),
                to_status=e.to_status.value,
                changed_at=e.changed_at,
                source=e.source.value,
            )
            for e in events
        ],
        notes=[NoteOut(id=n.id, text=n.text, created_at=n.created_at) for n in notes],
    )
