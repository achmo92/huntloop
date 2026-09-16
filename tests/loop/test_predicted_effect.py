"""LOOP-06 predicted effect per field. Owned by plan 05-03."""

import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

pytest.importorskip("huntloop.loop.predicted_effect")

from sqlalchemy import select  # noqa: E402

from huntloop.db.models import Job, JobStatus  # noqa: E402
from huntloop.loop import predicted_effect as pe  # noqa: E402
from tests.loop.factories import loop_session  # noqa: E402, F401
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


# ---------------------------------------------------------------------------
# Tier A — deterministic filter dry-run
# ---------------------------------------------------------------------------


def test_tier_a_posting_age_counts_exclusions(loop_session):
    _age_backlog(loop_session, [1, 5, 9, 12, 15, 18, 25, 35, 40, 60])

    payload = pe.predict_effect(
        loop_session,
        {"field": "posting_age_days", "current_value": 30, "proposed_value": 20},
        now=FIXED_NOW,
    )

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

    payload = pe.predict_effect(
        loop_session,
        {"field": "posting_age_days", "current_value": 30, "proposed_value": 45},
        now=FIXED_NOW,
    )

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

    payload = pe.predict_effect(
        loop_session,
        {"field": "posting_age_days", "current_value": 30, "proposed_value": 20},
        now=FIXED_NOW,
    )

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

    payload = pe.predict_effect(
        loop_session,
        {
            "field": "locations",
            "current_value": {
                "eligible_countries": ["US"],
                "eligible_regions": [],
                "preferred_cities": [],
            },
            "proposed_value": {
                "eligible_countries": ["DE"],
                "eligible_regions": [],
                "preferred_cities": [],
            },
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

    payload = pe.predict_effect(
        loop_session,
        {"field": "posting_age_days", "current_value": 30, "proposed_value": 20},
        now=FIXED_NOW,
    )

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

    payload = pe.predict_effect(
        loop_session,
        {
            "field": "dimension_weights",
            "current_value": CURRENT_WEIGHTS,
            "proposed_value": PROPOSED_WEIGHTS,
        },
    )

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

    pe.predict_effect(
        loop_session,
        {
            "field": "dimension_weights",
            "current_value": CURRENT_WEIGHTS,
            "proposed_value": PROPOSED_WEIGHTS,
        },
    )

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

    payload = pe.predict_effect(
        loop_session,
        {
            "field": "dimension_weights",
            "current_value": CURRENT_WEIGHTS,
            "proposed_value": PROPOSED_WEIGHTS,
        },
    )

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

    payload = pe.predict_effect(
        loop_session,
        {
            "field": "dimension_weights",
            "current_value": CURRENT_WEIGHTS,
            "proposed_value": PROPOSED_WEIGHTS,
        },
    )

    # Raising the weight of a badly-scoring dimension lowers the overall.
    assert payload["mean_delta"] < 0
    assert payload["max_delta"] < 0


# ---------------------------------------------------------------------------
# Dispatch — implemented by Task 2
# ---------------------------------------------------------------------------


def test_dispatch_covers_every_permitted_field():
    pytest.fail("not yet implemented — owned by plan 05-03 Task 2")
