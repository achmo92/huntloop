"""Run API contract tests (RUN-02, RUN-06, RUN-09, D-17).

Consumes tests/api/conftest.py unedited: `client` speaks HTTP against the app
with both session dependencies overridden onto a tmp SQLite file, and
`make_session` seeds rows directly over that same file.

RED-phase note: nothing from `huntloop.api.routers.runs` is imported at module
scope, so the unimplemented routes fail at request time (404/405) with a real
assertion failure rather than a collection error.
"""

import threading
import uuid

from huntloop.cli.render import RUN_HISTORY_FIELDS
from huntloop.db.models import Company, RunTrigger
from huntloop.db.repository import RunRepository

_COUNTERS = {
    "companies_checked": 3,
    "listings_fetched": 40,
    "after_dedup": 30,
    "after_deterministic": 20,
    "after_triage": 12,
    "scored": 8,
    "new_jobs_written": 5,
    "tokens_in": 1000,
    "tokens_out": 250,
}


def _finish(session, run_id, *, status, cost_usd=0.0123, error_summary=None):
    RunRepository(session).finish(
        run_id,
        status=status,
        cost_usd=cost_usd,
        error_summary=error_summary,
        **_COUNTERS,
    )


# ---------------------------------------------------------------------------
# GET /api/runs — run history over HTTP (RUN-09)
# ---------------------------------------------------------------------------


def test_history_returns_newest_first_with_full_field_shape(client, make_session):
    from huntloop.db.models import RunStatus

    session = make_session()
    try:
        repo = RunRepository(session)
        older = repo.start(RunTrigger.SCHEDULED)
        _finish(session, older.id, status=RunStatus.SUCCESS, cost_usd=0.0123)
        newer = repo.start(RunTrigger.MANUAL)
        _finish(
            session,
            newer.id,
            status=RunStatus.PARTIAL,
            cost_usd=1.5,
            error_summary="1 employer failed",
        )
        session.commit()
        newer_id, older_id = newer.id, older.id
    finally:
        session.close()

    resp = client.get("/api/runs")
    assert resp.status_code == 200
    body = resp.json()
    assert [row["id"] for row in body] == [str(newer_id), str(older_id)]

    # RUN_HISTORY_FIELDS is the single source of truth this API reshapes.
    expected_fields = {name for name, _ in RUN_HISTORY_FIELDS}
    assert expected_fields <= set(body[0].keys())
    assert {"id", "finished_at", "error_summary"} <= set(body[0].keys())

    assert body[0]["status"] == "partial"
    assert body[0]["trigger"] == "manual"
    assert body[0]["error_summary"] == "1 employer failed"
    # cost_usd quantized to 4dp — never the $0.012300000000000001 float repr.
    assert body[1]["cost_usd"] == 0.0123


def test_empty_history_is_200_with_empty_list(client):
    resp = client.get("/api/runs")
    assert resp.status_code == 200
    assert resp.json() == []


def test_history_limit_is_honored(client, make_session):
    from huntloop.db.models import RunStatus

    session = make_session()
    try:
        repo = RunRepository(session)
        for _ in range(3):
            run = repo.start(RunTrigger.SCHEDULED)
            _finish(session, run.id, status=RunStatus.SUCCESS)
        session.commit()
    finally:
        session.close()

    resp = client.get("/api/runs?limit=2")
    assert resp.status_code == 200
    assert len(resp.json()) == 2


# ---------------------------------------------------------------------------
# GET /api/runs/{id} — per-run detail incl. errors (RUN-06)
# ---------------------------------------------------------------------------


def test_detail_includes_errors_with_company_and_stage(client, make_session):
    from huntloop.db.models import RunStatus

    session = make_session()
    try:
        company = Company(name="Acme")
        session.add(company)
        session.flush()
        repo = RunRepository(session)
        run = repo.start(RunTrigger.SCHEDULED)
        repo.record_error(run.id, company.id, "fetch", "timed out after 15s")
        repo.record_error(run.id, None, "score", "model returned bad JSON")
        _finish(session, run.id, status=RunStatus.PARTIAL, error_summary="2 errors")
        session.commit()
        run_id = run.id
    finally:
        session.close()

    resp = client.get(f"/api/runs/{run_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["after_dedup"] == _COUNTERS["after_dedup"]
    assert {(e["company_name"], e["stage"], e["message"]) for e in body["errors"]} == {
        ("Acme", "fetch", "timed out after 15s"),
        (None, "score", "model returned bad JSON"),
    }


def test_detail_unknown_run_is_404(client, make_session):
    from huntloop.db.models import RunStatus

    session = make_session()
    try:
        repo = RunRepository(session)
        known = repo.start(RunTrigger.SCHEDULED)
        _finish(session, known.id, status=RunStatus.SUCCESS)
        session.commit()
        known_id = known.id
    finally:
        session.close()

    # Prove the 404 below comes from the run lookup, not an unmounted router.
    assert client.get(f"/api/runs/{known_id}").status_code == 200
    resp = client.get(f"/api/runs/{uuid.uuid4()}")
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/runs — fire-and-observe trigger (RUN-02, D-17)
# ---------------------------------------------------------------------------


def test_trigger_returns_202_and_runs_in_background(client, monkeypatch):
    from huntloop.api.routers import runs as runs_router

    done = threading.Event()
    calls = {}

    def _fake_run_discovery(*, sessionmaker, trigger):
        calls["trigger"] = trigger
        calls["sessionmaker"] = sessionmaker
        done.set()
        return object()

    monkeypatch.setattr(runs_router, "run_discovery", _fake_run_discovery)

    resp = client.post("/api/runs")
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}

    # The request returned BEFORE discovery ran (fire-and-observe): the
    # background thread completes on its own and the test joins it here.
    assert done.wait(timeout=5.0), "background discovery never executed"
    assert calls["trigger"] is RunTrigger.MANUAL


def test_trigger_with_run_in_progress_is_409(client, make_session):
    session = make_session()
    try:
        run = RunRepository(session).start(RunTrigger.MANUAL)
        session.commit()
        run_id = run.id
    finally:
        session.close()

    resp = client.post("/api/runs")
    assert resp.status_code == 409
    body = resp.json()
    assert body["run_id"] == str(run_id)
    assert "in progress" in body["detail"].lower()
