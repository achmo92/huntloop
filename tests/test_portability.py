"""OPS-04: the system runs against SQLite by default and Postgres by
changing exactly one setting — proven by running the IDENTICAL
repository-level write/read sequence against both backends with zero
dialect branching in the test body itself.

Backend selection lives entirely in the `portable_engine` fixture
(tests/conftest.py). No test body in this file may branch on which
backend it is running against -- that branch-freeness IS the OPS-04
assertion.
"""

import os
import subprocess
import sys
import uuid
from datetime import UTC, datetime
from decimal import Decimal
from pathlib import Path

import pytest
import sqlalchemy
from cryptography.fernet import Fernet

from huntloop.db.base import make_engine, make_session_factory
from huntloop.db.models import (
    AtsPlatform,
    CompPeriod,
    Criteria,
    CriteriaSource,
    FilterTier,
    JobStatus,
    RemoteScope,
    Run,
    RunStatus,
    RunTrigger,
)
from huntloop.db.repository import CompanyRepository, JobRepository, SettingsRepository

COMPANY_NAME = "Acme Corp"
DEDUP_KEY = "acme:job-42"
CRITERIA_PAYLOAD = {
    "locations": ["Bengaluru", "Remote (IN)"],
    "seniority": {"min": "senior", "max": "staff"},
    "comp_floor": {"amount": 4500000.5, "currency": "INR", "period": "annual"},
    "exclusions": ["crypto", "gambling"],
    "weights": {
        "role_fit": 0.4,
        "seniority_fit": 0.2,
        "employer_fit": 0.2,
        "trajectory": 0.2,
    },
}
POSTED_AT = datetime(2026, 9, 1, 12, 30, tzinfo=UTC)
COST_USD = Decimal("1.234567")
COMP_MIN = Decimal("4500000.00")
SCORE_OVERALL = Decimal("4.25")


def test_repository_write_read_parity(portable_engine):
    session = make_session_factory(portable_engine)()
    try:
        _run_repository_write_read_parity(session)
    finally:
        # Explicit close, not just letting `session` fall out of scope:
        # an unclosed session leaves its connection checked out of the pool
        # in an open (possibly idle-in-transaction) state, which on Postgres
        # blocks the portable_engine fixture's own `Base.metadata.drop_all()`
        # teardown indefinitely (DROP TABLE needs an exclusive lock that an
        # open transaction elsewhere is holding up).
        session.close()


def _run_repository_write_read_parity(session):
    company_repo = CompanyRepository(session)
    job_repo = JobRepository(session)
    settings_repo = SettingsRepository(session)

    run = Run(
        trigger=RunTrigger.MANUAL,
        status=RunStatus.SUCCESS,
        cost_usd=COST_USD,
        listings_fetched=17,
        after_dedup=15,
        after_deterministic=9,
        after_triage=4,
        scored=4,
        new_jobs_written=3,
        tokens_in=1200,
        tokens_out=350,
        companies_checked=2,
    )
    session.add(run)

    criteria = Criteria(
        version=1,
        is_active=True,
        payload=CRITERIA_PAYLOAD,
        source=CriteriaSource.INITIAL,
    )
    session.add(criteria)

    # Flush so the Python-side uuid4 primary-key defaults are populated on
    # both `run` and `criteria` before we need their generated ids.
    session.flush()
    run_id = run.id
    criteria_id = criteria.id

    company_id = company_repo.upsert_by_name(
        COMPANY_NAME,
        ats=AtsPlatform.GREENHOUSE,
        ats_identifier="acmecorp-eng",
        ats_config={"tenant": None, "site": "external"},
        careers_url="https://acme.example/careers",
    )

    job_repo.upsert_discovered(
        DEDUP_KEY,
        company_id,
        url="https://acme.example/jobs/42",
        title="Staff Engineer",
        external_id="42",
        location_raw="Bengaluru, India (Hybrid)",
        is_remote=False,
        remote_scope=RemoteScope.UNSPECIFIED,
        location_eligible=True,
        posted_at=POSTED_AT,
        comp_raw="INR 45L - 60L",
        comp_min=COMP_MIN,
        comp_currency="INR",
        comp_period=CompPeriod.ANNUAL,
    )
    job_id = job_repo.get_by_dedup_key(DEDUP_KEY).id

    job_repo.mark_scored(
        job_id,
        score_overall=SCORE_OVERALL,
        score_dimensions={
            "role_fit": {"score": 5, "reason": "direct match"},
            "seniority_fit": {"score": 4, "reason": "slight stretch"},
            "employer_fit": {"score": 4, "reason": "right stage"},
            "trajectory": {"score": 4, "reason": "scope increase"},
        },
        score_flags=["stretch_role"],
        score_summary="Strong platform role at the right level",
        criteria_version=1,
        rubric_version="rubric-v1",
        model="gpt-test",
        filter_tier=FilterTier.FULL,
    )

    settings_repo.set_value("schedule_cron", {"expr": "0 7 * * *", "tz": "Asia/Kolkata"})

    session.commit()
    session.expire_all()

    # Read everything back -- no branching on which backend produced it.
    company = company_repo.get_by_name(COMPANY_NAME)
    job = job_repo.get_by_dedup_key(DEDUP_KEY)
    reloaded_run = session.get(Run, run_id)
    reloaded_criteria = session.get(Criteria, criteria_id)

    assert isinstance(company.id, uuid.UUID)
    assert isinstance(job.id, uuid.UUID)

    assert reloaded_criteria.payload == CRITERIA_PAYLOAD

    assert job.score_dimensions["role_fit"]["reason"] == "direct match"

    assert reloaded_run.status is RunStatus.SUCCESS
    assert job.comp_period is CompPeriod.ANNUAL

    assert isinstance(reloaded_run.cost_usd, Decimal)
    assert reloaded_run.cost_usd == COST_USD
    assert job.comp_min == COMP_MIN
    assert job.score_overall == SCORE_OVERALL

    assert job.posted_at.tzinfo is not None
    assert job.posted_at.astimezone(UTC) == POSTED_AT

    assert settings_repo.get_value("schedule_cron") == {
        "expr": "0 7 * * *",
        "tz": "Asia/Kolkata",
    }

    assert reloaded_run.listings_fetched == 17
    assert reloaded_run.after_dedup == 15
    assert reloaded_run.after_deterministic == 9
    assert reloaded_run.after_triage == 4
    assert reloaded_run.scored == 4
    assert reloaded_run.new_jobs_written == 3


def test_upsert_parity(portable_engine):
    session = make_session_factory(portable_engine)()
    try:
        _run_upsert_parity(session)
    finally:
        session.close()


def _run_upsert_parity(session):
    company_repo = CompanyRepository(session)
    job_repo = JobRepository(session)

    company_id = company_repo.upsert_by_name(
        COMPANY_NAME,
        ats=AtsPlatform.GREENHOUSE,
        ats_identifier="acmecorp-eng",
    )
    job_repo.upsert_discovered(
        DEDUP_KEY,
        company_id,
        url="https://acme.example/jobs/42",
        title="Staff Engineer",
    )
    session.commit()

    job = job_repo.get_by_dedup_key(DEDUP_KEY)
    job_id = job.id
    original_first_seen_at = job.first_seen_at
    original_last_seen_at = job.last_seen_at

    job_repo.set_status(job_id, JobStatus.APPLIED)
    job.user_notes = "reached out on 2026-09-05"
    session.commit()

    # Re-run the identical discovery upsert -- must not clobber user-owned
    # or system-computed state, only discovery-owned fields.
    job_repo.upsert_discovered(
        DEDUP_KEY,
        company_id,
        url="https://acme.example/jobs/42",
        title="Staff Engineer, Platform",
    )
    session.commit()
    session.expire_all()

    assert job_repo.count() == 1

    job = job_repo.get_by_dedup_key(DEDUP_KEY)
    assert job.title == "Staff Engineer, Platform"
    assert job.status is JobStatus.APPLIED
    assert job.user_notes == "reached out on 2026-09-05"
    assert job.first_seen_at == original_first_seen_at
    assert job.last_seen_at > original_last_seen_at
    assert job.score_overall is None

    with pytest.raises(ValueError):
        job_repo.upsert_discovered(
            DEDUP_KEY,
            company_id,
            url="https://acme.example/jobs/42",
            title="Staff Engineer, Platform",
            status=JobStatus.NEW,
        )

    # REG-06: disabling an employer must survive a re-resolution.
    company = company_repo.get_by_name(COMPANY_NAME)
    company.enabled = False
    session.commit()

    company_repo.upsert_by_name(COMPANY_NAME, ats_identifier="acmecorp-2")
    session.commit()
    session.expire_all()

    company = company_repo.get_by_name(COMPANY_NAME)
    assert company.enabled is False
    assert company.ats_identifier == "acmecorp-2"


@pytest.mark.postgres
def test_alembic_upgrade_head_on_postgres(postgres_url, tmp_path):
    repo_root = Path(__file__).resolve().parents[1]
    env = {
        **os.environ,
        "DATABASE_URL": postgres_url,  # the ONE value that changes
        "HUNTLOOP_DATA_DIR": str(tmp_path),
        "HUNTLOOP_SECRET_KEY": Fernet.generate_key().decode(),
    }
    for args in (
        ["--name", "main_db", "upgrade", "head"],
        ["--name", "main_db", "check"],
    ):
        result = subprocess.run(
            [sys.executable, "-m", "alembic", *args],
            env=env,
            capture_output=True,
            text=True,
            cwd=repo_root,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr

    engine = make_engine(postgres_url)
    try:
        table_names = set(sqlalchemy.inspect(engine).get_table_names())
        expected_tables = {
            "criteria",
            "criteria_proposals",
            "companies",
            "jobs",
            "status_events",
            "feedback_notes",
            "runs",
            "run_errors",
            "settings",
            "alembic_version",
        }
        assert expected_tables <= table_names
        assert "credentials" not in table_names
    finally:
        engine.dispose()
