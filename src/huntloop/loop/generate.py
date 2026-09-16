"""D-06: the discovery run's cheap final step.

D-06 makes run cadence the throttle — no scheduler, no new infrastructure, and
no model call unless a threshold was actually crossed. This module is that
final step, and it runs over **already-stored data** at the tail of
``run_discovery``: ``StatusEvent`` history, ``Job.score_dimensions``,
``Job.score_flags`` and the user's unconsumed ``FeedbackNote`` rows.

Pipeline, in order:

1. An active criteria version must exist, or there is nothing to generate
   against and the pass returns immediately.
2. :func:`huntloop.loop.detect.detect_candidates` — ungated, model-free.
3. The qualifying general feedback notes are read in one query.
4. :func:`huntloop.loop.rationale.extract_feedback_claims` — the ONLY model call
   in the pass, and it is skipped unless there is unconsumed feedback (D-06).
5. Claims are folded onto candidates by ``(field, direction)`` as
   ``feedback_quotes``. A claim with no matching candidate is discarded: v1
   never lets the model author a ``proposed_value``, only corroborate one.
6. :func:`huntloop.loop.detect.gate` — LOOP-04. Everything below its own
   threshold stops here, which is why explicit feedback counts *toward* the
   gate rather than around it (D-08).
7. D-10 dedup by edit signature: a PENDING proposal is never duplicated, a
   REJECTED one returns only on strictly stronger evidence, an ACCEPTED one
   never blocks.
8. For each survivor: proposed changes, evidence (LOOP-05), predicted effect
   (LOOP-06) and a rationale — model-narrated when a client exists, otherwise
   the deterministic fallback.

The whole pass is one transaction: a single commit, or a rollback that leaves
nothing behind. ``run_discovery`` calls this inside its own try/except, so a
failure here is logged and cannot change a successful run's outcome.

D-06 restated: steps 1-3 and 6-8 are model-free; step 4 is the only model call
and it is skipped unless there is unconsumed feedback.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass, replace
from datetime import datetime
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.criteria.loader import get_active_criteria
from huntloop.db.models import (
    Criteria,
    CriteriaProposal,
    FeedbackNote,
    FeedbackSource,
    ProposalStatus,
)
from huntloop.loop.detect import build_evidence, detect_candidates, gate
from huntloop.loop.predicted_effect import predict_effect
from huntloop.loop.rationale import extract_feedback_claims, synthesize_rationale
from huntloop.loop.types import (
    UnsupportedEditField,
    build_proposed_changes,
    edit_signature,
)

logger = logging.getLogger(__name__)

__all__ = ["generate_proposals"]

#: An edit signature is ``(field, direction)`` — the D-10 dedup key, computable
#: from ``proposed_changes`` alone so no extra column is needed.
Signature = tuple[str, str]


@dataclass(frozen=True)
class _StoredHistory:
    """What the review surface already knows, indexed by edit signature."""

    #: A pending proposal with this signature already awaits the user.
    pending: set[Signature]
    #: The observation count behind the most recent rejection per signature.
    rejected: dict[Signature, int]

    def blocks(self, signature: Signature, observation_count: int) -> bool:
        """D-10: whether this history should suppress a new candidate."""
        if signature in self.pending:
            return True
        previous = self.rejected.get(signature)
        if previous is None:
            return False
        # Strictly greater, or an identical case would silently resurface.
        return observation_count <= previous


# ---------------------------------------------------------------------------
# Feedback intake (D-08)
# ---------------------------------------------------------------------------


def _unconsumed_notes(session: Session, criteria_version: int) -> list[FeedbackNote]:
    """The general feedback that appeared after the active criteria version.

    Two filters, both structural rather than heuristic:

    - ``job_id IS NULL`` and ``source == CHAT``: this is the general feedback
      sink (D-07). A per-listing note is TRAK-02's, and belongs to that listing.
    - ``created_at >= <active criteria created_at>``: feedback older than the
      criteria the user is currently running was already reflected in it (or
      deliberately superseded), so it is presumed consumed. The version's own
      timestamp is the migration-free boundary (05-RESEARCH open question 4).
    """
    active_row = session.execute(
        select(Criteria).where(Criteria.version == criteria_version)
    ).scalar_one_or_none()
    if active_row is None:
        return []

    rows = session.execute(
        select(FeedbackNote)
        .where(
            FeedbackNote.job_id.is_(None),
            FeedbackNote.source == FeedbackSource.CHAT,
            FeedbackNote.created_at >= active_row.created_at,
        )
        .order_by(FeedbackNote.created_at.asc())
    ).scalars()
    return list(rows)


def _fold_claims(
    candidates: list[Any],
    claims: list[dict[str, str]],
) -> list[Any]:
    """Attach each claim to the candidate it corroborates, dropping the rest.

    ``EditCandidate`` is frozen, so matching candidates are replaced rather than
    mutated. A claim whose ``(field, direction)`` matches no detected candidate
    is logged and discarded — the model corroborates a proposed value, it never
    authors one.
    """
    if not claims:
        return candidates

    index: dict[Signature, list[int]] = defaultdict(list)
    for position, candidate in enumerate(candidates):
        index[(candidate.field, candidate.direction.value)].append(position)

    folded = list(candidates)
    for claim in claims:
        key = (str(claim.get("field")), str(claim.get("direction")))
        positions = index.get(key)
        if not positions:
            logger.info(
                "discarding feedback claim for %s: no detected candidate to corroborate", key
            )
            continue
        for position in positions:
            candidate = folded[position]
            folded[position] = replace(
                candidate,
                feedback_quotes=[*candidate.feedback_quotes, dict(claim)],
            )

    return folded


# ---------------------------------------------------------------------------
# D-10 dedup
# ---------------------------------------------------------------------------


def _stored_history(session: Session) -> _StoredHistory:
    """Index every existing proposal by edit signature, oldest first.

    Historical rows are never trusted to be well-formed: a signature that raises
    (an unenumerated field, a missing key, a non-mapping payload) is skipped, so
    old data can never break generation.
    """
    pending: set[Signature] = set()
    rejected: dict[Signature, int] = {}

    rows = session.execute(
        select(CriteriaProposal).order_by(CriteriaProposal.created_at.asc())
    ).scalars()

    for row in rows:
        changes = row.proposed_changes
        if not isinstance(changes, dict):
            continue
        try:
            signature = edit_signature(changes)
        except (UnsupportedEditField, KeyError, TypeError):
            logger.warning("skipping proposal with unusable signature: %r", changes)
            continue

        if row.status is ProposalStatus.PENDING:
            pending.add(signature)
        elif row.status is ProposalStatus.REJECTED:
            evidence = row.evidence if isinstance(row.evidence, dict) else {}
            rejected[signature] = int(evidence.get("observation_count") or 0)

    return _StoredHistory(pending=pending, rejected=rejected)


# ---------------------------------------------------------------------------
# The pass
# ---------------------------------------------------------------------------


def generate_proposals(
    session: Session,
    *,
    run_id: UUID | None = None,
    llm_client: Any | None = None,
    now: datetime | None = None,
) -> list[UUID]:
    """Turn stored history plus unconsumed feedback into PENDING proposals.

    Returns the ids of the proposals created by this pass — an empty list is the
    normal, cheap outcome. Owns its transaction: exactly one commit, or a
    rollback that leaves nothing behind.
    """
    active = get_active_criteria(session)
    if active is None:
        return []
    criteria_version, _criteria = active

    # 2. Detected candidates are ungated: LOOP-04 is `gate` below.
    candidates = detect_candidates(session, now=now)

    # 3-4. One extraction call, and only when there is something to read.
    notes = _unconsumed_notes(session, criteria_version)
    claims = extract_feedback_claims(llm_client, notes=notes) if notes and llm_client else []

    # 5. Claims corroborate detected candidates; they never author one.
    candidates = _fold_claims(candidates, claims)

    # 6. LOOP-04.
    gated = gate(candidates)
    if not gated:
        return []

    # 7. D-10.
    history = _stored_history(session)

    created: list[CriteriaProposal] = []
    try:
        for candidate in gated:
            signature: Signature = (candidate.field, candidate.direction.value)
            if history.blocks(signature, candidate.observation_count):
                logger.info(
                    "suppressing %s: an equivalent proposal is pending or was "
                    "rejected on no weaker evidence",
                    signature,
                )
                continue

            changes = build_proposed_changes(
                candidate.field,
                candidate.direction,
                candidate.current_value,
                candidate.proposed_value,
            )
            evidence = build_evidence(candidate)
            predicted_effect = predict_effect(session, changes, now=now)
            rationale = synthesize_rationale(
                llm_client,
                candidate=candidate,
                evidence=evidence,
                predicted_effect=predicted_effect,
            )

            proposal = CriteriaProposal(
                based_on_run_id=run_id,
                proposed_changes=changes,
                rationale=rationale,
                evidence=evidence,
                predicted_effect=predicted_effect,
                status=ProposalStatus.PENDING,
            )
            session.add(proposal)
            created.append(proposal)

        # 9. All-or-nothing: one flush to mint ids, one commit for the pass.
        session.flush()
        created_ids = [proposal.id for proposal in created]
        session.commit()
    except Exception:
        session.rollback()
        raise

    return created_ids
