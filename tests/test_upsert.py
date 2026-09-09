"""Idempotent-write guardrail: a re-discovery upsert must never clobber
user-owned fields (status, user_notes) while still refreshing
discovery-owned fields (title, last_seen_at).

01-RESEARCH.md Pitfall A names exactly this failure mode: "a pipeline
status that was manually advanced reverting to 'new' after a scheduled
run."
"""

from huntloop.db.models import JobStatus
from huntloop.db.repository import CompanyRepository, JobRepository


def test_upsert_is_idempotent_and_preserves_user_fields(main_session):
    company_repo = CompanyRepository(main_session)
    job_repo = JobRepository(main_session)

    # 1. Initial discovery upsert.
    company_id = company_repo.upsert_by_name(name="Acme Corp")
    job_repo.upsert_discovered(
        dedup_key="acme:job-1",
        company_id=company_id,
        url="https://example.com/jobs/1",
        title="Staff Engineer",
    )
    main_session.commit()

    # 2. Simulate the user acting on the job before the next scheduled run.
    job = job_repo.get_by_dedup_key("acme:job-1")
    job.status = JobStatus.APPLIED
    job.user_notes = "spoke to the hiring manager"
    main_session.commit()
    first_seen_at = job.first_seen_at
    last_seen_at_before = job.last_seen_at

    # 3. Re-run the SAME discovery upsert with a changed title — simulates
    #    the scheduled run picking the listing up again after a restart.
    job_repo.upsert_discovered(
        dedup_key="acme:job-1",
        company_id=company_id,
        url="https://example.com/jobs/1",
        title="Staff Engineer, Platform",
    )
    main_session.commit()
    # The upsert wrote through session.connection() (Core-level), so the
    # identity-mapped `job` instance above is stale until expired.
    main_session.expire_all()

    # 4. Exactly one row for this dedup_key — no duplicate created.
    all_matches = job_repo.list_by_dedup_key("acme:job-1")
    assert len(all_matches) == 1

    refreshed = job_repo.get_by_dedup_key("acme:job-1")

    # 5. Discovery-owned field refreshed.
    assert refreshed.title == "Staff Engineer, Platform"

    # 6. User-owned field NOT clobbered — the whole point of this test.
    assert refreshed.status == JobStatus.APPLIED

    # 7. User-owned free text NOT clobbered.
    assert refreshed.user_notes == "spoke to the hiring manager"

    # 8. first_seen_at stable, last_seen_at advanced (or at least not reset).
    assert refreshed.first_seen_at == first_seen_at
    assert refreshed.last_seen_at >= last_seen_at_before
