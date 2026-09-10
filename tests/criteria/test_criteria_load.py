import pytest
import yaml
from pathlib import Path
from pydantic import ValidationError
from huntloop.criteria.loader import load_criteria_yaml, save_new_criteria_version, get_active_criteria
from huntloop.criteria.schema import CriteriaPayload

def test_load_example_yaml():
    path = Path("criteria.example.yml")
    p = load_criteria_yaml(path)
    assert p.dimension_weights.role_fit == 0.40
    assert p.posting_age_days == 30

def test_missing_dimension_weights(tmp_path):
    path = tmp_path / "test.yml"
    path.write_text("profile_summary: 'test'\n")
    with pytest.raises(ValidationError, match="dimension_weights"):
        load_criteria_yaml(path)

def test_unknown_dimension_weight(tmp_path):
    path = tmp_path / "test.yml"
    path.write_text("profile_summary: 'test'\ndimension_weights:\n  role_fit: 1\n  seniority_fit: 1\n  employer_fit: 1\n  trajectory: 1\n  unknown: 1\n")
    with pytest.raises(ValidationError, match="Extra inputs are not permitted"):
        load_criteria_yaml(path)

def test_dimension_weights_sum_zero(tmp_path):
    path = tmp_path / "test.yml"
    path.write_text("profile_summary: 'test'\ndimension_weights:\n  role_fit: 0\n  seniority_fit: 0\n  employer_fit: 0\n  trajectory: 0\n")
    with pytest.raises(ValidationError, match="dimension_weights sum must be > 0"):
        load_criteria_yaml(path)

def test_eligible_countries_normalization(tmp_path):
    path = tmp_path / "test.yml"
    path.write_text("profile_summary: 'test'\ndimension_weights:\n  role_fit: 1\n  seniority_fit: 1\n  employer_fit: 1\n  trajectory: 1\nlocations:\n  eligible_countries: [in, us]\n")
    p = load_criteria_yaml(path)
    assert p.locations.eligible_countries == ["IN", "US"]

def test_eligible_countries_invalid(tmp_path):
    path = tmp_path / "test.yml"
    path.write_text("profile_summary: 'test'\ndimension_weights:\n  role_fit: 1\n  seniority_fit: 1\n  employer_fit: 1\n  trajectory: 1\nlocations:\n  eligible_countries: [Xzz]\n")
    with pytest.raises(ValidationError, match="Invalid country code: Xzz"):
        load_criteria_yaml(path)

def test_currency_normalization(tmp_path):
    path = tmp_path / "test.yml"
    path.write_text("profile_summary: 'test'\ndimension_weights:\n  role_fit: 1\n  seniority_fit: 1\n  employer_fit: 1\n  trajectory: 1\ncompensation_floor:\n  currency: inr\n")
    p = load_criteria_yaml(path)
    assert p.compensation_floor.currency == "INR"

def test_currency_invalid(tmp_path):
    path = tmp_path / "test.yml"
    path.write_text("profile_summary: 'test'\ndimension_weights:\n  role_fit: 1\n  seniority_fit: 1\n  employer_fit: 1\n  trajectory: 1\ncompensation_floor:\n  currency: XZZ\n")
    with pytest.raises(ValidationError, match="Invalid currency code: XZZ"):
        load_criteria_yaml(path)

def test_posting_age_days_invalid(tmp_path):
    path = tmp_path / "test.yml"
    path.write_text("profile_summary: 'test'\nposting_age_days: 0\ndimension_weights:\n  role_fit: 1\n  seniority_fit: 1\n  employer_fit: 1\n  trajectory: 1\n")
    with pytest.raises(ValidationError, match="Input should be greater than or equal to 1"):
        load_criteria_yaml(path)

def test_db_save_and_get(main_session):
    assert get_active_criteria(main_session) is None
    
    p = load_criteria_yaml(Path("criteria.example.yml"))
    v1 = save_new_criteria_version(main_session, p)
    assert v1 == 1
    
    # Check is_active
    from huntloop.db.models import Criteria
    row1 = main_session.query(Criteria).filter_by(version=1).one()
    assert row1.is_active is True
    
    v2 = save_new_criteria_version(main_session, p)
    assert v2 == 2
    
    row1_again = main_session.query(Criteria).filter_by(version=1).one()
    assert row1_again.is_active is False
    row2 = main_session.query(Criteria).filter_by(version=2).one()
    assert row2.is_active is True
    
    active = get_active_criteria(main_session)
    assert active is not None
    assert active[0] == 2
