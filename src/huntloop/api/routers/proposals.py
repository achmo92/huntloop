"""Proposals review API: LOOP-07 accept/reject and LOOP-09 rejection history.

UI-03's "conversational review" is deliberately plain request/response — no
chat, no WebSocket (ROADMAP Phase Ordering Rationale). This router serves
pending proposals with their rationale, evidence and predicted effect, accepts
one through the insert-only versioning path in ``huntloop.loop.apply`` (never a
direct ``Criteria`` write — LOOP-07), and rejects one with an optional
sanitised one-line reason.

LOOP-09 is why listing does more than a query: every item carries its edit
signature's rejection history inline ("you rejected a similar proposal on
[date]") so a repeatedly-rejected suggestion stays visible as a pattern rather
than silently resurfacing or silently staying vetoed (D-10). That history is
gathered in ONE extra query and grouped in Python — never one query per
proposal — and a malformed historical row contributes nothing rather than
failing the whole list.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.api.deps import get_session
from huntloop.api.sanitize import to_safe_text
from huntloop.db.models import CriteriaProposal, ProposalStatus
from huntloop.loop.apply import (
    ProposalNotPending,
    ProposalValidationError,
    accept_proposal,
    reject_proposal,
)
from huntloop.loop.types import UnsupportedEditField, edit_signature

router = APIRouter(prefix="/api/proposals", tags=["proposals"])

MAX_PAGE_SIZE = 200
DEFAULT_LIMIT = 50

# LOOP-09's dedup key: (field, direction).
_Signature = tuple[str, str]
# (proposal id, decided_at, rejection_reason) — id kept so a returned rejected
# row never lists itself as its own prior rejection.
_RejectionEntry = tuple[uuid.UUID, datetime, str | None]


# ---------------------------------------------------------------------------
# Request / response models (colocated here — mirrors jobs/criteria)
# ---------------------------------------------------------------------------


class PriorRejection(BaseModel):
    decided_at: datetime
    rejection_reason: str | None


class ProposalOut(BaseModel):
    id: uuid.UUID
    created_at: datetime
    status: ProposalStatus
    based_on_run_id: uuid.UUID | None
    proposed_changes: dict[str, Any]
    rationale: str
    evidence: dict[str, Any] | None
    predicted_effect: dict[str, Any] | None
    decided_at: datetime | None
    resulting_version: int | None
    rejection_reason: str | None
    prior_rejections: list[PriorRejection]


class RejectRequest(BaseModel):
    reason: str | None = Field(default=None, max_length=500)


class AcceptResponse(BaseModel):
    version: int


class RejectResponse(BaseModel):
    status: ProposalStatus


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _signature_or_none(proposed_changes: Any) -> _Signature | None:
    """Return the edit signature, or None when a stored row cannot produce one.

    Historical or hand-edited data must never 500 the list endpoint: a row that
    names an unpermitted field (or is missing one) simply carries no history.
    """
    try:
        return edit_signature(proposed_changes)
    except (UnsupportedEditField, KeyError, TypeError):
        return None


def _rejection_history(
    session: Session,
) -> dict[_Signature, list[_RejectionEntry]]:
    """Every rejection's history in ONE query, grouped by edit signature.

    Only rejected rows with a decision timestamp qualify — the copy is "you
    rejected a similar proposal on [date]", so a date is required. Entries are
    sorted newest-first per signature.
    """
    rejected = (
        session.execute(
            select(CriteriaProposal).where(
                CriteriaProposal.status == ProposalStatus.REJECTED,
                CriteriaProposal.decided_at.is_not(None),
            )
        )
        .scalars()
        .all()
    )

    grouped: dict[_Signature, list[_RejectionEntry]] = {}
    for row in rejected:
        signature = _signature_or_none(row.proposed_changes)
        if signature is None:
            continue
        grouped.setdefault(signature, []).append(
            (row.id, row.decided_at, row.rejection_reason)
        )

    for entries in grouped.values():
        entries.sort(key=lambda entry: entry[1], reverse=True)
    return grouped


def _to_out(
    row: CriteriaProposal, grouped: dict[_Signature, list[_RejectionEntry]]
) -> ProposalOut:
    signature = _signature_or_none(row.proposed_changes)
    prior_rejections = (
        [
            PriorRejection(decided_at=decided_at, rejection_reason=reason)
            for proposal_id, decided_at, reason in grouped.get(signature, [])
            if proposal_id != row.id
        ]
        if signature is not None
        else []
    )
    return ProposalOut(
        id=row.id,
        created_at=row.created_at,
        status=row.status,
        based_on_run_id=row.based_on_run_id,
        proposed_changes=row.proposed_changes,
        rationale=row.rationale,
        evidence=row.evidence,
        predicted_effect=row.predicted_effect,
        decided_at=row.decided_at,
        resulting_version=row.resulting_version,
        rejection_reason=row.rejection_reason,
        prior_rejections=prior_rejections,
    )


def _load(session: Session, proposal_id: uuid.UUID) -> CriteriaProposal:
    proposal = session.get(CriteriaProposal, proposal_id)
    if proposal is None:
        raise HTTPException(status_code=404, detail="proposal not found")
    return proposal


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=list[ProposalOut])
def list_proposals(
    status: Literal["pending", "accepted", "rejected", "all"] = Query(default="pending"),
    limit: int = Query(default=DEFAULT_LIMIT, ge=1),
    session: Session = Depends(get_session),
) -> list[ProposalOut]:
    """Pending proposals newest-first, each with its LOOP-09 rejection history.

    ``status=all`` returns every proposal; the default is the review queue.
    ``limit`` is clamped rather than rejected (the jobs.py convention).
    """
    limit = min(limit, MAX_PAGE_SIZE)

    conditions = []
    if status != "all":
        conditions.append(CriteriaProposal.status == ProposalStatus(status))

    rows = (
        session.execute(
            select(CriteriaProposal)
            .where(*conditions)
            .order_by(CriteriaProposal.created_at.desc(), CriteriaProposal.id)
            .limit(limit)
        )
        .scalars()
        .all()
    )

    grouped = _rejection_history(session)
    return [_to_out(row, grouped) for row in rows]


@router.post("/{proposal_id}/accept", response_model=AcceptResponse)
def accept(proposal_id: uuid.UUID, session: Session = Depends(get_session)) -> AcceptResponse:
    """Apply an approved proposal as a new criteria version (LOOP-07).

    A second decision is a 409 — acceptance is a one-way door. An invalid or
    out-of-surface change is a 422 raised before any write.
    """
    proposal = _load(session, proposal_id)
    try:
        version = accept_proposal(session, proposal)
    except ProposalNotPending as exc:
        raise HTTPException(
            status_code=409, detail="Proposal has already been decided."
        ) from exc
    except (ProposalValidationError, UnsupportedEditField) as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc

    session.commit()
    return AcceptResponse(version=version)


@router.post("/{proposal_id}/reject", response_model=RejectResponse)
def reject(
    proposal_id: uuid.UUID,
    body: RejectRequest | None = None,
    session: Session = Depends(get_session),
) -> RejectResponse:
    """Retain a rejected proposal with an optional one-line reason (LOOP-09).

    No confirmation step and no separate "confirm reject" endpoint: 05-UI-SPEC
    specifies a single inline action, and rejecting is never destructive. The
    reason crosses the boundary through ``to_safe_text`` exactly as
    ``user_notes`` does in ``jobs.py``.
    """
    proposal = _load(session, proposal_id)

    reason = body.reason if body is not None else None
    safe_reason = to_safe_text(reason) if reason else None

    try:
        reject_proposal(session, proposal, reason=safe_reason)
    except ProposalNotPending as exc:
        raise HTTPException(
            status_code=409, detail="Proposal has already been decided."
        ) from exc

    session.commit()
    return RejectResponse(status=ProposalStatus.REJECTED)
