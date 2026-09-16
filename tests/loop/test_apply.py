"""LOOP-07 apply path: accept/reject a proposal, validate, version, never UPDATE.

Owned by plan 05-04. A ``CriteriaProposal`` becomes a live criteria version only
through ``huntloop.loop.apply``: the proposed value re-enters through
``CriteriaPayload.model_validate`` (RESEARCH Pitfall 3) and the write goes
through ``save_new_criteria_version`` tagged ``PROPOSAL_ACCEPTED`` (insert-only,
never an UPDATE). A proposal is decided exactly once.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import func, select

from huntloop.criteria.loader import get_active_criteria
from huntloop.criteria.schema import CriteriaPayload, DimensionWeights
from huntloop.db.models import (
    Criteria,
    CriteriaProposal,
    CriteriaSource,
    ProposalStatus,
)
from huntloop.loop.apply import (
    ProposalNotPending,
    ProposalValidationError,
    accept_proposal,
    apply_proposed_change,
)
from huntloop.loop.types import UnsupportedEditField
from tests.loop.factories import (  # noqa: F401  (fixture re-export)
    default_payload,
    loop_session,
    make_criteria,
)


def _make_proposal(
    session,
    *,
    field: str = "posting_age_days",
    direction: str = "tighten",
    current_value=30,
    proposed_value=20,
    status: ProposalStatus = ProposalStatus.PENDING,
) -> CriteriaProposal:
    """Insert a proposal with one proposed change and flush (never commit)."""
    proposal = CriteriaProposal(
        proposed_changes={
            "field": field,
            "direction": direction,
            "current_value": current_value,
            "proposed_value": proposed_value,
        },
        rationale="stale postings keep getting rejected",
        status=status,
    )
    session.add(proposal)
    session.flush()
    return proposal


# ---------------------------------------------------------------------------
# apply_proposed_change — single-field edit, re-validated through the schema
# ---------------------------------------------------------------------------


def test_apply_validates_through_criteria_payload():
    result = apply_proposed_change(
        default_payload(),
        {"field": "posting_age_days", "proposed_value": 20},
    )
    assert isinstance(result, CriteriaPayload)
    assert not isinstance(result, dict)


def test_apply_sets_scalar_field():
    payload = default_payload()
    result = apply_proposed_change(
        payload, {"field": "posting_age_days", "proposed_value": 20}
    )
    assert result.posting_age_days == 20

    before = payload.model_dump(mode="json")
    after = result.model_dump(mode="json")
    untouched_before = {k: v for k, v in before.items() if k != "posting_age_days"}
    untouched_after = {k: v for k, v in after.items() if k != "posting_age_days"}
    assert untouched_after == untouched_before


def test_apply_sets_nested_field():
    payload = default_payload()
    result = apply_proposed_change(
        payload,
        {"field": "exclusions.title_keywords", "proposed_value": ["contractor"]},
    )
    assert result.exclusions.title_keywords == ["contractor"]
    assert result.exclusions.employers == payload.exclusions.employers


def test_apply_sets_model_field():
    payload = default_payload()
    result = apply_proposed_change(
        payload,
        {
            "field": "dimension_weights",
            "proposed_value": {
                "role_fit": 4.0,
                "seniority_fit": 1.0,
                "employer_fit": 1.0,
                "trajectory": 1.0,
            },
        },
    )
    assert isinstance(result.dimension_weights, DimensionWeights)
    assert result.dimension_weights.role_fit == 4.0
    assert result.dimension_weights.seniority_fit == 1.0


def test_apply_rejects_unpermitted_field():
    with pytest.raises(UnsupportedEditField):
        apply_proposed_change(
            default_payload(),
            {"field": "profile_summary", "proposed_value": "rewritten"},
        )


def test_apply_rejects_invalid_result():
    payload = default_payload(seniority_min="senior", seniority_max="staff")
    with pytest.raises(ProposalValidationError):
        apply_proposed_change(
            payload, {"field": "seniority_max", "proposed_value": "junior"}
        )
    # The refusal left the caller's payload untouched.
    assert payload.seniority_max == "staff"


def test_apply_does_not_mutate_input():
    payload = default_payload()
    snapshot = payload.model_dump(mode="json")

    apply_proposed_change(payload, {"field": "posting_age_days", "proposed_value": 20})

    assert payload.posting_age_days == 30
    assert payload.model_dump(mode="json") == snapshot


# ---------------------------------------------------------------------------
# accept_proposal — the only write path to a new criteria version
# ---------------------------------------------------------------------------


def test_accept_proposal_creates_new_version(loop_session):
    session = loop_session
    previous = make_criteria(session)
    proposal = _make_proposal(session)

    version = accept_proposal(session, proposal)

    assert version > previous
    active = get_active_criteria(session)
    assert active is not None
    assert active[0] == version


def test_accept_proposal_source_is_proposal_accepted(loop_session):
    session = loop_session
    make_criteria(session)
    proposal = _make_proposal(session)

    version = accept_proposal(session, proposal)

    row = session.execute(
        select(Criteria).where(Criteria.version == version)
    ).scalar_one()
    assert row.source == CriteriaSource.PROPOSAL_ACCEPTED


def test_accept_proposal_stamps_the_proposal(loop_session):
    session = loop_session
    make_criteria(session)
    proposal = _make_proposal(session)
    stamp = datetime(2026, 9, 16, 12, 0, tzinfo=UTC)

    version = accept_proposal(session, proposal, now=stamp)

    assert proposal.status == ProposalStatus.ACCEPTED
    assert proposal.decided_at == stamp
    assert proposal.resulting_version == version


def test_accept_proposal_preserves_prior_version(loop_session):
    session = loop_session
    previous = make_criteria(session)
    prior_row = session.execute(
        select(Criteria).where(Criteria.version == previous)
    ).scalar_one()
    prior_payload = prior_row.payload
    proposal = _make_proposal(session)

    accept_proposal(session, proposal)
    session.flush()

    still_there = session.execute(
        select(Criteria).where(Criteria.version == previous)
    ).scalar_one()
    assert still_there.payload == prior_payload
    assert still_there.is_active is False


def test_accept_non_pending_raises(loop_session):
    session = loop_session
    previous = make_criteria(session)
    rejected = _make_proposal(session, status=ProposalStatus.REJECTED)
    accepted = _make_proposal(session, status=ProposalStatus.ACCEPTED)

    with pytest.raises(ProposalNotPending):
        accept_proposal(session, rejected)
    with pytest.raises(ProposalNotPending):
        accept_proposal(session, accepted)

    # Neither refusal wrote a new version.
    assert get_active_criteria(session)[0] == previous
    assert session.execute(select(func.count()).select_from(Criteria)).scalar_one() == 1


def test_accept_with_no_active_criteria_raises(loop_session):
    session = loop_session
    proposal = _make_proposal(session)

    with pytest.raises(ProposalValidationError):
        accept_proposal(session, proposal)

    assert session.execute(select(func.count()).select_from(Criteria)).scalar_one() == 0
