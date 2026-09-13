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
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.api.background import resolve_company_in_background, run_in_background
from huntloop.api.deps import get_session
from huntloop.db.models import AtsPlatform, Company
from huntloop.db.repository import CompanyRepository
from huntloop.discovery.ats.registry import ADAPTERS
from huntloop.registry.resolve import build_manual_resolution_config, mark_resolving
from huntloop.registry.staleness import is_possibly_stale, staleness_message

router = APIRouter(prefix="/api/companies", tags=["companies"])

# GAP-14: a manually entered board must be on a platform the resolver can
# actually watch. Reuse the resolver's own adapter set rather than duplicating
# it, so the manual path and the automatic path can never disagree.
SUPPORTED_MANUAL_PLATFORMS = tuple(sorted(ADAPTERS))

# GAP-10: the persisted resolution trail (ats_config.resolution.reason) is
# diagnostics-only. The API exposes one generic, user-facing sentence and never
# the internal probe/candidate detail.
RESOLUTION_FAILURE_MESSAGE = (
    "We couldn't find a supported job board for this employer automatically. "
    "Retry, or set the job board manually."
)


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
    # GAP-13 lifecycle, derived from persisted facts: "added" | "resolving" |
    # "resolved" | "error" — a status, never an error (D-07).
    resolution_state: str
    # The generic failure sentence; only present in the "error" state.
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
    """An in-place partial update: the enabled toggle and/or manual board entry.

    Every field is optional so the enabled toggle keeps working unchanged and a
    board can be set independently (D-14a). ``patch_company`` rejects an empty
    body (nothing to update) with 422.
    """

    enabled: bool | None = None
    ats: str | None = None
    ats_identifier: str | None = None
    careers_url: str | None = None


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


class CoverageOut(BaseModel):
    added: int
    watchable: int
    needs_attention: int
    resolved: int


def _resolution_block(company: Company) -> dict:
    if not company.ats_config:
        return {}
    return company.ats_config.get("resolution", {}) or {}


def _parse_iso(value: object) -> datetime | None:
    """Parse a persisted ISO timestamp; None when missing or malformed."""
    if not isinstance(value, str) or not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except ValueError:
        return None


def _as_utc(value: datetime | None) -> datetime | None:
    """Normalise a datetime to UTC so naive/aware comparisons never raise."""
    if value is None:
        return None
    if value.tzinfo is None:
        return value.replace(tzinfo=UTC)
    return value.astimezone(UTC)


def _resolution_state(company: Company) -> str:
    """Derive the one honest lifecycle state from persisted facts (GAP-13).

    A ``resolving`` marker is authoritative only while it is newer than the
    last successful completion (or there is none); a stale marker can therefore
    never mask a newer ``resolved``. With no marker: ``resolved`` when
    ``resolved_at`` is set, ``error`` when a completed (unsuccessful) resolution
    block exists, else ``added``.
    """
    block = _resolution_block(company)
    if block.get("state") == "resolving":
        started = _as_utc(_parse_iso(block.get("started_at")))
        if started is None:
            return "resolving"
        resolved_at = _as_utc(company.resolved_at)
        if resolved_at is None or started > resolved_at:
            return "resolving"
    if company.resolved_at is not None:
        return "resolved"
    if block:
        return "error"
    return "added"


def _resolution_detail(state: str) -> str | None:
    """The generic failure sentence, shown ONLY in the error state (GAP-13).

    The raw ``ats_config.resolution.reason`` stays persisted for diagnostics and
    is deliberately never returned.
    """
    return RESOLUTION_FAILURE_MESSAGE if state == "error" else None


def _company_to_out(company: Company) -> CompanyOut:
    resolved = company.resolved_at is not None
    state = _resolution_state(company)
    return CompanyOut(
        id=company.id,
        name=company.name,
        ats=company.ats.value if company.ats is not None else None,
        ats_identifier=company.ats_identifier,
        careers_url=company.careers_url,
        enabled=company.enabled,
        resolved=resolved,
        resolution_state=state,
        resolution_detail=_resolution_detail(state),
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

    GAP-13: the ``resolving`` marker for every existing id is committed BEFORE
    any thread starts and before the 202, so a refresh mid-probe reads
    ``resolving`` rather than the old two-value status.
    """
    for company_id in body.ids:
        company = session.get(Company, company_id)
        if company is not None:
            company.ats_config = mark_resolving(company.ats_config)
    session.commit()
    for company_id in body.ids:
        run_in_background(resolve_company_in_background, company_id)
    return ResolveBatchResult(queued=len(body.ids))


@router.get("/coverage", response_model=CoverageOut)
def coverage(session: Session = Depends(get_session)) -> CoverageOut:
    """D-06's honest headline numbers — no client-side math.

    ``watchable`` counts only resolved AND enabled employers (what will
    actually be watched); ``needs_attention`` is every added employer whose
    resolution has not succeeded. The UI renders "we'd watch N of your M added
    employers" directly from these fields.
    """
    companies = session.execute(select(Company)).scalars().all()
    added = len(companies)
    resolved = sum(1 for c in companies if c.resolved_at is not None)
    watchable = sum(1 for c in companies if c.resolved_at is not None and c.enabled)
    return CoverageOut(
        added=added,
        watchable=watchable,
        needs_attention=added - resolved,
        resolved=resolved,
    )


@router.patch("/{company_id}", response_model=CompanyOut)
def patch_company(
    company_id: uuid.UUID,
    body: CompanyPatch,
    session: Session = Depends(get_session),
) -> CompanyOut:
    """Toggle ``enabled`` and/or set a job board by hand (GAP-14).

    History is never touched — rows are never removed. Setting a supported
    platform + board id records the resolution as ``manual`` and sets
    ``resolved_at`` so the employer becomes watchable/Resolved immediately;
    an unsupported platform or a missing identifier is a 422 with a human
    sentence. A careers URL on its own just updates the URL.
    """
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail=f"company {company_id} not found")
    data = body.model_dump(exclude_unset=True)
    if not data:
        raise HTTPException(status_code=422, detail="Provide at least one field to update.")
    if "enabled" in data:
        company.enabled = bool(data["enabled"])
    board_requested = data.get("ats") is not None or data.get("ats_identifier") is not None
    if board_requested:
        platform = (data.get("ats") or "").strip()
        identifier = (data.get("ats_identifier") or "").strip()
        if not platform or not identifier:
            raise HTTPException(
                status_code=422,
                detail="Choose a supported platform and enter its board id or slug.",
            )
        if platform not in SUPPORTED_MANUAL_PLATFORMS:
            raise HTTPException(
                status_code=422,
                detail=(
                    f"Unsupported platform '{platform}'. "
                    f"Choose one of: {', '.join(SUPPORTED_MANUAL_PLATFORMS)}."
                ),
            )
        company.ats = AtsPlatform(platform)
        company.ats_identifier = identifier
        if "careers_url" in data:
            company.careers_url = (data["careers_url"] or "").strip() or None
        company.ats_config = build_manual_resolution_config(
            platform=platform,
            identifier=identifier,
            careers_url=company.careers_url,
        )
        company.resolved_at = datetime.now(UTC)
    elif "careers_url" in data:
        company.careers_url = (data["careers_url"] or "").strip() or None
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

    GAP-13: the ``resolving`` marker is committed synchronously BEFORE the
    background thread starts and before the 202, so a page refresh mid-probe
    reads ``resolving`` instead of collapsing to ``added``.
    """
    company = session.get(Company, company_id)
    if company is None:
        raise HTTPException(status_code=404, detail=f"company {company_id} not found")
    company.ats_config = mark_resolving(company.ats_config)
    session.commit()
    run_in_background(resolve_company_in_background, company_id)
    return ResolveAccepted(status="accepted")
