"""Listings API contract tests (TRAK-01..05, TRAK-07, UI-06).

Consumes tests/api/conftest.py unedited: `client` speaks HTTP against the app
with both session dependencies overridden onto a tmp SQLite file, and
`make_session` seeds rows directly over that same file.

RED-phase note: nothing from `huntloop.api.routers.jobs` is imported at module
scope, so the unimplemented routes fail at request time (404/405) with a real
assertion failure rather than a collection error.
"""

import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select

from huntloop.db.models import (
    Company,
    FeedbackNote,
    FeedbackSource,
    Job,
    JobStatus,
    StatusEvent,
    StatusEventSource,
)

_DIMENSIONS = {
    "role_fit": {"score": 5, "reason": "direct match for the stack"},
    "seniority_fit": {"score": 4, "reason": "slightly senior but reachable"},
    "employer_fit": {"score": 3, "reason": "neutral company signal"},
    "trajectory": {"score": 4, "reason": "clear growth path"},
}


def _mk_company(session, name: str) -> Company:
    company = Company(name=name)
    session.add(company)
    session.flush()
    return company


def _mk_job(
    session,
    company: Company,
    *,
    title: str,
    dedup_key: str,
    score: float | None = None,
    status: JobStatus = JobStatus.NEW,
    posted_at: datetime | None = None,
    first_seen_at: datetime | None = None,
    description: str | None = None,
    dimensions: dict | None = None,
    flags: dict | None = None,
) -> Job:
    job = Job(
        company_id=company.id,
        dedup_key=dedup_key,
        url=f"https://example.test/jobs/{dedup_key}",
        title=title,
        status=status,
        score_overall=score,
        posted_at=posted_at,
        first_seen_at=first_seen_at or datetime.now(UTC),
        description=description,
        score_dimensions=dimensions,
        score_flags=flags,
    )
    session.add(job)
    session.flush()
    return job


# ---------------------------------------------------------------------------
# GET /api/jobs — composable list (TRAK-05)
# ---------------------------------------------------------------------------


def test_list_returns_every_row_with_total_and_score(client, make_session):
    session = make_session()
    try:
        acme = _mk_company(session, "Acme")
        globex = _mk_company(session, "Globex")
        _mk_job(session, acme, title="High Score", dedup_key="acme:1", score=4.5)
        _mk_job(session, acme, title="Low Score", dedup_key="acme:2", score=1.5)
        _mk_job(session, globex, title="Unscored", dedup_key="globex:1", score=None)
        session.commit()
    finally:
        session.close()

    resp = client.get("/api/jobs")
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 3
    assert len(body["items"]) == 3

    by_title = {row["title"]: row for row in body["items"]}
    assert by_title["High Score"]["score_overall"] == 4.5
    assert by_title["Low Score"]["score_overall"] == 1.5
    # TRAK-06 philosophy on the read side: an unscored row is surfaced, not hidden
    assert by_title["Unscored"]["score_overall"] is None
    assert by_title["High Score"]["company_name"] == "Acme"


def test_filters_compose_status_and_score_min(client, make_session):
    session = make_session()
    try:
        acme = _mk_company(session, "Acme")
        _mk_job(session, acme, title="New Strong", dedup_key="acme:1", score=4.5)
        _mk_job(session, acme, title="New Weak", dedup_key="acme:2", score=2.0)
        _mk_job(
            session,
            acme,
            title="Shortlisted Strong",
            dedup_key="acme:3",
            score=4.8,
            status=JobStatus.SHORTLISTED,
        )
        session.commit()
    finally:
        session.close()

    resp = client.get("/api/jobs?status=new,shortlisted&score_min=3")
    assert resp.status_code == 200
    titles = {row["title"] for row in resp.json()["items"]}
    assert titles == {"New Strong", "Shortlisted Strong"}


def test_employer_and_posted_window_filters(client, make_session):
    session = make_session()
    try:
        acme = _mk_company(session, "Acme")
        globex = _mk_company(session, "Globex")
        now = datetime.now(UTC)
        recent = _mk_job(
            session,
            acme,
            title="Recent",
            dedup_key="acme:1",
            posted_at=now - timedelta(days=2),
        )
        _mk_job(
            session,
            acme,
            title="Old",
            dedup_key="acme:2",
            posted_at=now - timedelta(days=40),
        )
        _mk_job(
            session,
            globex,
            title="Other Employer",
            dedup_key="globex:1",
            posted_at=now - timedelta(days=2),
        )
        acme_id = acme.id
        cutoff = (now - timedelta(days=10)).date().isoformat()
        session.commit()
    finally:
        session.close()

    by_employer = client.get(f"/api/jobs?employer_id={acme_id}").json()
    assert {row["title"] for row in by_employer["items"]} == {"Recent", "Old"}

    by_window = client.get(f"/api/jobs?posted_after={cutoff}").json()
    assert {row["title"] for row in by_window["items"]} == {
        "Recent",
        "Other Employer",
    }
    # The window actually excluded the 40-day-old row.
    assert recent.title == "Recent"


def test_sort_by_score_desc_and_first_seen_asc(client, make_session):
    session = make_session()
    try:
        acme = _mk_company(session, "Acme")
        now = datetime.now(UTC)
        _mk_job(
            session,
            acme,
            title="Middle",
            dedup_key="acme:1",
            score=2.0,
            first_seen_at=now - timedelta(days=2),
        )
        _mk_job(
            session,
            acme,
            title="Top",
            dedup_key="acme:2",
            score=4.0,
            first_seen_at=now - timedelta(days=1),
        )
        _mk_job(
            session,
            acme,
            title="Bottom",
            dedup_key="acme:3",
            score=1.0,
            first_seen_at=now - timedelta(days=3),
        )
        session.commit()
    finally:
        session.close()

    by_score = client.get("/api/jobs?sort=score_overall&order=desc").json()
    assert [row["title"] for row in by_score["items"]] == ["Top", "Middle", "Bottom"]

    by_seen = client.get("/api/jobs?sort=first_seen_at&order=asc").json()
    assert [row["title"] for row in by_seen["items"]] == ["Bottom", "Middle", "Top"]


def test_pagination_second_page_and_page_size_clamp(client, make_session):
    session = make_session()
    try:
        acme = _mk_company(session, "Acme")
        for i in range(3):
            _mk_job(session, acme, title=f"Job {i}", dedup_key=f"acme:{i}", score=float(i))
        session.commit()
    finally:
        session.close()

    second = client.get("/api/jobs?page=2&page_size=2").json()
    assert second["total"] == 3
    assert second["page"] == 2
    assert len(second["items"]) == 1

    clamped = client.get("/api/jobs?page=1&page_size=500").json()
    assert clamped["page_size"] == 200


# ---------------------------------------------------------------------------
# GET /api/jobs/{id} — explainable detail (TRAK-04, TRAK-07)
# ---------------------------------------------------------------------------


def test_detail_carries_breakdown_facts_events_and_notes(client, make_session):
    session = make_session()
    try:
        acme = _mk_company(session, "Acme")
        now = datetime.now(UTC)
        job = _mk_job(
            session,
            acme,
            title="Backend Engineer",
            dedup_key="acme:1",
            score=4.25,
            first_seen_at=now - timedelta(days=10),
            dimensions=_DIMENSIONS,
            flags={"stretch_role": True},
        )
        # A repost: identical title at the same employer -> repost_count 1.
        _mk_job(
            session,
            acme,
            title="Backend Engineer",
            dedup_key="acme:2",
            first_seen_at=now - timedelta(days=1),
        )
        session.add(
            StatusEvent(
                job_id=job.id,
                from_status=None,
                to_status=JobStatus.NEW,
                changed_at=now - timedelta(days=3),
                source=StatusEventSource.USER,
            )
        )
        session.add(
            StatusEvent(
                job_id=job.id,
                from_status=JobStatus.NEW,
                to_status=JobStatus.SHORTLISTED,
                changed_at=now - timedelta(days=1),
                source=StatusEventSource.USER,
            )
        )
        session.add(
            FeedbackNote(
                job_id=job.id,
                text="follow up Monday",
                source=FeedbackSource.JOB_NOTE,
            )
        )
        job_id = job.id
        session.commit()
    finally:
        session.close()

    resp = client.get(f"/api/jobs/{job_id}")
    assert resp.status_code == 200
    body = resp.json()

    assert body["company_name"] == "Acme"
    assert body["score_overall"] == 4.25
    assert body["score_dimensions"]["role_fit"] == {
        "score": 5,
        "reason": "direct match for the stack",
    }
    assert set(body["score_dimensions"]) == {
        "role_fit",
        "seniority_fit",
        "employer_fit",
        "trajectory",
    }
    assert body["score_flags"] == {"stretch_role": True}
    assert body["open_duration_days"] == 10
    assert body["repost_count"] == 1
    assert [e["to_status"] for e in body["status_events"]] == ["new", "shortlisted"]
    assert body["status_events"][0]["changed_at"] is not None
    assert [n["text"] for n in body["notes"]] == ["follow up Monday"]


def test_detail_sanitizes_employer_html(client, make_session):
    session = make_session()
    try:
        acme = _mk_company(session, "Acme")
        job = _mk_job(
            session,
            acme,
            title="Backend Engineer",
            dedup_key="acme:1",
            description=(
                "<script>alert('x')</script><p>Real <b>text</b></p>"
                "<img src=x onerror=alert(1)>"
            ),
        )
        job_id = job.id
        session.commit()
    finally:
        session.close()

    body = client.get(f"/api/jobs/{job_id}").json()
    description = body["description"]
    assert "Real text" in description
    assert "<" not in description
    assert "onerror" not in description
    assert "<script" not in description


def test_detail_unknown_id_is_404(client):
    resp = client.get(f"/api/jobs/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# PATCH /api/jobs/{id}/status — one action, timestamped event (TRAK-01/03, D-10)
# ---------------------------------------------------------------------------


def _events(make_session, job_id):
    session = make_session()
    try:
        return list(
            session.execute(
                select(StatusEvent)
                .where(StatusEvent.job_id == job_id)
                .order_by(StatusEvent.changed_at.asc())
            )
            .scalars()
            .all()
        )
    finally:
        session.close()


def _seed_new_job(make_session, *, title="Backend Engineer"):
    session = make_session()
    try:
        company = _mk_company(session, "Acme")
        job = _mk_job(session, company, title=title, dedup_key="acme:1")
        job_id = job.id
        session.commit()
    finally:
        session.close()
    return job_id


def test_patch_status_records_timestamped_event(client, make_session):
    job_id = _seed_new_job(make_session)

    resp = client.patch(f"/api/jobs/{job_id}/status", json={"status": "shortlisted"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["id"] == str(job_id)
    assert body["status"] == "shortlisted"
    assert body["changed_at"] is not None

    events = _events(make_session, job_id)
    assert len(events) == 1
    assert events[0].from_status == JobStatus.NEW
    assert events[0].to_status == JobStatus.SHORTLISTED
    assert events[0].changed_at is not None


def test_two_transitions_append_second_event_in_order(client, make_session):
    job_id = _seed_new_job(make_session)

    assert (
        client.patch(
            f"/api/jobs/{job_id}/status", json={"status": "shortlisted"}
        ).status_code
        == 200
    )
    assert (
        client.patch(
            f"/api/jobs/{job_id}/status", json={"status": "applied"}
        ).status_code
        == 200
    )

    events = _events(make_session, job_id)
    assert len(events) == 2
    assert (events[0].from_status, events[0].to_status) == (
        JobStatus.NEW,
        JobStatus.SHORTLISTED,
    )
    assert (events[1].from_status, events[1].to_status) == (
        JobStatus.SHORTLISTED,
        JobStatus.APPLIED,
    )
    assert events[0].changed_at is not None and events[1].changed_at is not None


def test_patch_invalid_status_is_422(client, make_session):
    job_id = _seed_new_job(make_session)
    resp = client.patch(f"/api/jobs/{job_id}/status", json={"status": "wizard"})
    assert resp.status_code == 422


def test_patch_unknown_id_is_404(client):
    resp = client.patch(
        f"/api/jobs/{uuid.uuid4()}/status", json={"status": "shortlisted"}
    )
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/jobs/{id}/notes — freeform feedback (TRAK-02)
# ---------------------------------------------------------------------------


def test_post_note_created_and_visible_in_detail(client, make_session):
    job_id = _seed_new_job(make_session)

    resp = client.post(
        f"/api/jobs/{job_id}/notes", json={"text": "follow up Monday"}
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["text"] == "follow up Monday"
    assert body["created_at"] is not None

    detail = client.get(f"/api/jobs/{job_id}").json()
    assert [n["id"] for n in detail["notes"]] == [body["id"]]

    session = make_session()
    try:
        note = session.get(FeedbackNote, uuid.UUID(body["id"]))
        assert note is not None
        assert note.source == FeedbackSource.JOB_NOTE
        assert note.job_id == job_id
    finally:
        session.close()


def test_post_empty_note_is_422(client, make_session):
    job_id = _seed_new_job(make_session)
    resp = client.post(f"/api/jobs/{job_id}/notes", json={"text": ""})
    assert resp.status_code == 422
