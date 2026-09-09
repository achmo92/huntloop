"""Development-only persistence probe: `python -m huntloop.dev_probe write|read`.

Exists so the OPS-03 stop/recreate/restart cycle can be verified by a script
rather than by hand. Not imported by application code and not part of the
public API. Exit code 0 means the assertions held; 1 means they did not.
"""

from __future__ import annotations

import sys

from huntloop.credentials.base import get_credentials_engine, make_credentials_session_factory
from huntloop.credentials.store import CredentialStore
from huntloop.db.base import get_engine, make_session_factory
from huntloop.db.models import JobStatus
from huntloop.db.repository import CompanyRepository, JobRepository

PROBE_COMPANY = "Probe Industries"
PROBE_DEDUP_KEY = "probe-industries:persistence-check-1"
PROBE_URL = "https://probe.example/jobs/persistence-check-1"
PROBE_TITLE_V1 = "Persistence Check Engineer"
PROBE_TITLE_V2 = "Persistence Check Engineer, Volume Team"
PROBE_NOTE = "probe note that must survive a restart"
PROBE_SECRET_KEY_NAME = "probe_api_key"
PROBE_SECRET_VALUE = "sk-probe-do-not-leak"


def write_probe() -> None:
    """Write (or re-write) the probe company/job/credential.

    Safe to call repeatedly. The SECOND call is what proves idempotency: the
    discovery-owned `title` field refreshes to PROBE_TITLE_V2, while the
    user-owned status/notes set on the FIRST call are never re-applied and
    therefore never revert.
    """
    engine = get_engine()
    session = make_session_factory(engine)()

    existing = JobRepository(session).get_by_dedup_key(PROBE_DEDUP_KEY)
    title = PROBE_TITLE_V2 if existing is not None else PROBE_TITLE_V1

    company_id = CompanyRepository(session).upsert_by_name(
        PROBE_COMPANY,
        ats_identifier="probe-industries",
        careers_url="https://probe.example/careers",
    )
    JobRepository(session).upsert_discovered(
        PROBE_DEDUP_KEY, company_id, url=PROBE_URL, title=title
    )
    session.commit()

    if existing is None:
        job = JobRepository(session).get_by_dedup_key(PROBE_DEDUP_KEY)
        JobRepository(session).set_status(job.id, JobStatus.APPLIED)
        job.user_notes = PROBE_NOTE
        session.commit()

    cred_engine = get_credentials_engine()
    cred_session = make_credentials_session_factory(cred_engine)()
    CredentialStore(cred_session).set(PROBE_SECRET_KEY_NAME, PROBE_SECRET_VALUE)
    cred_session.commit()

    print(f"WROTE dedup_key={PROBE_DEDUP_KEY} title={title} credential={PROBE_SECRET_KEY_NAME}")


def read_probe() -> None:
    """Read back the probe row and assert every persistence guarantee holds."""
    engine = get_engine()
    session = make_session_factory(engine)()

    job = JobRepository(session).get_by_dedup_key(PROBE_DEDUP_KEY)
    assert job is not None, f"expected a job with dedup_key={PROBE_DEDUP_KEY!r}, found none"

    count = JobRepository(session).count()
    assert count == 1, f"expected JobRepository(session).count() == 1, found {count}"

    assert job.status is JobStatus.APPLIED, (
        f"expected status={JobStatus.APPLIED!r} (must survive a re-run), got {job.status!r}"
    )

    assert job.user_notes == PROBE_NOTE, (
        f"expected user_notes={PROBE_NOTE!r} (must survive a re-run), got {job.user_notes!r}"
    )

    assert job.title in {PROBE_TITLE_V1, PROBE_TITLE_V2}, (
        f"expected title in {{{PROBE_TITLE_V1!r}, {PROBE_TITLE_V2!r}}}, got {job.title!r}"
    )
    print(f"title is {job.title}")

    cred_engine = get_credentials_engine()
    cred_session = make_credentials_session_factory(cred_engine)()
    secret = CredentialStore(cred_session).get(PROBE_SECRET_KEY_NAME)
    assert secret == PROBE_SECRET_VALUE, (
        f"expected credential {PROBE_SECRET_KEY_NAME!r} to decrypt to the probe value, "
        f"got {secret!r}"
    )

    print(f"READ ok count={count} status=applied title={job.title} credential=decrypted")


def main() -> int:
    if len(sys.argv) != 2 or sys.argv[1] not in {"write", "read"}:
        print("usage: python -m huntloop.dev_probe write|read", file=sys.stderr)
        return 2

    try:
        if sys.argv[1] == "write":
            write_probe()
        else:
            read_probe()
        return 0
    except AssertionError as exc:
        print(f"PROBE FAILED: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 - deliberately broad: this is a CLI entrypoint's final catch-all
        print(f"PROBE ERROR: {exc!r}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
