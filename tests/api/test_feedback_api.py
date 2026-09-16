"""LOOP-02 general feedback capture on the proposal review surface (D-07).

Owned by plan 05-04. Consumes ``tests/api/conftest.py`` unedited: ``client``
speaks HTTP against the app with both session dependencies overridden onto a
tmp SQLite file, and ``make_session`` seeds rows directly over that same file.

Per the ``test_jobs_api.py`` convention, nothing from
``huntloop.api.routers.feedback`` is imported at module scope, so the
unimplemented route fails at request time (404) with a real assertion failure
rather than a collection error.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime

from sqlalchemy import select

from huntloop.db.models import Company, FeedbackNote, FeedbackSource, Job, JobStatus


def _mk_company(session, name: str = "Acme") -> Company:
    company = Company(name=name)
    session.add(company)
    session.flush()
    return company


def _mk_job(session, company: Company, *, title="Backend Engineer", dedup_key="acme:1") -> Job:
    now = datetime.now(UTC)
    job = Job(
        company_id=company.id,
        dedup_key=dedup_key,
        url=f"https://example.test/jobs/{dedup_key}",
        title=title,
        status=JobStatus.NEW,
        first_seen_at=now,
        last_seen_at=now,
    )
    session.add(job)
    session.flush()
    return job


def _note_rows(make_session) -> list[tuple[uuid.UUID, uuid.UUID | None, str, object]]:
    """Materialise every feedback note as plain values (session closed after)."""
    session = make_session()
    try:
        notes = (
            session.execute(select(FeedbackNote).order_by(FeedbackNote.created_at))
            .scalars()
            .all()
        )
        return [(n.id, n.job_id, n.text, n.source) for n in notes]
    finally:
        session.close()


# ---------------------------------------------------------------------------
# POST /api/feedback — the general, unattached sink (LOOP-02, D-07)
# ---------------------------------------------------------------------------


def test_post_feedback_creates_general_note(client, make_session):
    resp = client.post(
        "/api/feedback", json={"text": "Stop showing me contract roles"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert uuid.UUID(body["id"])
    assert body["created_at"] is not None

    rows = _note_rows(make_session)
    assert len(rows) == 1
    _note_id, job_id, text, source = rows[0]
    assert job_id is None
    assert text == "Stop showing me contract roles"
    assert source == FeedbackSource.CHAT


def test_empty_text_is_422(client, make_session):
    for payload in ({"text": ""}, {"text": "   "}):
        resp = client.post("/api/feedback", json=payload)
        assert resp.status_code == 422

    assert _note_rows(make_session) == []


def test_missing_text_is_422(client, make_session):
    resp = client.post("/api/feedback", json={})
    assert resp.status_code == 422
    assert _note_rows(make_session) == []


def test_text_is_length_capped(client, make_session):
    resp = client.post("/api/feedback", json={"text": "x" * 5000})
    assert resp.status_code == 422
    assert _note_rows(make_session) == []


def test_text_is_sanitised(client, make_session):
    resp = client.post(
        "/api/feedback",
        json={"text": "<script>alert('x')</script>Prefer remote roles"},
    )
    assert resp.status_code == 201

    rows = _note_rows(make_session)
    assert len(rows) == 1
    stored = rows[0][2]
    assert "Prefer remote roles" in stored
    assert "<" not in stored
    assert "script" not in stored.lower()


def test_does_not_touch_per_listing_notes(client, make_session):
    session = make_session()
    try:
        company = _mk_company(session)
        job = _mk_job(session, company)
        session.add(
            FeedbackNote(
                job_id=job.id,
                text="follow up Monday",
                source=FeedbackSource.JOB_NOTE,
            )
        )
        session.commit()
    finally:
        session.close()

    before = [row for row in _note_rows(make_session) if row[1] is not None]

    resp = client.post("/api/feedback", json={"text": "General signal"})
    assert resp.status_code == 201

    after = [row for row in _note_rows(make_session) if row[1] is not None]
    assert after == before
    assert len(after) == 1


def test_feedback_note_is_unattached(client, make_session):
    session = make_session()
    try:
        company = _mk_company(session)
        _mk_job(session, company)
        session.commit()
    finally:
        session.close()

    resp = client.post("/api/feedback", json={"text": "Nothing about a listing"})
    assert resp.status_code == 201

    rows = _note_rows(make_session)
    assert len(rows) == 1
    assert rows[0][1] is None
