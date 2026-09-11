"""Dashboard API contract tests (UI-02, D-14).

Consumes tests/api/conftest.py unedited. RED-phase note: nothing from
`huntloop.api.routers.dashboard` is imported at module scope, so the
unimplemented route fails at request time (404) with a real assertion failure.
"""

from datetime import UTC, datetime

from huntloop.db.models import Company, Job, JobStatus, RunStatus, RunTrigger
from huntloop.db.repository import RunRepository

_COUNTERS = {
    "companies_checked": 4,
    "listings_fetched": 50,
    "after_dedup": 40,
    "after_deterministic": 30,
    "after_triage": 20,
    "scored": 10,
    "new_jobs_written": 6,
    "tokens_in": 1000,
    "tokens_out": 250,
}


def _mk_company(session, name: str) -> Company:
    company = Company(name=name)
    session.add(company)
    session.flush()
    return company


def _mk_job(session, company: Company, index: int, status: JobStatus) -> Job:
    job = Job(
        company_id=company.id,
        dedup_key=f"{company.name}:{index}",
        url=f"https://example.test/jobs/{index}",
        title=f"Job {index}",
        status=status,
    )
    session.add(job)
    session.flush()
    return job


def test_dashboard_answers_last_run_funnel_and_stage_counts(client, make_session):
    session = make_session()
    try:
        company = _mk_company(session, "Acme")
        repo = RunRepository(session)
        run = repo.start(RunTrigger.SCHEDULED)
        repo.finish(run.id, status=RunStatus.SUCCESS, cost_usd=0.5, **_COUNTERS)
        statuses = [
            JobStatus.NEW,
            JobStatus.NEW,
            JobStatus.SHORTLISTED,
            JobStatus.APPLIED,
            JobStatus.INTERVIEWING,
            JobStatus.OFFER,
            JobStatus.REJECTED,
        ]
        for index, status in enumerate(statuses):
            _mk_job(session, company, index, status)
        session.commit()
        run_id = run.id
    finally:
        session.close()

    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.json()

    assert body["last_run"]["id"] == str(run_id)
    assert body["last_run"]["status"] == "success"
    assert body["last_run"]["cost_usd"] == 0.5

    assert body["funnel"] == {
        "companies_checked": 4,
        "listings_fetched": 50,
        "after_dedup": 40,
        "after_deterministic": 30,
        "after_triage": 20,
        "scored": 10,
        "new_jobs_written": 6,
    }

    # Every pipeline stage is present, zeros included (a zero is information).
    assert set(body["listings_by_status"]) == {status.value for status in JobStatus}
    assert body["listings_by_status"]["new"] == 2
    assert body["listings_by_status"]["withdrawn"] == 0
    assert body["total_listings"] == 7

    assert body["next_scheduled_run"] is not None
    datetime.fromisoformat(body["next_scheduled_run"])


def test_dashboard_quiet_week_is_an_answer_not_an_error(client):
    resp = client.get("/api/dashboard")
    assert resp.status_code == 200
    body = resp.json()

    assert body["last_run"] is None
    assert body["funnel"] is None
    assert body["total_listings"] == 0
    assert set(body["listings_by_status"]) == {status.value for status in JobStatus}
    assert all(count == 0 for count in body["listings_by_status"].values())
    # The schedule is still knowable with no runs at all.
    assert body["next_scheduled_run"] is not None
