"""Request-boundary security tests (T-04-02, plan 04-26).

The API is intentionally unauthenticated (PROJECT.md private-network trust
model); the browser is the boundary. These tests pin the boundary:

- state-changing requests from a foreign browser origin (or an opaque ``null``
  origin, or a mismatched Referer) are rejected 403 before any handler runs;
- same-origin browser traffic and headerless non-browser clients pass unchanged;
- safe methods (GET/HEAD/OPTIONS) are never origin-gated;
- a Host that is neither an allowlisted hostname nor a bare IP literal is
  rejected 400 (DNS-rebinding defense), while LAN IP literals and localhost
  keep working so UI-01's any-device access survives.

Consumes tests/api/conftest.py unedited: the ``client`` fixture speaks to
``create_app()`` with Host ``testserver`` (TestClient's default base URL).
"""

from fastapi.testclient import TestClient

from huntloop.api.app import create_app
from huntloop.config import load_config

# ---------------------------------------------------------------------------
# T-04-02: cross-origin state changes are rejected (CSRF / DNS rebinding)
# ---------------------------------------------------------------------------


def test_cross_origin_post_is_rejected_403(client):
    resp = client.post(
        "/api/companies",
        json={"name": "Acme"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403
    # The write never happened: the registry is still empty.
    assert client.get("/api/companies").json() == []


def test_origin_null_is_rejected_403(client):
    resp = client.post(
        "/api/companies",
        json={"name": "Acme"},
        headers={"Origin": "null"},
    )
    assert resp.status_code == 403


def test_referer_fallback_is_rejected_403(client):
    """No Origin header: the Referer is the browser's remaining origin signal."""
    resp = client.post(
        "/api/companies",
        json={"name": "Acme"},
        headers={"Referer": "https://evil.example/page"},
    )
    assert resp.status_code == 403


def test_matching_origin_passes(client):
    """A genuine same-origin SPA call reaches the endpoint unchanged."""
    resp = client.post(
        "/api/companies",
        json={"name": "Acme"},
        headers={"Origin": "http://testserver"},
    )
    assert resp.status_code == 201


def test_no_origin_header_passes(client):
    """A non-browser client (curl, the CLI, tests) sends no Origin at all.

    Documented choice: browsers always attach Origin to cross-origin
    state-changing requests, so omitting it cannot evade enforcement in a
    browser while keeping every existing headerless API test green.
    """
    resp = client.post("/api/companies", json={"name": "Acme"})
    assert resp.status_code == 201


def test_safe_methods_ignore_origin(client):
    """GET/HEAD are not origin-gated: reads never need a CSRF boundary."""
    resp = client.get("/api/health", headers={"Origin": "https://evil.example"})
    assert resp.status_code == 200


def test_cross_origin_put_is_rejected_403(client):
    resp = client.put(
        "/api/settings/models",
        json={"triage": "x"},
        headers={"Origin": "https://evil.example"},
    )
    assert resp.status_code == 403


# ---------------------------------------------------------------------------
# T-04-02: Host allowlist with LAN IP-literal passthrough (DNS rebinding)
# ---------------------------------------------------------------------------


def test_unknown_host_is_rejected_400():
    """A domain the operator never configured is rejected before routing."""
    with TestClient(create_app(), base_url="http://evil.example") as evil:
        assert evil.get("/api/health").status_code == 400


def test_lan_ip_literal_host_is_allowed():
    """UI-01 survives: bare IP hosts need no per-host configuration.

    A DNS-rebinding attack carries the attacker's *hostname*; a bare IP-literal
    Host can only be reached by deliberately navigating to the LAN address.
    """
    with TestClient(create_app(), base_url="http://192.168.1.50:8000") as lan:
        assert lan.get("/api/health").status_code == 200


def test_localhost_and_allowlisted_host_pass():
    with TestClient(create_app(), base_url="http://localhost:8000") as local:
        assert local.get("/api/health").status_code == 200


# ---------------------------------------------------------------------------
# T-04-02: the allowlist is configurable from the environment
# ---------------------------------------------------------------------------


def test_config_parses_allowed_hosts_env(monkeypatch):
    monkeypatch.setenv("HUNTLOOP_ALLOWED_HOSTS", "localhost,127.0.0.1,box.lan")
    assert load_config().allowed_hosts == ("localhost", "127.0.0.1", "box.lan")


def test_config_allowed_hosts_defaults_to_local_and_testserver(monkeypatch):
    monkeypatch.delenv("HUNTLOOP_ALLOWED_HOSTS", raising=False)
    assert load_config().allowed_hosts == ("localhost", "127.0.0.1", "testserver")


def test_config_allowed_origins_defaults_to_empty(monkeypatch):
    monkeypatch.delenv("HUNTLOOP_ALLOWED_ORIGINS", raising=False)
    assert load_config().allowed_origins == ()

    monkeypatch.setenv("HUNTLOOP_ALLOWED_ORIGINS", "https://huntloop.lan")
    assert load_config().allowed_origins == ("https://huntloop.lan",)
