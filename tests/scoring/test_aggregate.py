from decimal import Decimal
import pytest
from sqlalchemy import insert
from huntloop.db.models import Job, Company, FilterTier
from huntloop.scoring.aggregate import compute_overall_score, recompute_backlog_overall_scores, UnscoreableError

def test_overall_equal_weights():
    res = compute_overall_score(
        {"role_fit": 4, "seniority_fit": 4, "employer_fit": 4, "trajectory": 4},
        {"role_fit": 0.25, "seniority_fit": 0.25, "employer_fit": 0.25, "trajectory": 0.25}
    )
    assert res == Decimal("4.00")

def test_overall_unequal_weights():
    res = compute_overall_score(
        {"role_fit": 5, "seniority_fit": 1, "employer_fit": 3, "trajectory": 3},
        {"role_fit": 0.4, "seniority_fit": 0.2, "employer_fit": 0.2, "trajectory": 0.2}
    )
    assert res == Decimal("3.40")

def test_overall_non_normalized_weights():
    res = compute_overall_score(
        {"role_fit": 5, "seniority_fit": 1, "employer_fit": 3, "trajectory": 3},
        {"role_fit": 4, "seniority_fit": 2, "employer_fit": 2, "trajectory": 2}
    )
    assert res == Decimal("3.40")

def test_renormalize_missing_dimension():
    res = compute_overall_score(
        {"role_fit": 5, "seniority_fit": None, "employer_fit": 3, "trajectory": 3},
        {"role_fit": 0.4, "seniority_fit": 0.2, "employer_fit": 0.2, "trajectory": 0.2}
    )
    assert res == Decimal("3.75")
    assert res != Decimal("3.40")

def test_overall_all_null():
    with pytest.raises(UnscoreableError):
        compute_overall_score(
            {"role_fit": None, "seniority_fit": None, "employer_fit": None, "trajectory": None},
            {"role_fit": 0.4, "seniority_fit": 0.2, "employer_fit": 0.2, "trajectory": 0.2}
        )

def test_overall_all_zero_weight():
    with pytest.raises(UnscoreableError, match="no assessable"):
        compute_overall_score(
            {"role_fit": 4, "seniority_fit": 4, "employer_fit": None, "trajectory": None},
            {"role_fit": 0, "seniority_fit": 0, "employer_fit": 0.2, "trajectory": 0.2}
        )

def test_overall_missing_weight():
    with pytest.raises(KeyError):
        compute_overall_score(
            {"role_fit": 4, "extra": 4},
            {"role_fit": 0.4}
        )

@pytest.fixture
def assert_no_model_calls(monkeypatch):
    import huntloop.llm.client
    def boom(*args, **kwargs):
        raise AssertionError("SCOR-11 violated: a model call was issued during backlog recompute")
    monkeypatch.setattr(huntloop.llm.client, "complete_json", boom)
    monkeypatch.setattr(huntloop.llm.client, "get_llm_client", boom)

def test_backlog_recompute(main_session, assert_no_model_calls):
    from datetime import datetime, timezone
    import uuid
    now = datetime.now(timezone.utc)
    c_id = uuid.uuid4()
    j1_id = uuid.uuid4()
    j2_id = uuid.uuid4()
    j3_id = uuid.uuid4()
    j4_id = uuid.uuid4()
    j5_id = uuid.uuid4()
    
    main_session.execute(insert(Company).values(id=c_id, name="C1"))
    main_session.execute(insert(Job).values(
        id=j1_id, company_id=c_id, external_id="x1", url="u1", title="t1",
        dedup_key="x1", status="NEW",
        filter_tier_reached=FilterTier.FULL,
        score_overall=Decimal("1.00"),
        score_dimensions={"role_fit": {"score": 5}, "seniority_fit": {"score": 1}, "employer_fit": {"score": 3}, "trajectory": {"score": 3}},
        score_flags={}, score_summary="old",
        scored_criteria_version=1, scored_rubric_version="abc", scored_with_model="m1"
    ))
    main_session.execute(insert(Job).values(
        id=j2_id, company_id=c_id, external_id="x2", url="u2", title="t2",
        dedup_key="x2", status="NEW",
        filter_tier_reached=FilterTier.FULL,
        score_overall=Decimal("1.00"),
        score_dimensions={"role_fit": 5, "seniority_fit": 1, "employer_fit": 3, "trajectory": 3},
        score_flags={}, score_summary="old",
        scored_criteria_version=1, scored_rubric_version="abc", scored_with_model="m1"
    ))
    main_session.execute(insert(Job).values(
        id=j3_id, company_id=c_id, external_id="x3", url="u3", title="t3",
        dedup_key="x3", status="NEW",
        filter_tier_reached=FilterTier.FULL,
        score_overall=Decimal("1.00"),
        score_dimensions={"role_fit": 5, "seniority_fit": None, "employer_fit": 3, "trajectory": 3},
        score_flags={}, score_summary="old",
        scored_criteria_version=1, scored_rubric_version="abc", scored_with_model="m1"
    ))
    # Unscored job
    main_session.execute(insert(Job).values(
        id=j4_id, company_id=c_id, external_id="x4", url="u4", title="t4",
        dedup_key="x4", status="NEW",
        filter_tier_reached=FilterTier.DETERMINISTIC
    ))
    # All null dimensions job
    main_session.execute(insert(Job).values(
        id=j5_id, company_id=c_id, external_id="x5", url="u5", title="t5",
        dedup_key="x5", status="NEW",
        filter_tier_reached=FilterTier.FULL,
        score_overall=Decimal("1.00"),
        score_dimensions={"role_fit": None, "seniority_fit": None, "employer_fit": None, "trajectory": None},
        score_flags={}, score_summary="old",
        scored_criteria_version=1, scored_rubric_version="abc", scored_with_model="m1"
    ))
    main_session.commit()
    
    weights = {"role_fit": 0.4, "seniority_fit": 0.2, "employer_fit": 0.2, "trajectory": 0.2}
    updated = recompute_backlog_overall_scores(main_session, weights)
    assert updated == 3
    
    j1 = main_session.query(Job).filter_by(id=j1_id).one()
    assert j1.score_overall == Decimal("3.40")
    assert j1.score_summary == "old"
    assert j1.scored_with_model == "m1"
    
    j2 = main_session.query(Job).filter_by(id=j2_id).one()
    assert j2.score_overall == Decimal("3.40")
    
    j3 = main_session.query(Job).filter_by(id=j3_id).one()
    assert j3.score_overall == Decimal("3.75")
    
    j4 = main_session.query(Job).filter_by(id=j4_id).one()
    assert j4.score_overall is None
    
    j5 = main_session.query(Job).filter_by(id=j5_id).one()
    assert j5.score_overall == Decimal("1.00")
