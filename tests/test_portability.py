"""OPS-04: the system runs against SQLite by default and Postgres by
changing exactly one setting — proven by running the IDENTICAL
repository-level write/read sequence against both backends with zero
dialect branching in the test body itself.
"""

import uuid
from datetime import UTC, datetime

import pytest


def test_repository_write_read_parity(portable_engine):
    # Imports inside the body — see import discipline rule.
    from huntloop.db.base import make_session_factory
    from huntloop.db.models import RunStatus
    from huntloop.db.repository import (
        CompanyRepository,
        CriteriaRepository,
        JobRepository,
        RunRepository,
    )

    session = make_session_factory(portable_engine)()

    company_repo = CompanyRepository(session)
    job_repo = JobRepository(session)
    criteria_repo = CriteriaRepository(session)
    run_repo = RunRepository(session)

    company = company_repo.upsert(name="Acme Corp")
    job_repo.upsert(
        company_id=company.id,
        dedup_key="acme:job-1",
        title="Staff Engineer",
        url="https://example.com/jobs/1",
    )

    payload = {
        "locations": ["remote", "US"],
        "min_comp": 150000.5,
        "weights": {"seniority": 0.4, "comp": 0.6},
    }
    criteria_repo.create(
        version=1,
        is_active=True,
        payload=payload,
        source="initial",
    )

    run = run_repo.create(
        trigger="manual",
        status=RunStatus.SUCCESS,
        started_at=datetime.now(UTC),
        cost_usd=1.2345,
    )

    session.commit()

    # Read everything back through the repositories — no `if dialect ==`
    # branching anywhere in this test body.
    reloaded_company = company_repo.get_by_id(company.id)
    reloaded_job = job_repo.get_by_dedup_key("acme:job-1")
    reloaded_criteria = criteria_repo.get_active()
    reloaded_run = run_repo.get_by_id(run.id)

    assert isinstance(reloaded_company.id, uuid.UUID)
    assert reloaded_job.title == "Staff Engineer"
    assert isinstance(reloaded_job.id, uuid.UUID)

    assert reloaded_criteria.payload == payload
    assert reloaded_criteria.payload["weights"]["comp"] == 0.6
    assert isinstance(reloaded_criteria.payload["locations"], list)
    assert reloaded_criteria.is_active is True

    assert reloaded_run.status == RunStatus.SUCCESS
    assert float(reloaded_run.cost_usd) == pytest.approx(1.2345)

    assert reloaded_run.started_at.tzinfo is not None
    assert reloaded_job.first_seen_at.tzinfo is not None


@pytest.mark.postgres
def test_alembic_upgrade_head_on_postgres():
    pytest.skip("implemented in plan 01-05")
