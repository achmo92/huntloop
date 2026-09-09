"""OPS-03: data survives a simulated container stop/recreate cycle.

File-backed SQLite only — an in-memory database would make this test
meaningless, since :memory: never survives engine disposal in the first
place.
"""


def test_survives_engine_recreation(main_db_path):
    # Imports deliberately inside the test body (see import discipline rule
    # in 01-01-PLAN.md Task 3): huntloop.db.models/repository don't exist
    # with real content yet, so this raises AttributeError/ImportError here,
    # not at collection time.
    from huntloop.db.base import Base, make_engine, make_session_factory
    from huntloop.db.repository import CompanyRepository, JobRepository

    # 1. Build engine A, create schema, write one Company and one Job.
    engine_a = make_engine(f"sqlite:///{main_db_path}")
    Base.metadata.create_all(engine_a)
    session_a = make_session_factory(engine_a)()

    company_repo = CompanyRepository(session_a)
    job_repo = JobRepository(session_a)

    company = company_repo.upsert(name="Acme Corp")
    job_repo.upsert(
        company_id=company.id,
        dedup_key="acme:job-1",
        title="Staff Engineer",
        url="https://example.com/jobs/1",
    )
    session_a.commit()

    # 2. Tear down engine A — simulates the container stopping.
    session_a.close()
    engine_a.dispose()

    # 3. Build a brand-new engine B against the SAME file path — simulates
    #    the container being recreated against the same volume.
    engine_b = make_engine(f"sqlite:///{main_db_path}")
    session_b = make_session_factory(engine_b)()

    # 4. The job written under engine A must still be readable via engine B.
    job = JobRepository(session_b).get_by_dedup_key("acme:job-1")
    assert job is not None
    assert job.title == "Staff Engineer"

    session_b.close()
    engine_b.dispose()

    # 5. The file itself is real and non-empty.
    assert main_db_path.exists()
    assert main_db_path.stat().st_size > 0
