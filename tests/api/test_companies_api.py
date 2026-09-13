"""Employer-registry API contract tests (REG-04, REG-06, D-07, D-08).

Consumes tests/api/conftest.py unedited: `client` speaks HTTP against the app
with both session dependencies overridden onto a tmp SQLite file, and
`make_session` seeds rows directly over that same file.

GAP-10 note: `RESOLUTION_FAILURE_MESSAGE` is imported from the router so the
contract asserts the one named constant instead of duplicating the sentence.
"""

import time
import uuid
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from huntloop.api.routers.companies import RESOLUTION_FAILURE_MESSAGE
from huntloop.db.models import AtsPlatform, Company, Job
from huntloop.registry.resolve import ResolutionResult, ResolutionStatus


def _mk_company(
    session,
    name: str,
    *,
    ats: AtsPlatform | None = None,
    ats_identifier: str | None = None,
    ats_config: dict | None = None,
    careers_url: str | None = None,
    resolved_at=None,
    enabled: bool = True,
    consecutive_empty_runs: int = 0,
    last_job_count: int | None = None,
) -> Company:
    company = Company(
        name=name,
        ats=ats,
        ats_identifier=ats_identifier,
        ats_config=ats_config,
        careers_url=careers_url,
        resolved_at=resolved_at,
        enabled=enabled,
        consecutive_empty_runs=consecutive_empty_runs,
        last_job_count=last_job_count,
    )
    session.add(company)
    session.flush()
    return company


# ---------------------------------------------------------------------------
# GET /api/companies — resolution is a STATUS, not an error (REG-04, D-07)
# ---------------------------------------------------------------------------


def test_list_shows_resolved_and_needs_attention_with_detail(client, make_session):
    session = make_session()
    try:
        _mk_company(
            session,
            "Acme",
            ats=AtsPlatform.GREENHOUSE,
            ats_identifier="acme",
            ats_config={"resolution": {"status": "resolved", "reason": ""}},
            resolved_at=datetime.now(UTC),
        )
        _mk_company(
            session,
            "Globex",
            ats_config={
                "resolution": {
                    "status": "unresolved",
                    "reason": "tiers ran: tier1_guess; 12 probes, all failed",
                }
            },
        )
        session.commit()
    finally:
        session.close()

    resp = client.get("/api/companies")
    assert resp.status_code == 200
    by_name = {row["name"]: row for row in resp.json()}

    assert by_name["Acme"].get("resolution_state") == "resolved"
    assert by_name["Acme"]["resolved"] is True
    assert by_name["Acme"]["resolution_detail"] is None
    assert by_name["Globex"].get("resolution_state") == "error"
    assert by_name["Globex"]["resolved"] is False
    # D-07/GAP-10: the failure state surfaces a generic user-facing message; the
    # raw probe/candidate trail must never reach the wire.
    assert by_name["Globex"]["resolution_detail"] == RESOLUTION_FAILURE_MESSAGE
    assert "tiers ran" not in resp.text
    assert "probes, all failed" not in resp.text


def test_unprobed_company_is_added(client, make_session):
    """A never-probed employer reads `added` and has no failure message.

    A freshly added employer must NOT show the failure sentence before any
    probe has run, and its lifecycle state is `added` rather than the old
    two-value `needs_attention` (GAP-10, GAP-13).
    """
    session = make_session()
    try:
        _mk_company(session, "Initech")
        session.commit()
    finally:
        session.close()

    row = client.get("/api/companies").json()[0]
    assert row.get("resolution_state") == "added"
    assert row["resolution_detail"] is None


def test_resolved_company_with_raw_reason_has_no_resolution_detail(
    client, make_session
):
    """A resolved employer never exposes a stale raw reason on the wire (GAP-10)."""
    session = make_session()
    try:
        _mk_company(
            session,
            "Acme",
            ats=AtsPlatform.GREENHOUSE,
            ats_identifier="acme",
            ats_config={
                "resolution": {
                    "status": "resolved",
                    "reason": "tiers ran: tier1_guess; legacy trail",
                }
            },
            resolved_at=datetime.now(UTC),
        )
        session.commit()
    finally:
        session.close()

    row = client.get("/api/companies").json()[0]
    assert row["resolved"] is True
    assert row["resolution_detail"] is None


def test_resolved_row_exposes_platform_and_board_identifier(client, make_session):
    session = make_session()
    try:
        _mk_company(
            session,
            "Acme",
            ats=AtsPlatform.GREENHOUSE,
            ats_identifier="acme",
            resolved_at=datetime.now(UTC),
        )
        session.commit()
    finally:
        session.close()

    row = client.get("/api/companies").json()[0]
    assert row["ats"] == "greenhouse"
    assert row["ats_identifier"] == "acme"


def test_possibly_stale_company_carries_flag_and_message(client, make_session):
    session = make_session()
    try:
        _mk_company(session, "Quiet Co", consecutive_empty_runs=5, last_job_count=0)
        session.commit()
    finally:
        session.close()

    row = client.get("/api/companies").json()[0]
    assert row["possibly_stale"] is True
    assert "consecutive runs" in row["staleness_message"]


def test_list_includes_disabled_companies_in_one_list(client, make_session):
    session = make_session()
    try:
        _mk_company(session, "Active Co", enabled=True)
        _mk_company(session, "Retired Co", enabled=False)
        session.commit()
    finally:
        session.close()

    rows = client.get("/api/companies").json()
    by_name = {row["name"]: row for row in rows}
    assert set(by_name) == {"Active Co", "Retired Co"}
    assert by_name["Retired Co"]["enabled"] is False


# ---------------------------------------------------------------------------
# POST /api/companies — add is a registry write, NOT a live probe (D-05)
# ---------------------------------------------------------------------------


def test_add_returns_201_and_unresolved(client, make_session):
    resp = client.post("/api/companies", json={"name": "Acme"})
    assert resp.status_code == 201
    body = resp.json()
    assert body["name"] == "Acme"
    assert body["enabled"] is True
    assert body["resolved"] is False
    assert body.get("resolution_state") == "added"

    session = make_session()
    try:
        company = session.execute(
            select(Company).where(Company.name == "Acme")
        ).scalar_one()
        assert company.enabled is True
        assert company.resolved_at is None
    finally:
        session.close()


def test_batch_add_creates_every_accepted_employer(client, make_session):
    resp = client.post(
        "/api/companies/batch",
        json={
            "names": ["Acme", "Globex"],
            "careers_urls": {"Acme": "https://acme.test/careers"},
        },
    )
    assert resp.status_code == 201
    added = resp.json()["added"]
    assert {row["name"] for row in added} == {"Acme", "Globex"}
    by_name = {row["name"]: row for row in added}
    assert by_name["Acme"]["careers_url"] == "https://acme.test/careers"
    assert by_name["Globex"]["careers_url"] is None


# ---------------------------------------------------------------------------
# PATCH /api/companies/{id} — disable is a toggle in place (REG-06, D-08)
# ---------------------------------------------------------------------------


def test_disable_retains_row_and_history(client, make_session):
    session = make_session()
    try:
        company = _mk_company(session, "Acme", enabled=True)
        session.add(
            Job(
                company_id=company.id,
                dedup_key="acme:1",
                url="https://acme.test/jobs/1",
                title="Backend Engineer",
            )
        )
        company_id = company.id
        session.commit()
    finally:
        session.close()

    resp = client.patch(f"/api/companies/{company_id}", json={"enabled": False})
    assert resp.status_code == 200
    assert resp.json()["enabled"] is False

    # REG-06: the row survives with its job. Plan 04-04's GET /api/jobs is not
    # merged yet (only 04-01/04-02 are), so history survival is asserted
    # directly over the seeded session rather than through the jobs endpoint.
    session = make_session()
    try:
        company = session.get(Company, company_id)
        assert company is not None
        assert company.enabled is False
        job = session.execute(
            select(Job).where(Job.company_id == company_id)
        ).scalar_one()
        assert job.title == "Backend Engineer"
    finally:
        session.close()


def test_patch_unknown_id_404(client):
    resp = client.patch(f"/api/companies/{uuid.uuid4()}", json={"enabled": False})
    assert resp.status_code == 404


# ---------------------------------------------------------------------------
# POST /api/companies/{id}/resolve — 202 + background state update (D-07)
# ---------------------------------------------------------------------------


def _resolve_on_test_engine(monkeypatch, api_engine, result: ResolutionResult):
    """Point the background runner at the tmp test engine and stub the probe.

    `resolve_company_in_background` opens its OWN session via
    `make_session_factory(get_engine())`; in tests that must be the fixture
    engine, so `background.get_engine` is patched. `resolve_employer` is
    imported at module top precisely so tests can patch
    `huntloop.api.background.resolve_employer` here.
    """
    from huntloop.api import background

    monkeypatch.setattr(background, "get_engine", lambda: api_engine)
    monkeypatch.setattr(background, "resolve_employer", lambda **kwargs: result)


def _resolved_result(*, careers_url=None) -> ResolutionResult:
    return ResolutionResult(
        status=ResolutionStatus.RESOLVED,
        platform="greenhouse",
        slug="acme",
        careers_url=careers_url,
    )


def _poll_resolved(make_session, company_id, timeout=5.0) -> Company:
    deadline = time.time() + timeout
    while time.time() < deadline:
        session = make_session()
        try:
            company = session.get(Company, company_id)
            if company is not None and company.resolved_at is not None:
                return company
        finally:
            session.close()
        time.sleep(0.05)
    pytest.fail("background resolution did not complete before the poll timeout")


def _poll_state(client, name: str, state: str, timeout=5.0) -> dict:
    """GET /api/companies until the named row reports ``state`` (GAP-13).

    The background probe runs on its own thread, so the wire row is the only
    truthful signal that the lifecycle has landed.
    """
    deadline = time.time() + timeout
    while time.time() < deadline:
        row = next(
            (r for r in client.get("/api/companies").json() if r["name"] == name),
            None,
        )
        if row is not None and row.get("resolution_state") == state:
            return row
        time.sleep(0.05)
    pytest.fail(f"{name} never reached resolution_state={state!r}")


def test_resolve_returns_202_and_background_updates_state(
    client, make_session, api_engine, monkeypatch
):
    session = make_session()
    try:
        company = _mk_company(session, "Acme", careers_url="https://acme.test/careers")
        company_id = company.id
        session.commit()
    finally:
        session.close()

    _resolve_on_test_engine(monkeypatch, api_engine, _resolved_result())

    resp = client.post(f"/api/companies/{company_id}/resolve")
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}

    company = _poll_resolved(make_session, company_id)
    assert company.ats == AtsPlatform.GREENHOUSE
    assert company.ats_identifier == "acme"


def test_resolve_sets_resolving_marker_synchronously(
    client, make_session, monkeypatch
):
    """The 202 commits the Resolving marker before returning (GAP-13).

    The background thread is stubbed out entirely, so the only thing that can
    make the row read `resolving` is the marker the request itself commits.
    """
    session = make_session()
    try:
        company = _mk_company(session, "Acme", careers_url="https://acme.test/careers")
        company_id = company.id
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        "huntloop.api.routers.companies.run_in_background", lambda *a, **k: None
    )

    resp = client.post(f"/api/companies/{company_id}/resolve")
    assert resp.status_code == 202

    row = next(r for r in client.get("/api/companies").json() if r["name"] == "Acme")
    assert row.get("resolution_state") == "resolving"
    assert row["resolved"] is False
    assert row["resolution_detail"] is None


def test_resolve_batch_sets_resolving_marker_for_each(
    client, make_session, monkeypatch
):
    """resolve-batch commits the Resolving marker for every id before 202 (GAP-13)."""
    session = make_session()
    try:
        first = _mk_company(session, "Acme")
        second = _mk_company(session, "Globex")
        ids = [first.id, second.id]
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(
        "huntloop.api.routers.companies.run_in_background", lambda *a, **k: None
    )

    resp = client.post(
        "/api/companies/resolve-batch", json={"ids": [str(i) for i in ids]}
    )
    assert resp.status_code == 202
    assert resp.json() == {"queued": 2}

    rows = {r["name"]: r for r in client.get("/api/companies").json()}
    assert rows["Acme"].get("resolution_state") == "resolving"
    assert rows["Globex"].get("resolution_state") == "resolving"


def test_resolve_completion_replaces_marker_with_resolved_state(
    client, make_session, api_engine, monkeypatch
):
    """A completed probe lands `resolved` and leaves no `resolving` marker (GAP-13)."""
    session = make_session()
    try:
        company = _mk_company(session, "Acme", careers_url="https://acme.test/careers")
        company_id = company.id
        session.commit()
    finally:
        session.close()

    _resolve_on_test_engine(monkeypatch, api_engine, _resolved_result())

    assert client.post(f"/api/companies/{company_id}/resolve").status_code == 202

    row = _poll_state(client, "Acme", "resolved")
    assert row["resolved"] is True
    assert row["ats"] == "greenhouse"
    assert row["ats_identifier"] == "acme"

    session = make_session()
    try:
        company = session.get(Company, company_id)
        assert company.ats_config["resolution"]["state"] == "resolved"
    finally:
        session.close()


def test_failed_resolution_shows_error_state_and_generic_detail(
    client, make_session, api_engine, monkeypatch
):
    """A failed probe lands `error` with the generic sentence, not the raw trail."""
    session = make_session()
    try:
        company = _mk_company(
            session, "Globex", careers_url="https://globex.test/careers"
        )
        company_id = company.id
        session.commit()
    finally:
        session.close()

    _resolve_on_test_engine(
        monkeypatch,
        api_engine,
        ResolutionResult(
            status=ResolutionStatus.UNRESOLVED,
            reason="tiers ran: tier1_guess; 3 probes, all failed",
        ),
    )

    assert client.post(f"/api/companies/{company_id}/resolve").status_code == 202

    row = _poll_state(client, "Globex", "error")
    assert row["resolved"] is False
    assert row["resolution_detail"] == RESOLUTION_FAILURE_MESSAGE

    resp = client.get("/api/companies")
    assert "tiers ran" not in resp.text
    assert "probes, all failed" not in resp.text


def test_unexpected_probe_exception_records_error_and_clears_marker(
    client, make_session, api_engine, monkeypatch
):
    """An unexpected probe exception must land `error`, never a stuck marker (GAP-13)."""
    session = make_session()
    try:
        company = _mk_company(
            session, "Initech", careers_url="https://initech.test/careers"
        )
        company_id = company.id
        session.commit()
    finally:
        session.close()

    from huntloop.api import background

    monkeypatch.setattr(background, "get_engine", lambda: api_engine)

    def _boom(**_kwargs):
        raise RuntimeError("kaboom")

    monkeypatch.setattr(background, "resolve_employer", _boom)

    assert client.post(f"/api/companies/{company_id}/resolve").status_code == 202

    row = _poll_state(client, "Initech", "error")
    assert row["resolved"] is False
    assert row["resolution_detail"] == RESOLUTION_FAILURE_MESSAGE


def test_stale_resolving_marker_older_than_completion_reads_resolved(
    client, make_session
):
    """A stale `resolving` marker never overrides a newer successful completion."""
    resolved_at = datetime.now(UTC)
    session = make_session()
    try:
        _mk_company(
            session,
            "Acme",
            ats=AtsPlatform.GREENHOUSE,
            ats_identifier="acme",
            ats_config={
                "resolution": {
                    "state": "resolving",
                    "started_at": (resolved_at - timedelta(seconds=30)).isoformat(),
                }
            },
            resolved_at=resolved_at,
        )
        session.commit()
    finally:
        session.close()

    row = client.get("/api/companies").json()[0]
    assert row.get("resolution_state") == "resolved"


def test_resolve_batch_queues_each_employer(
    client, make_session, api_engine, monkeypatch
):
    session = make_session()
    try:
        first = _mk_company(session, "Acme", careers_url="https://acme.test/careers")
        second = _mk_company(session, "Globex", careers_url="https://globex.test/careers")
        ids = [first.id, second.id]
        session.commit()
    finally:
        session.close()

    _resolve_on_test_engine(monkeypatch, api_engine, _resolved_result())

    resp = client.post(
        "/api/companies/resolve-batch", json={"ids": [str(i) for i in ids]}
    )
    assert resp.status_code == 202
    assert resp.json() == {"queued": 2}

    for company_id in ids:
        _poll_resolved(make_session, company_id)


# ---------------------------------------------------------------------------
# Structural guarantee: there is no DELETE path (REG-06)
# ---------------------------------------------------------------------------


def test_no_delete_verb_is_mounted(client):
    paths = client.get("/openapi.json").json()["paths"]
    for path, methods in paths.items():
        if path.startswith("/api/companies"):
            assert "delete" not in methods, f"DELETE {path} must not exist"
