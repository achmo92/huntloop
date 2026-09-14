"""Diagnostics API contract tests (UI-05, D-16).

Consumes tests/api/conftest.py unedited. RED-phase note: nothing from
`huntloop.api.routers.diagnostics` is imported at module scope, so the
unimplemented routes fail at request time (404) with real assertion failures.
"""

from datetime import UTC, datetime

from huntloop.api.deps import get_llm
from huntloop.api.routers import diagnostics
from huntloop.config import load_config
from huntloop.db.models import AtsPlatform, Company
from huntloop.discovery.ats.base import FetchResult, FetchStatus, RawListing
from huntloop.llm.client import LlmResponseError

# ---------------------------------------------------------------------------
# POST /api/diagnostics/database — both stores reachable
# ---------------------------------------------------------------------------


def test_database_check_passes_on_both_stores(client, api_engine, monkeypatch):
    monkeypatch.setattr(diagnostics, "get_engine", lambda: api_engine)
    monkeypatch.setattr(diagnostics, "get_credentials_engine", lambda: api_engine)

    resp = client.post("/api/diagnostics/database")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pass"
    assert body["remedy"] is None


# ---------------------------------------------------------------------------
# POST /api/diagnostics/llm — one click, human remedy on failure
# ---------------------------------------------------------------------------


def test_llm_check_passes_when_endpoint_answers(client, monkeypatch):
    client.app.dependency_overrides[get_llm] = lambda: object()
    monkeypatch.setattr(diagnostics, "complete_json", lambda *a, **k: object())

    resp = client.post("/api/diagnostics/llm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pass"
    assert body["remedy"] is None


def test_llm_check_failure_is_generic_and_gives_remedy(client, monkeypatch):
    client.app.dependency_overrides[get_llm] = lambda: object()

    def _boom(*args, **kwargs):
        raise LlmResponseError("connection refused")

    monkeypatch.setattr(diagnostics, "complete_json", _boom)

    resp = client.post("/api/diagnostics/llm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fail"
    # T-04-09: the detail is generic — never the internal endpoint or raw exc.
    assert load_config().openai_base_url not in body["detail"]
    assert "connection refused" not in body["detail"]
    assert body["remedy"]
    assert "API access" in body["remedy"]


def test_llm_check_pass_omits_the_endpoint_url(client, monkeypatch):
    client.app.dependency_overrides[get_llm] = lambda: object()
    monkeypatch.setattr(diagnostics, "complete_json", lambda *a, **k: object())

    resp = client.post("/api/diagnostics/llm")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pass"
    assert load_config().openai_base_url not in body["detail"]


def test_database_failure_is_generic(client, monkeypatch):
    def _boom():
        raise RuntimeError("secret dsn leaked")

    monkeypatch.setattr(diagnostics, "get_engine", _boom)

    resp = client.post("/api/diagnostics/database")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fail"
    assert "secret dsn leaked" not in body["detail"]
    assert body["remedy"]


# ---------------------------------------------------------------------------
# POST /api/diagnostics/employer-fetch — one live board fetch
# ---------------------------------------------------------------------------


class _FakeAdapter:
    def fetch(self, slug, *, client=None):
        return FetchResult(
            status=FetchStatus.OK,
            listings=[
                RawListing(external_id="1", url="https://acme.test/1", title="Engineer")
            ],
        )


def test_employer_fetch_passes_with_listing_count(client, make_session, monkeypatch):
    session = make_session()
    try:
        session.add(
            Company(
                name="Acme",
                ats=AtsPlatform.GREENHOUSE,
                ats_identifier="acme",
                resolved_at=datetime.now(UTC),
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(diagnostics, "get_adapter", lambda platform: _FakeAdapter())

    resp = client.post("/api/diagnostics/employer-fetch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "pass"
    assert "1" in body["detail"]
    assert "Acme" in body["detail"]
    assert body["remedy"] is None


def test_employer_fetch_without_resolved_employer_is_honest(client):
    resp = client.post("/api/diagnostics/employer-fetch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fail"
    assert "resolve at least one employer" in body["remedy"].lower()


class _BoomAdapter:
    def fetch(self, slug, *, client=None):
        raise RuntimeError("raw internal")


def test_employer_fetch_failure_is_generic(client, make_session, monkeypatch):
    """A raw adapter exception must never reach the diagnostics body (T-04-09)."""
    session = make_session()
    try:
        session.add(
            Company(
                name="Acme",
                ats=AtsPlatform.GREENHOUSE,
                ats_identifier="acme",
                resolved_at=datetime.now(UTC),
            )
        )
        session.commit()
    finally:
        session.close()

    monkeypatch.setattr(diagnostics, "get_adapter", lambda platform: _BoomAdapter())

    resp = client.post("/api/diagnostics/employer-fetch")
    assert resp.status_code == 200
    body = resp.json()
    assert body["status"] == "fail"
    assert "raw internal" not in body["detail"]
    assert body["remedy"]


# ---------------------------------------------------------------------------
# Structural: three independent checks, not one combined endpoint (D-16)
# ---------------------------------------------------------------------------


def test_diagnostics_expose_three_separate_endpoints(client):
    paths = client.get("/openapi.json").json()["paths"]
    diag_paths = {path for path in paths if path.startswith("/api/diagnostics")}
    assert diag_paths == {
        "/api/diagnostics/llm",
        "/api/diagnostics/database",
        "/api/diagnostics/employer-fetch",
    }
