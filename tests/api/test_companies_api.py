"""Employer-registry API contract tests (REG-04, REG-06, D-07, D-08).

Consumes tests/api/conftest.py unedited: `client` speaks HTTP against the app
with both session dependencies overridden onto a tmp SQLite file, and
`make_session` seeds rows directly over that same file.

RED-phase note: these tests import nothing from `huntloop.api.routers.companies`
at module scope, so a missing route fails at request time (404/405), not at
collection time — the TDD RED evidence is a real assertion failure against the
unimplemented contract.
"""

import time
import uuid
from datetime import UTC, datetime

import pytest
from sqlalchemy import select

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

    assert by_name["Acme"]["resolution_status"] == "resolved"
    assert by_name["Acme"]["resolved"] is True
    assert by_name["Acme"]["resolution_detail"] is None
    assert by_name["Globex"]["resolution_status"] == "needs_attention"
    assert by_name["Globex"]["resolved"] is False
    # D-07/GAP-10: the failure state surfaces a generic user-facing message; the
    # raw probe/candidate trail must never reach the wire.
    assert by_name["Globex"]["resolution_detail"] is not None
    assert "tiers ran" not in resp.text
    assert "probes, all failed" not in resp.text


def test_unprobed_company_has_no_resolution_detail(client, make_session):
    """A never-probed employer has no resolution block, so no failure message.

    A freshly added employer must NOT show the failure sentence before any
    probe has run (GAP-10).
    """
    session = make_session()
    try:
        _mk_company(session, "Initech")
        session.commit()
    finally:
        session.close()

    row = client.get("/api/companies").json()[0]
    assert row["resolution_status"] == "needs_attention"
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
    assert body["resolution_status"] == "needs_attention"

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
