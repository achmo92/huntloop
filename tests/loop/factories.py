"""Shared row builders for the Phase 5 loop test modules (owner: plan 05-01).

Plans 05-02, 05-03 and 05-05 all need the same fixtures — a company, a job with
dimension scores and advisory flags, a status-transition history, and an active
criteria version. Defining them once here stops three parallel executors from
inventing three incompatible shapes.

Conventions:
- Every builder takes an open ``Session`` and **commits nothing** — the caller
  owns the transaction boundary.
- ``make_criteria`` goes through ``save_new_criteria_version``, never a direct
  INSERT, so tests exercise the same insert-only versioning path production uses.
- ``loop_session`` is exposed as a plain fixture (not a conftest) and must be
  imported explicitly where used:
  ``from tests.loop.factories import loop_session  # noqa: F401``
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from sqlalchemy.orm import Session

from huntloop.criteria.loader import save_new_criteria_version
from huntloop.criteria.schema import (
    CompensationFloor,
    CriteriaPayload,
    DimensionWeights,
    Exclusions,
    LocationCriteria,
    WorkAuthorization,
)
from huntloop.db.base import Base, make_engine, make_session_factory
from huntloop.db.models import (
    Company,
    CompPeriod,
    Job,
    JobStatus,
    StatusEvent,
    StatusEventSource,
)

__all__ = [
    "default_payload",
    "loop_session",
    "make_company",
    "make_criteria",
    "make_job",
    "make_transitions",
]


def _as_utc(value: datetime) -> datetime:
    """Attach UTC to a naive datetime so ``UTCDateTime`` accepts the write."""
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def make_company(session: Session, *, name: str) -> Company:
    """Insert one Company and flush so its id is available to callers."""
    company = Company(name=name)
    session.add(company)
    session.flush()
    return company


def make_job(
    session: Session,
    *,
    company: Company,
    title: str,
    status: JobStatus = JobStatus.NEW,
    first_seen_at: datetime | None = None,
    posted_at: datetime | None = None,
    location_normalized: str | None = None,
    is_remote: bool = False,
    comp_min: Decimal | None = None,
    comp_currency: str | None = None,
    comp_period: CompPeriod | None = None,
    score_overall: Decimal | None = None,
    score_dimensions: dict[str, float] | None = None,
    score_flags: dict[str, dict] | None = None,
) -> Job:
    """Insert one Job and flush.

    ``dedup_key``/``url`` are derived from ``(company.name, title)`` — pass
    distinct titles per company. ``first_seen_at`` defaults to 30 days ago, which
    is the reference point a fast-reject dwell time is measured from.
    """
    if first_seen_at is None:
        first_seen_at = datetime.now(UTC) - timedelta(days=30)
    else:
        first_seen_at = _as_utc(first_seen_at)
    if posted_at is not None:
        posted_at = _as_utc(posted_at)

    dedup_key = f"{company.name}:{title}"
    job = Job(
        company_id=company.id,
        dedup_key=dedup_key,
        url=f"https://example.test/jobs/{uuid.uuid5(uuid.NAMESPACE_URL, dedup_key)}",
        title=title,
        location_normalized=location_normalized,
        is_remote=is_remote,
        posted_at=posted_at,
        first_seen_at=first_seen_at,
        last_seen_at=first_seen_at,
        comp_min=comp_min,
        comp_currency=comp_currency,
        comp_period=comp_period,
        status=status,
        score_overall=score_overall,
        score_dimensions=score_dimensions,
        score_flags=score_flags,
    )
    session.add(job)
    session.flush()
    return job


def make_transitions(
    session: Session,
    job: Job,
    pairs: list[tuple[JobStatus | None, JobStatus]],
    *,
    start: datetime,
    step_days: float = 1.0,
) -> list[StatusEvent]:
    """Insert a StatusEvent per ``(from_status, to_status)`` pair and flush.

    Events are spaced ``step_days`` apart from ``start``. The job's own
    ``status`` is set to the final ``to_status`` so the pair stays truthful
    (``jobs.status`` and ``status_events`` are never allowed to disagree).
    """
    start = _as_utc(start)
    events: list[StatusEvent] = []
    for index, (from_status, to_status) in enumerate(pairs):
        event = StatusEvent(
            job_id=job.id,
            from_status=from_status,
            to_status=to_status,
            changed_at=start + timedelta(days=step_days * index),
            source=StatusEventSource.USER,
        )
        session.add(event)
        events.append(event)
    if pairs:
        job.status = pairs[-1][1]
    session.flush()
    return events


def default_payload(**overrides) -> CriteriaPayload:
    """A fully valid CriteriaPayload; ``overrides`` vary one field inline."""
    payload = CriteriaPayload(
        profile_summary="Staff-level backend engineer in the US, remote-friendly.",
        seniority_min="mid",
        seniority_max="staff",
        posting_age_days=30,
        locations=LocationCriteria(
            eligible_countries=["US"], eligible_regions=[], preferred_cities=[]
        ),
        compensation_floor=CompensationFloor(
            amount=Decimal("150000"), currency="USD", period="annual"
        ),
        exclusions=Exclusions(title_keywords=[], employers=[]),
        work_authorization=WorkAuthorization(
            countries_authorized=["US"], requires_sponsorship=False
        ),
        dimension_weights=DimensionWeights(
            role_fit=3.0, seniority_fit=2.0, employer_fit=2.0, trajectory=1.0
        ),
    )
    if overrides:
        payload = payload.model_copy(update=overrides)
    return payload


def make_criteria(session: Session, payload: CriteriaPayload | None = None) -> int:
    """Save a new active criteria version and return its version number."""
    return save_new_criteria_version(
        session, payload if payload is not None else default_payload()
    )


@pytest.fixture
def loop_session(tmp_path):
    """A Session over a tmp-path SQLite engine with the main schema created."""
    import huntloop.db.models  # noqa: F401  (populates Base.metadata)

    engine = make_engine(f"sqlite:///{tmp_path / 'loop.db'}")
    Base.metadata.create_all(engine)
    session = make_session_factory(engine)()
    try:
        yield session
    finally:
        session.rollback()
        session.close()
        engine.dispose()
