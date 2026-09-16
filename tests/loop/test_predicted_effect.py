"""LOOP-06 predicted effect per field. Owned by plan 05-03."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("huntloop.loop.predicted_effect")

from sqlalchemy import select  # noqa: E402

from huntloop.criteria.schema import (  # noqa: E402
    CompensationFloor,
    DimensionWeights,
    LocationCriteria,
    WorkAuthorization,
)
from huntloop.db.models import CompPeriod, Job, JobStatus  # noqa: E402
from huntloop.loop import predicted_effect as pe  # noqa: E402
from huntloop.loop.types import (  # noqa: E402
    PERMITTED_EDIT_FIELDS,
    EditDirection,
    UnsupportedEditField,
    build_proposed_changes,
    resolve_field,
)
from tests.loop.factories import default_payload, loop_session  # noqa: E402, F401
from tests.loop.factories import make_company, make_job  # noqa: E402

# A fixed clock so posting-age arithmetic is deterministic regardless of when
# the suite runs. ``make_job``/``UTCDateTime`` normalise everything to UTC.
FIXED_NOW = datetime(2026, 9, 16, 12, 0, 0, tzinfo=UTC)

CURRENT_WEIGHTS = {
    "role_fit": 3.0,
    "seniority_fit": 2.0,
    "employer_fit": 2.0,
    "trajectory": 1.0,
}
PROPOSED_WEIGHTS = {
    "role_fit": 4.0,
    "seniority_fit": 2.0,
    "employer_fit": 2.0,
    "trajectory": 1.0,
}

#: The only payload shapes the dispatcher may ever hand to the review surface.
KNOWN_KINDS = frozenset(
    {"filter_dry_run", "flag_recompute", "literal_count", "score_recompute"}
)


def _days_ago(days: float) -> datetime:
    return FIXED_NOW - timedelta(days=days)


def _age_backlog(session, ages: list[float]):
    """Insert one live job per posting age and return their company."""
    company = make_company(session, name="Age Co")
    for index, age in enumerate(ages):
        make_job(
            session,
            company=company,
            title=f"Engineer {index}",
            posted_at=_days_ago(age),
        )
    return company


def _score_dimensions(role_fit: float) -> dict[str, float]:
    """A flat dimension-score dict: role_fit varies, everything else is high."""
    return {
        "role_fit": role_fit,
        "seniority_fit": 5.0,
        "employer_fit": 5.0,
        "trajectory": 5.0,
    }


def _age_payload(session, current: int, proposed: int) -> dict:
    """The Tier A payload for a ``posting_age_days`` proposal."""
    return pe.predict_filter_change(
        session,
        field="posting_age_days",
        current_value=current,
        proposed_value=proposed,
        now=FIXED_NOW,
    )


def _weights_payload(session) -> dict:
    """The Tier D payload for a ``role_fit`` 3.0 -> 4.0 proposal."""
    return pe.predict_score_change(
        session,
        current_value=CURRENT_WEIGHTS,
        proposed_value=PROPOSED_WEIGHTS,
    )


# ---------------------------------------------------------------------------
# Tier A — deterministic filter dry-run
# ---------------------------------------------------------------------------


def test_tier_a_posting_age_counts_exclusions(loop_session):
    _age_backlog(loop_session, [1, 5, 9, 12, 15, 18, 25, 35, 40, 60])

    payload = _age_payload(loop_session, 30, 20)

    assert payload["kind"] == "filter_dry_run"
    assert payload["field"] == "posting_age_days"
    assert payload["backlog_size"] == 10
    # Only the 25-day posting changes verdict (PASS -> DROP). The 35/40/60-day
    # postings were already excluded at 30 days, so they are not *newly*
    # excluded — would_exclude is a verdict delta, not the total DROP set.
    assert payload["would_exclude"] == 1
    assert payload["would_include"] == 0
    assert payload["approximate"] is False
    assert json.loads(json.dumps(payload)) == payload


def test_tier_a_loosening_counts_inclusions(loop_session):
    _age_backlog(loop_session, [1, 5, 9, 12, 15, 18, 25, 35, 40, 60])

    payload = _age_payload(loop_session, 30, 45)

    # 35d and 40d come back in; 60d was and remains excluded.
    assert payload["would_include"] == 2
    assert payload["would_exclude"] == 0


def test_tier_a_excludes_rejected_and_withdrawn_jobs(loop_session):
    company = make_company(loop_session, name="History Co")
    make_job(loop_session, company=company, title="Live", posted_at=_days_ago(25))
    make_job(
        loop_session,
        company=company,
        title="Rejected",
        status=JobStatus.REJECTED,
        posted_at=_days_ago(25),
    )
    make_job(
        loop_session,
        company=company,
        title="Withdrawn",
        status=JobStatus.WITHDRAWN,
        posted_at=_days_ago(25),
    )

    payload = _age_payload(loop_session, 30, 20)

    assert payload["backlog_size"] == 1
    assert payload["would_exclude"] == 1
    assert payload["would_include"] == 0


def test_tier_a_geography_uses_persisted_columns_only(loop_session, monkeypatch):
    company = make_company(loop_session, name="Geo Co")
    job = make_job(
        loop_session,
        company=company,
        title="Backend Engineer",
        location_normalized="Berlin, DE",
        is_remote=False,
    )

    captured: dict = {}
    real_normalize = pe.normalize_location

    def _spy(raw, **kwargs):
        captured["raw"] = raw
        captured["kwargs"] = kwargs
        return real_normalize(raw, **kwargs)

    monkeypatch.setattr(pe, "normalize_location", _spy)

    payload = pe.predict_filter_change(
        loop_session,
        field="locations",
        current_value={
            "eligible_countries": ["US"],
            "eligible_regions": [],
            "preferred_cities": [],
        },
        proposed_value={
            "eligible_countries": ["DE"],
            "eligible_regions": [],
            "preferred_cities": [],
        },
    )

    # RESEARCH Pitfall 2: the platform hints are not persisted on Job, so the
    # only hint this reconstruction may pass is the remote flag.
    assert captured["raw"] == "Berlin, DE"
    assert set(captured["kwargs"]) == {"platform_is_remote"}
    assert captured["kwargs"]["platform_is_remote"] is False

    assert payload["approximate"] is True
    # Berlin drops under the US-only criteria and comes back under DE.
    assert payload["would_include"] == 1
    assert payload["would_exclude"] == 0
    assert payload["sample"][0]["job_id"] == str(job.id)


def test_tier_a_sample_is_capped_and_json_safe(loop_session):
    _age_backlog(loop_session, [25] * 20)

    payload = _age_payload(loop_session, 30, 20)

    assert payload["would_exclude"] == 20
    assert len(payload["sample"]) == pe.SAMPLE_CAP
    for entry in payload["sample"]:
        assert set(entry) == {"job_id", "title", "reason"}
        assert isinstance(entry["job_id"], str)
        assert isinstance(entry["title"], str)
        assert isinstance(entry["reason"], str)
    assert json.loads(json.dumps(payload)) == payload


# ---------------------------------------------------------------------------
# Tier D — in-memory score recompute
# ---------------------------------------------------------------------------


def test_tier_d_counts_affected_and_deltas(loop_session):
    company = make_company(loop_session, name="Scored Co")
    for index, role_fit in enumerate([1.0, 1.0, 1.0, 3.0, 3.0, 3.0]):
        make_job(
            loop_session,
            company=company,
            title=f"Scored {index}",
            score_dimensions=_score_dimensions(role_fit),
            score_overall=Decimal("3.50") if role_fit == 1.0 else Decimal("4.25"),
        )

    payload = _weights_payload(loop_session)

    assert payload["kind"] == "score_recompute"
    assert payload["field"] == "dimension_weights"
    assert payload["backlog_size"] == 6
    assert payload["affected"] == 6
    assert payload["skipped"] == 0
    assert isinstance(payload["mean_delta"], float)
    assert isinstance(payload["max_delta"], float)
    # role_fit 1.0 -> 3.50 becomes 3.22 (-0.28); role_fit 3.0 -> 4.25 becomes
    # 4.11 (-0.14). mean = (-0.28*3 + -0.14*3)/6 = -0.21.
    assert payload["mean_delta"] == pytest.approx(-0.21)
    assert payload["max_delta"] == pytest.approx(-0.28)
    assert json.loads(json.dumps(payload)) == payload


def test_tier_d_writes_nothing(loop_session):
    company = make_company(loop_session, name="Untouched Co")
    for index in range(3):
        make_job(
            loop_session,
            company=company,
            title=f"Frozen {index}",
            score_dimensions=_score_dimensions(1.0),
            score_overall=Decimal("3.50"),
        )

    def _snapshot() -> dict:
        return {
            job.id: job.score_overall
            for job in loop_session.execute(select(Job)).scalars().all()
        }

    before = _snapshot()

    _weights_payload(loop_session)

    assert not loop_session.dirty
    assert not loop_session.new

    loop_session.expire_all()
    assert _snapshot() == before


def test_tier_d_skips_unscoreable_jobs(loop_session):
    company = make_company(loop_session, name="Partial Co")
    make_job(loop_session, company=company, title="Unscored", score_dimensions=None)
    make_job(
        loop_session,
        company=company,
        title="All null",
        score_dimensions={
            "role_fit": None,
            "seniority_fit": None,
            "employer_fit": None,
            "trajectory": None,
        },
    )
    make_job(
        loop_session,
        company=company,
        title="Scored",
        score_dimensions=_score_dimensions(1.0),
        score_overall=Decimal("3.50"),
    )

    payload = _weights_payload(loop_session)

    assert payload["backlog_size"] == 3
    assert payload["skipped"] == 2
    assert payload["affected"] == 1


def test_tier_d_signs_are_preserved(loop_session):
    company = make_company(loop_session, name="Down Co")
    for index in range(3):
        make_job(
            loop_session,
            company=company,
            title=f"Downgrade {index}",
            score_dimensions=_score_dimensions(1.0),
            score_overall=Decimal("3.50"),
        )

    payload = _weights_payload(loop_session)

    # Raising the weight of a badly-scoring dimension lowers the overall.
    assert payload["mean_delta"] < 0
    assert payload["max_delta"] < 0


# ---------------------------------------------------------------------------
# Tier B — advisory-flag recompute
# ---------------------------------------------------------------------------


def _ladder_backlog(session):
    """Eight live listings whose titles span intern -> staff."""
    company = make_company(session, name="Ladder Co")
    for index, title in enumerate(
        [
            "Engineering Intern",
            "Junior Backend Engineer",
            "Mid-Level Backend Engineer",
            "Senior Backend Engineer",
            "Senior Platform Engineer",
            "Staff Backend Engineer",
            "Staff Data Engineer",
            "Staff Site Reliability Engineer",
        ]
    ):
        make_job(session, company=company, title=title)
    return company


def test_tier_b_seniority_max_flag_delta(loop_session):
    _ladder_backlog(loop_session)

    payload = pe.predict_flag_change(
        loop_session,
        field="seniority_max",
        current_value="staff",
        proposed_value="senior",
    )

    assert payload["kind"] == "flag_recompute"
    assert payload["field"] == "seniority_max"
    assert payload["flag"] == "stretch_role"
    assert payload["backlog_size"] == 8
    # Only the three staff-level titles sit above the proposed ceiling of
    # "senior" without having sat above the current ceiling of "staff".
    assert payload["would_flag"] == 3
    assert payload["would_unflag"] == 0
    assert payload["unknown"] == 0
    assert json.loads(json.dumps(payload)) == payload


def test_tier_b_seniority_min_maps_to_step_down(loop_session):
    _ladder_backlog(loop_session)

    payload = pe.predict_flag_change(
        loop_session,
        field="seniority_min",
        current_value="mid",
        proposed_value="senior",
    )

    assert payload["flag"] == "step_down"
    # The single mid-level title newly falls below the raised floor.
    assert payload["would_flag"] == 1
    assert payload["would_unflag"] == 0


def test_tier_b_comp_floor_flag_delta(loop_session):
    company = make_company(loop_session, name="Comp Co")
    for index, amount in enumerate([90000, 110000, 120000, 150000]):
        make_job(
            loop_session,
            company=company,
            title=f"Paid {index}",
            comp_min=Decimal(str(amount)),
            comp_currency="USD",
            comp_period=CompPeriod.ANNUAL,
        )
    make_job(loop_session, company=company, title="No salary stated")

    payload = pe.predict_flag_change(
        loop_session,
        field="compensation_floor",
        current_value=CompensationFloor(
            amount=Decimal("100000"), currency="USD", period="annual"
        ),
        proposed_value=CompensationFloor(
            amount=Decimal("135000"), currency="USD", period="annual"
        ),
    )

    assert payload["kind"] == "flag_recompute"
    assert payload["field"] == "compensation_floor"
    assert payload["flag"] == "comp_below_floor"
    assert payload["backlog_size"] == 5
    # 110000 and 120000 sit between the two floors; 90000 was already below and
    # 150000 stays above.
    assert payload["would_flag"] == 2
    assert payload["would_unflag"] == 0
    # A listing with no salary data is not evidence of "below" — it is unknown.
    assert payload["unknown"] == 1


def test_tier_b_ignores_jobs_with_untitled_seniority(loop_session):
    company = make_company(loop_session, name="Blank Co")
    make_job(loop_session, company=company, title="Backend Engineer")
    make_job(loop_session, company=company, title="Staff Engineer")
    make_job(loop_session, company=company, title="Principal Engineer")

    payload = pe.predict_flag_change(
        loop_session,
        field="seniority_max",
        current_value="staff",
        proposed_value="senior",
    )

    assert payload["unknown"] == 1
    assert payload["would_flag"] == 1
    assert payload["would_unflag"] == 0


# ---------------------------------------------------------------------------
# Tier C — honest literal mention count
# ---------------------------------------------------------------------------


def test_tier_c_literal_count_for_title_keywords(loop_session):
    company = make_company(loop_session, name="Words Co")
    make_job(loop_session, company=company, title="Contractor Backend Engineer")
    make_job(loop_session, company=company, title="Senior Contractor")
    make_job(loop_session, company=company, title="Contractor")
    make_job(loop_session, company=company, title="Staff Engineer")

    payload = pe.predict_literal_count(
        loop_session,
        field="exclusions.title_keywords",
        current_value=[],
        proposed_value=["contractor"],
    )

    assert payload["kind"] == "literal_count"
    assert payload["field"] == "exclusions.title_keywords"
    assert payload["term"] == "contractor"
    assert payload["matches"] == 3
    assert payload["backlog_size"] == 4
    assert payload["enforced"] is False
    assert isinstance(payload["note"], str) and payload["note"]
    assert json.loads(json.dumps(payload)) == payload


def test_tier_c_note_states_not_enforced(loop_session):
    company = make_company(loop_session, name="Note Co")
    make_job(loop_session, company=company, title="Anything At All")

    payload = pe.predict_literal_count(
        loop_session,
        field="exclusions.title_keywords",
        current_value=[],
        proposed_value=["nurse"],
    )

    # The exact 05-UI-SPEC Tier C wording — the UI renders this sentence.
    assert "isn't enforced by discovery yet" in payload["note"]


def test_tier_c_employers_matches_company_names(loop_session):
    acme = make_company(loop_session, name="ACME")
    other = make_company(loop_session, name="Globex")
    make_job(loop_session, company=acme, title="Engineer One")
    make_job(loop_session, company=acme, title="Engineer Two")
    make_job(loop_session, company=other, title="Engineer Three")

    payload = pe.predict_literal_count(
        loop_session,
        field="exclusions.employers",
        current_value=[],
        proposed_value=["Acme"],
    )

    assert payload["term"] == "Acme"
    # Case-insensitive equality: "ACME" matches "Acme".
    assert payload["matches"] == 2


def test_tier_c_work_authorization_counts_requirement_mentions(loop_session):
    company = make_company(loop_session, name="Auth Co")
    for index in range(2):
        job = make_job(loop_session, company=company, title=f"Requires Auth {index}")
        job.work_auth_required = "Must be authorized to work in the US"
    make_job(loop_session, company=company, title="No requirement stated")
    loop_session.flush()

    payload = pe.predict_literal_count(
        loop_session,
        field="work_authorization",
        current_value=WorkAuthorization(
            countries_authorized=["US"], requires_sponsorship=False
        ),
        proposed_value=WorkAuthorization(
            countries_authorized=["US", "CA"], requires_sponsorship=False
        ),
    )

    assert payload["term"] is None
    assert payload["matches"] == 2
    assert payload["enforced"] is False


# ---------------------------------------------------------------------------
# The dispatcher — total over PERMITTED_EDIT_FIELDS
# ---------------------------------------------------------------------------


def _proposed_value(field: str):
    """A plausible proposed value for each permitted edit field."""
    if field == "seniority_min":
        return "senior"
    if field == "seniority_max":
        return "staff"
    if field == "posting_age_days":
        return 14
    if field == "locations":
        return LocationCriteria(
            eligible_countries=["US", "CA"], eligible_regions=[], preferred_cities=[]
        )
    if field == "compensation_floor":
        return CompensationFloor(
            amount=Decimal("135000"), currency="USD", period="annual"
        )
    if field == "exclusions.title_keywords":
        return ["contractor"]
    if field == "exclusions.employers":
        return ["Globex"]
    if field == "work_authorization":
        return WorkAuthorization(
            countries_authorized=["US", "CA"], requires_sponsorship=False
        )
    return DimensionWeights(
        role_fit=4.0, seniority_fit=2.0, employer_fit=2.0, trajectory=1.0
    )


def _direction_for(field: str) -> EditDirection:
    return EditDirection.INCREASE if field == "dimension_weights" else EditDirection.TIGHTEN


def _dispatch_backlog(session):
    """One live backlog covering every tier's input columns."""
    company = make_company(session, name="Dispatch Co")
    make_job(
        session,
        company=company,
        title="Senior Contractor",
        posted_at=_days_ago(25),
        location_normalized="Berlin, DE",
        is_remote=False,
        comp_min=Decimal("90000"),
        comp_currency="USD",
        comp_period=CompPeriod.ANNUAL,
        score_dimensions=_score_dimensions(1.0),
        score_overall=Decimal("3.50"),
    )
    other = make_job(
        session, company=company, title="Backend Engineer", posted_at=_days_ago(40)
    )
    other.work_auth_required = "US work authorization required"
    session.flush()
    return company


def _sweep(session) -> dict:
    """Run the dispatcher once for every permitted field."""
    payload = default_payload()
    results = {}
    for field in PERMITTED_EDIT_FIELDS:
        changes = build_proposed_changes(
            field,
            _direction_for(field),
            resolve_field(payload, field),
            _proposed_value(field),
        )
        results[field] = pe.predict_effect(session, changes, now=FIXED_NOW)
    return results


def test_dispatch_covers_every_permitted_field(loop_session):
    _dispatch_backlog(loop_session)

    results = _sweep(loop_session)

    # Iterating the constant itself means adding a tenth permitted field without
    # a tier fails this test rather than shipping a payload-less proposal.
    assert set(results) == set(PERMITTED_EDIT_FIELDS)
    for field, payload in results.items():
        assert payload["kind"] in KNOWN_KINDS, field
        assert payload["field"] == field


def test_dispatch_rejects_unknown_field(loop_session):
    with pytest.raises(UnsupportedEditField):
        pe.predict_effect(
            loop_session,
            {"field": "scoring_rubric", "current_value": None, "proposed_value": None},
        )


def test_dispatch_never_writes(loop_session):
    _dispatch_backlog(loop_session)

    _sweep(loop_session)

    assert not loop_session.dirty
    assert not loop_session.new


def test_every_payload_is_json_serialisable(loop_session):
    _dispatch_backlog(loop_session)

    for field, payload in _sweep(loop_session).items():
        assert json.loads(json.dumps(payload)) == payload, field
