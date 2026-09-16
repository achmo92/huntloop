"""The LOOP-07 apply path: an approved proposal becomes a new criteria version.

Nothing else in the system turns a ``CriteriaProposal`` into a live criteria
change, and this module is deliberately thin while being deliberately strict:

- The edit surface is :data:`~huntloop.loop.types.PERMITTED_EDIT_FIELDS`
  (LOOP-03), so a proposal naming anything else is refused before a value is
  read — the rubric is structurally out of reach.
- Every proposed value re-enters through ``CriteriaPayload.model_validate``
  (RESEARCH Pitfall 3) — the same schema that guards a manual form save — so an
  inverted seniority range, a negative or all-zero weight vector, or an invalid
  currency/country code is refused here rather than corrupting a criteria
  version.
- The write goes through the loader's insert-only versioning path and nothing
  else (LOOP-07): a new row at ``max(version)+1`` tagged
  ``CriteriaSource.PROPOSAL_ACCEPTED``, never an UPDATE, never a directly
  constructed ``Criteria`` row.
- A proposal is decided exactly once. A second accept or reject raises
  :class:`ProposalNotPending` and writes nothing.

The caller owns the transaction boundary: both writers flush and return — they
never commit.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from pydantic import ValidationError

from huntloop.criteria import loader
from huntloop.criteria.schema import CriteriaPayload
from huntloop.db.models import CriteriaProposal, CriteriaSource, ProposalStatus
from huntloop.loop.types import PERMITTED_EDIT_FIELDS, UnsupportedEditField

if TYPE_CHECKING:  # pragma: no cover - import-time only, no runtime dependency
    from sqlalchemy.orm import Session

__all__ = [
    "ProposalNotPending",
    "ProposalValidationError",
    "accept_proposal",
    "apply_proposed_change",
    "reject_proposal",
]


class ProposalValidationError(ValueError):
    """The proposed change does not describe a valid criteria payload.

    Raised before any write, so the API can answer with a clear 4xx instead of
    letting a bad value explode inside the versioning writer.
    """


class ProposalNotPending(ValueError):
    """The proposal has already been decided; a decision is a one-way door."""


def _set_leaf(data: dict[str, Any], field: str, value: Any) -> None:
    """Set the (possibly dotted) leaf ``field`` on ``data`` in place."""
    parts = field.split(".")
    target: Any = data
    for part in parts[:-1]:
        target = target[part]
    target[parts[-1]] = value


def apply_proposed_change(
    payload: CriteriaPayload, proposed_changes: Mapping[str, Any]
) -> CriteriaPayload:
    """Return a NEW payload with the single proposed edit applied.

    ``payload`` is never mutated: the change lands on a fresh JSON dump of it,
    and the result round-trips through ``CriteriaPayload`` so every existing
    validator (seniority ordering, weight sign/sum, currency and country codes,
    ``extra="forbid"``) runs against the proposed result.

    Raises :class:`~huntloop.loop.types.UnsupportedEditField` for a field
    outside the enumerated surface and :class:`ProposalValidationError` when the
    edited result violates the schema.
    """
    field = proposed_changes["field"]
    if field not in PERMITTED_EDIT_FIELDS:
        raise UnsupportedEditField(
            f"{field!r} is not a permitted edit field. "
            f"Permitted: {', '.join(PERMITTED_EDIT_FIELDS)}"
        )

    data = payload.model_dump(mode="json")
    _set_leaf(data, field, proposed_changes["proposed_value"])

    try:
        return CriteriaPayload.model_validate(data)
    except ValidationError as exc:
        raise ProposalValidationError(str(exc)) from exc


def accept_proposal(
    session: Session, proposal: CriteriaProposal, *, now: datetime | None = None
) -> int:
    """Apply an approved proposal as a new active criteria version.

    Returns the new version number. Writes through the loader's insert-only
    versioning path tagged ``CriteriaSource.PROPOSAL_ACCEPTED``, then stamps the
    proposal ACCEPTED with the resulting version. Flushes; never commits.
    """
    if proposal.status != ProposalStatus.PENDING:
        raise ProposalNotPending(
            f"Proposal {proposal.id} has already been decided "
            f"({proposal.status.value})."
        )

    active = loader.get_active_criteria(session)
    if active is None:
        raise ProposalValidationError(
            "No active criteria version exists, so there is nothing to apply "
            "this proposal to."
        )
    _current_version, current_payload = active

    new_payload = apply_proposed_change(current_payload, proposal.proposed_changes)

    version = loader.save_new_criteria_version(
        session, new_payload, source=CriteriaSource.PROPOSAL_ACCEPTED
    )

    proposal.status = ProposalStatus.ACCEPTED
    proposal.decided_at = now or datetime.now(UTC)
    proposal.resulting_version = version
    session.flush()
    return version


def reject_proposal(
    session: Session,
    proposal: CriteriaProposal,
    *,
    reason: str | None = None,
    now: datetime | None = None,
) -> None:
    """Record a rejection with an optional one-line reason (LOOP-09 retention).

    The proposal is retained (never deleted) so the same edit signature can be
    surfaced later as "you rejected a similar proposal on [date]". ``reason`` is
    normalised so an empty string becomes NULL — a rejection never requires a
    justification. Flushes; never commits.
    """
    if proposal.status != ProposalStatus.PENDING:
        raise ProposalNotPending(
            f"Proposal {proposal.id} has already been decided "
            f"({proposal.status.value})."
        )

    proposal.status = ProposalStatus.REJECTED
    proposal.decided_at = now or datetime.now(UTC)
    proposal.rejection_reason = reason or None
    session.flush()
