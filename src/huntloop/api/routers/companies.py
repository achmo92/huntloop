"""Employer-registry API: list with resolution status, add, enable/disable,
batch add, and background resolution (REG-04, REG-06, D-07, D-08).

Resolution is a STATUS here, not an exception: background resolution runs the
Phase 2 ``resolve_employer`` / ``persist_resolution`` code path (via
``huntloop.api.background``) — never a reimplementation — and the Phase 2
resolver persists its full candidate/probe trail in ``companies.ats_config``,
which this router surfaces as-is. Disabling is the only off-switch — this
module exposes no row-removal path (REG-06's "without losing history"), and the
list endpoint deliberately does not filter on ``enabled`` (one list, D-08).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.api.background import resolve_company_in_background, run_in_background
from huntloop.api.deps import get_session
from huntloop.db.models import Company
from huntloop.db.repository import CompanyRepository
from huntloop.registry.staleness import is_possibly_stale, staleness_message

router = APIRouter(prefix="/api/companies", tags=["companies"])


# ---------------------------------------------------------------------------
# Request / response models (colocated here — mirrors criteria.py's pattern)
# ---------------------------------------------------------------------------


class CompanyOut(BaseModel):
    id: uuid.UUID
    name: str
    ats: str | None
    ats_identifier: str | None
    careers_url: str | None
    enabled: bool
    resolved: bool
    # "resolved" | "needs_attention" — a status column, never an error (D-07).
    resolution_status: str
    # What was tried / why it failed, read from the persisted resolution block.
    resolution_detail: str | None
    possibly_stale: bool
    staleness_message: str | None
    last_job_count: int | None
    last_checked_at: datetime | None
    consecutive_empty_runs: int


class CompanyCreate(BaseModel):
    name: str = Field(min_length=1)
    careers_url: str | None = None


class CompanyPatch(BaseModel):
    enabled: bool


class BatchCreate(BaseModel):
    names: list[str]
    careers_urls: dict[str, str] | None = None


class BatchCreated(BaseModel):
    added: list[CompanyOut]


class ResolveAccepted(BaseModel):
    status: str = "accepted"


class ResolveBatchRequest(BaseModel):
    ids: list[uuid.UUID]


class ResolveBatchResult(BaseModel):
    queued: int


def _resolution_block(company: Company) -> dict:
    if not company.ats_config:
        return {}
    return company.ats_config.get("resolution", {}) or {}


def _company_to_out(company: Company) -> CompanyOut:
    resolved = company.resolved_at is not None
    detail = _resolution_block(company).get("reason") or None
    return CompanyOut(
        id=company.id,
        name=company.name,
        ats=company.ats.value if company.ats is not None else None,
        ats_identifier=company.ats_identifier,
        careers_url=company.careers_url,
        enabled=company.enabled,
        resolved=resolved,
        resolution_status="resolved" if resolved else "needs_attention",
        resolution_detail=detail,
        possibly_stale=is_possibly_stale(company),
        staleness_message=staleness_message(company),
        last_job_count=company.last_job_count,
        last_checked_at=company.last_checked_at,
        consecutive_empty_runs=company.consecutive_empty_runs,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[CompanyOut])
def list_companies(session: Session = Depends(get_session)) -> list[CompanyOut]:
    """ALL companies, enabled and disabled, ordered by name.

    Deliberately NOT ``CompanyRepository.list_enabled()`` — D-08's one-list
    rule needs disabled rows present with ``enabled=false`` so history stays
    visible.
    """
    companies = session.execute(select(Company).order_by(Company.name)).scalars().all()
    return [_company_to_out(c) for c in companies]


@router.post("", status_code=201, response_model=CompanyOut)
def add_company(
    body: CompanyCreate, session: Session = Depends(get_session)
) -> CompanyOut:
    """Register an employer through the repository, same as CLI `company add`.

    No live probe at add time (D-05): resolution is a separate, user-gated
    step via POST /{id}/resolve or POST /resolve-batch.
    """
    repo = CompanyRepository(session)
    repo.upsert_by_name(body.name, careers_url=body.careers_url)
    session.commit()
    return _company_to_out(repo.get_by_name(body.name))


@router.post("/batch", status_code=201, response_model=BatchCreated)
def add_batch(
    body: BatchCreate, session: Session = Depends(get_session)
) -> BatchCreated:
    """Bulk add the batch-reviewed, accepted employers (D-05).

    The UI checks/unchecks before this call; the endpoint adds exactly what it
    is given and performs no probing.
    """
    repo = CompanyRepository(session)
    urls = body.careers_urls or {}
    for name in body.names:
        repo.upsert_by_name(name, careers_url=urls.get(name))
    session.commit()
    added = [_company_to_out(c) for name in body.names if (c := repo.get_by_name(name))]
    return BatchCreated(added=added)


@router.post("/resolve-batch", status_code=202, response_model=ResolveBatchResult)
def resolve_batch(
    body: ResolveBatchRequest, session: Session = Depends(get_session)
) -> ResolveBatchResult:
    """Queue background resolution for each id (one thread per employer).

    Resolution probes are I/O-bound; per-employer threads mirror the Phase 2
    fan-out philosophy. Resolution is not a Run, so the scheduler's
    run-overlap guard is unaffected.
    """
    for company_id in body.ids:
        run_in_background(resolve_company_in_background, company_id)
    return ResolveBatchResult(queued=len(body.ids))


@router.patch("/{company_id}", response_model=CompanyOut)
def patch_company(
    company_id: uuid.UUID,
    body: CompanyPatch,
    session: Session = Depends(get_session),
) -> CompanyOut:
    """Toggle ``enabled`` in place. History is never touched — rows are never removed."""
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail=f"company {company_id} not found")
    company.enabled = body.enabled
    session.commit()
    return _company_to_out(company)


@router.post(
    "/{company_id}/resolve", status_code=202, response_model=ResolveAccepted
)
def resolve_company(
    company_id: uuid.UUID, session: Session = Depends(get_session)
) -> ResolveAccepted:
    """Accept a retry (202) and run the probe in the background (D-07).

    Never awaits the probe in-request: live probes take seconds-to-tens-of-
    seconds. The row's resolution state updates when the thread finishes.
    """
    if session.get(Company, company_id) is None:
        raise HTTPException(status_code=404, detail=f"company {company_id} not found")
    run_in_background(resolve_company_in_background, company_id)
    return ResolveAccepted(status="accepted")
