"""LOOP-03/04/05 pattern detection. Owned by plan 05-02.

Detection is pure Python over rows that already exist (``StatusEvent``,
``Job.score_dimensions``, ``Job.score_flags``) — no model call (D-06). Three
parallel tracks produce ungated ``EditCandidate``s:

- Track A attributes each fast rejection to its lowest-scoring dimension and
  proposes raising that dimension's weight.
- Track B maps a raised advisory flag straight onto the criteria field it
  implies, tightening it by the smallest step that addresses the evidence.
- Track C finds a title token or employer name that repeats across fast
  rejections and never appears among progressed listings.

``gate`` then drops anything below its own per-field minimum (LOOP-04) and
``build_evidence`` names the exact listings and transitions behind a candidate
(LOOP-05).
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

from huntloop.criteria.schema import CompensationFloor, DimensionWeights
from huntloop.db.models import CompPeriod, JobStatus
from huntloop.loop.detect import (
    EVIDENCE_LISTING_CAP,
    attribute_dimension,
    build_evidence,
    detect_candidates,
    gate,
)
from huntloop.loop.types import (
    MIN_OBSERVATIONS,
    PERMITTED_EDIT_FIELDS,
    EditDirection,
    Signal,
)
from tests.loop.factories import (  # noqa: F401  (loop_session is a pytest fixture)
    default_payload,
    loop_session,
    make_company,
    make_criteria,
    make_job,
    make_transitions,
)

FIRST_SEEN = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)

_FLAG_NAMES = (
    "stretch_role",
    "step_down",
    "language_requirement",
    "location_ambiguity",
    "comp_below_floor",
    "posting_stale",
)

# role_fit is the numerically lowest dimension, so every job scored this way
# attributes to role_fit in Track A.
ROLE_FIT_LOW = {
    "role_fit": 1.0,
    "seniority_fit": 4.0,
    "employer_fit": 4.0,
    "trajectory": 4.0,
}


def _score_flags(*raised: str) -> dict[str, dict]:
    """A full six-key score_flags payload with only ``raised`` set true."""
    return {name: {"raised": name in raised, "detail": ""} for name in _FLAG_NAMES}


def _floor(amount: str) -> CompensationFloor:
    """A USD/annual compensation floor at ``amount``."""
    return CompensationFloor(
        amount=Decimal(amount), currency="USD", period="annual"
    )


def _fast_reject(
    session,
    *,
    title,
    company,
    dimensions=None,
    flags=None,
    comp_min=None,
    comp_currency=None,
    comp_period=None,
):
    """Insert one job rejected 3 days after first being seen (a fast reject)."""
    company_row = make_company(session, name=company)
    job = make_job(
        session,
        company=company_row,
        title=title,
        first_seen_at=FIRST_SEEN,
        comp_min=comp_min,
        comp_currency=comp_currency,
        comp_period=comp_period,
        score_dimensions=dimensions,
        score_flags=flags,
    )
    make_transitions(
        session,
        job,
        [(JobStatus.NEW, JobStatus.REJECTED)],
        start=FIRST_SEEN + timedelta(days=3),
    )
    return job


def _fast_reject_batch(session, count, *, dimensions=None, flags=None, prefix="A"):
    """``count`` fast rejections with unique titles and companies.

    Titles carry a single unique token each and companies are distinct, so a
    batch never accidentally qualifies for Track C.
    """
    return [
        _fast_reject(
            session,
            title=f"Tok{prefix}{index}xyz",
            company=f"Employer {prefix}{index}",
            dimensions=dimensions,
            flags=flags,
        )
        for index in range(count)
    ]


def _progressed(session, *, title, company):
    """Insert one job that reached APPLIED — a PROGRESSED signal."""
    company_row = make_company(session, name=company)
    job = make_job(session, company=company_row, title=title, first_seen_at=FIRST_SEEN)
    make_transitions(
        session,
        job,
        [
            (None, JobStatus.NEW),
            (JobStatus.NEW, JobStatus.SHORTLISTED),
            (JobStatus.SHORTLISTED, JobStatus.APPLIED),
        ],
        start=FIRST_SEEN,
    )
    return job


def test_only_enumerated_fields(loop_session):
    """No candidate may name a field outside the LOOP-03 enumerated surface."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 8, dimensions=ROLE_FIT_LOW)
    _fast_reject_batch(loop_session, 4, flags=_score_flags("stretch_role"), prefix="B")
    for index, word in enumerate(("Alpha", "Bravo", "Charlie")):
        _fast_reject(
            loop_session, title=f"Kubernetes {word}", company=f"Kube Co {index}"
        )

    fields = {candidate.field for candidate in detect_candidates(loop_session)}

    assert fields, "expected the fixture backlog to produce candidates"
    assert fields <= set(PERMITTED_EDIT_FIELDS)
    assert {"dimension_weights", "seniority_max", "exclusions.title_keywords"} <= fields
    assert "profile_summary" not in fields
    assert not any(field.startswith("scoring") for field in fields)


def test_threshold_gating(loop_session):
    """A candidate below its own per-field minimum never becomes a proposal."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 4, dimensions=ROLE_FIT_LOW)

    candidates = detect_candidates(loop_session)
    weights = [candidate for candidate in candidates if candidate.field == "dimension_weights"]
    assert len(weights) == 1
    assert weights[0].observation_count == 4
    assert weights[0].threshold == MIN_OBSERVATIONS["dimension_weights"]
    assert gate(candidates) == []

    _fast_reject_batch(loop_session, 4, dimensions=ROLE_FIT_LOW, prefix="C")

    gated = gate(detect_candidates(loop_session))
    assert len(gated) == 1
    assert gated[0].field == "dimension_weights"
    assert gated[0].observation_count == 8


def test_evidence_shape(loop_session):
    """Evidence is a JSON-safe, six-key dict naming each supporting listing."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 8, dimensions=ROLE_FIT_LOW)

    candidate = next(
        candidate
        for candidate in detect_candidates(loop_session)
        if candidate.field == "dimension_weights"
    )
    evidence = build_evidence(candidate)

    assert set(evidence) == {
        "signal",
        "observation_count",
        "threshold",
        "listings",
        "feedback_quotes",
        "truncated",
    }
    assert evidence["signal"] == Signal.FAST_REJECT.value
    assert evidence["threshold"] == MIN_OBSERVATIONS["dimension_weights"]
    assert evidence["observation_count"] == 8
    assert evidence["truncated"] is False
    assert evidence["feedback_quotes"] == []
    for listing in evidence["listings"]:
        assert set(listing) == {
            "job_id",
            "title",
            "company",
            "status",
            "dwell_days",
            "transitions",
            "attributed_to",
        }
        assert isinstance(listing["job_id"], str)
        assert listing["status"] == JobStatus.REJECTED.value
    assert json.loads(json.dumps(evidence)) == evidence


def test_evidence_listings_are_capped(loop_session):
    """The listing list is capped while the true count is still reported."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 25, dimensions=ROLE_FIT_LOW)

    candidate = next(
        candidate
        for candidate in detect_candidates(loop_session)
        if candidate.field == "dimension_weights"
    )
    evidence = build_evidence(candidate)

    assert evidence["observation_count"] == 25
    assert len(evidence["listings"]) == EVIDENCE_LISTING_CAP == 10
    assert evidence["truncated"] is True


def test_attribute_dimension_picks_lowest(loop_session):
    """D-03: the numerically lowest dimension is the prime suspect."""
    company = make_company(loop_session, name="Attribution Co")
    job = make_job(
        loop_session,
        company=company,
        title="Attribution Role",
        score_dimensions={
            "role_fit": 4.0,
            "seniority_fit": 1.0,
            "employer_fit": 3.0,
            "trajectory": 3.0,
        },
    )

    assert attribute_dimension(job) == "seniority_fit"


def test_attribute_dimension_none_when_unscored(loop_session):
    """An unscored job attributes to nothing and never enters Track A."""
    make_criteria(loop_session, default_payload())
    jobs = _fast_reject_batch(loop_session, 8)

    assert all(attribute_dimension(job) is None for job in jobs)
    assert [
        candidate
        for candidate in detect_candidates(loop_session)
        if candidate.field == "dimension_weights"
    ] == []


def test_track_b_stretch_role_tightens_seniority_max(loop_session):
    """A repeated stretch_role flag lowers the seniority ceiling one rung."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 4, flags=_score_flags("stretch_role"))

    candidate = next(
        candidate
        for candidate in detect_candidates(loop_session)
        if candidate.field == "seniority_max"
    )

    assert candidate.direction is EditDirection.TIGHTEN
    assert candidate.current_value == "staff"
    assert candidate.proposed_value == "senior"


def test_track_b_never_crosses_min(loop_session):
    """Tightening may never push seniority_max below seniority_min."""
    make_criteria(
        loop_session, default_payload(seniority_min="senior", seniority_max="senior")
    )
    _fast_reject_batch(loop_session, 4, flags=_score_flags("stretch_role"))

    fields = {candidate.field for candidate in detect_candidates(loop_session)}

    assert "seniority_max" not in fields


def test_track_b_comp_floor_uses_median(loop_session):
    """The new floor is the median comp_min of the flagged, comparable listings."""
    make_criteria(loop_session, default_payload(compensation_floor=_floor("100000")))
    for index, amount in enumerate(("120000", "130000", "140000", "150000")):
        _fast_reject(
            loop_session,
            title=f"TokA{index}xyz",
            company=f"Employer A{index}",
            flags=_score_flags("comp_below_floor"),
            comp_min=Decimal(amount),
            comp_currency="USD",
            comp_period=CompPeriod.ANNUAL,
        )

    candidate = next(
        candidate
        for candidate in detect_candidates(loop_session)
        if candidate.field == "compensation_floor"
    )

    assert candidate.direction is EditDirection.TIGHTEN
    assert Decimal(str(candidate.proposed_value["amount"])) == Decimal("135000")
    assert candidate.proposed_value["currency"] == "USD"
    assert candidate.proposed_value["period"] == "annual"


def test_track_b_comp_floor_skips_mixed_currency(loop_session):
    """Never compare across currencies — a mixed set produces no proposal."""
    make_criteria(loop_session, default_payload(compensation_floor=_floor("100000")))
    for index, (amount, currency) in enumerate(
        (("120000", "USD"), ("130000", "USD"), ("140000", "USD"), ("150000", "EUR"))
    ):
        _fast_reject(
            loop_session,
            title=f"TokA{index}xyz",
            company=f"Employer A{index}",
            flags=_score_flags("comp_below_floor"),
            comp_min=Decimal(amount),
            comp_currency=currency,
            comp_period=CompPeriod.ANNUAL,
        )

    fields = {candidate.field for candidate in detect_candidates(loop_session)}

    assert "compensation_floor" not in fields


def test_track_b_posting_age_tightens(loop_session):
    """Repeated stale postings shrink the accepted posting age."""
    make_criteria(loop_session, default_payload(posting_age_days=30))
    _fast_reject_batch(loop_session, 5, flags=_score_flags("posting_stale"))

    candidate = next(
        candidate
        for candidate in detect_candidates(loop_session)
        if candidate.field == "posting_age_days"
    )

    assert candidate.direction is EditDirection.TIGHTEN
    assert candidate.current_value == 30
    assert candidate.proposed_value == 20


def test_unmapped_flags_produce_nothing(loop_session):
    """location_ambiguity and language_requirement are never auto-mapped."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(
        loop_session,
        10,
        flags=_score_flags("location_ambiguity", "language_requirement"),
    )

    assert detect_candidates(loop_session) == []


def test_track_c_title_keyword_requires_absence_from_progressed(loop_session):
    """A token repeated in fast rejects is excluded only if it never progressed."""
    make_criteria(loop_session, default_payload())
    for index, word in enumerate(("Quokka", "Narwhal", "Pangolin")):
        _fast_reject(
            loop_session, title=f"Staff {word}", company=f"Staff Co {index}"
        )

    fields = {candidate.field for candidate in detect_candidates(loop_session)}
    assert "exclusions.title_keywords" in fields

    _progressed(loop_session, title="Staff Otter", company="Progressed Co")

    fields = {candidate.field for candidate in detect_candidates(loop_session)}
    assert "exclusions.title_keywords" not in fields


def test_track_a_proposed_weights_validate(loop_session):
    """The proposed weights dict is a valid DimensionWeights and moves one key."""
    make_criteria(loop_session, default_payload())
    _fast_reject_batch(loop_session, 8, dimensions=ROLE_FIT_LOW)

    candidate = next(
        candidate
        for candidate in detect_candidates(loop_session)
        if candidate.field == "dimension_weights"
    )

    DimensionWeights.model_validate(candidate.proposed_value)
    changed = [
        key
        for key in candidate.current_value
        if candidate.current_value[key] != candidate.proposed_value[key]
    ]
    assert changed == ["role_fit"]


def test_no_active_criteria_returns_empty(loop_session):
    """No active criteria row is an empty result, never an exception."""
    company = make_company(loop_session, name="No Criteria Co")
    job = make_job(
        loop_session, company=company, title="Tok0xyz", first_seen_at=FIRST_SEEN
    )
    make_transitions(
        loop_session,
        job,
        [(JobStatus.NEW, JobStatus.REJECTED)],
        start=FIRST_SEEN + timedelta(days=3),
    )

    assert detect_candidates(loop_session) == []
