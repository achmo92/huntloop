"""App-factory contract tests: health endpoint and the mounted /api surface."""

import pytest
from fastapi.testclient import TestClient

from huntloop.api.app import create_app

# Sentinel baked into the fake dist's index.html: proves a response body is
# the SPA shell (not an API JSON error or a directory listing).
SPA_SHELL_SENTINEL = "huntloop-spa-shell"

# Every client-side route web/src/App.tsx registers (the fallback must cover
# them all — a URL the app shows must survive a direct load/refresh, UI-01).
CLIENT_ROUTES = [
    "/listings",
    "/employers",
    "/criteria",
    "/runs",
    "/settings",
    "/onboarding",
]


def _spa_client(monkeypatch, tmp_path) -> TestClient:
    """TestClient over create_app() with a fake built frontend.

    A tmp_path dist holding only index.html is monkeypatched over
    ``huntloop.api.app._frontend_dist`` so the SPA mount is hermetic (the
    repo checkout may or may not carry a real web/dist build). GET-only
    static/404 paths are exercised, so no DB fixtures are needed.
    """
    dist = tmp_path / "dist"
    dist.mkdir()
    (dist / "index.html").write_text(
        "<!doctype html><html><head>"
        f"<title>{SPA_SHELL_SENTINEL}</title></head>"
        '<body><div id="root"></div></body></html>'
    )
    monkeypatch.setattr("huntloop.api.app._frontend_dist", lambda: dist)
    return TestClient(create_app())


@pytest.mark.parametrize("route", CLIENT_ROUTES)
def test_client_route_deep_links_serve_spa_shell(monkeypatch, tmp_path, route):
    """GAP-1: direct navigation/refresh on a client route returns the shell."""
    client = _spa_client(monkeypatch, tmp_path)
    resp = client.get(route)
    assert resp.status_code == 200
    assert SPA_SHELL_SENTINEL in resp.text


def test_root_still_serves_index_html(monkeypatch, tmp_path):
    """html=True root behavior must not regress: GET / is the shell."""
    client = _spa_client(monkeypatch, tmp_path)
    resp = client.get("/")
    assert resp.status_code == 200
    assert SPA_SHELL_SENTINEL in resp.text


def test_head_on_unknown_path_serves_shell(monkeypatch, tmp_path):
    """HEAD falls back like GET (the method guard admits GET and HEAD only)."""
    client = _spa_client(monkeypatch, tmp_path)
    resp = client.head("/listings")
    assert resp.status_code == 200


def test_unknown_api_path_still_404_not_shell(monkeypatch, tmp_path):
    """Unknown /api/* keeps API 404 semantics — never the SPA shell."""
    client = _spa_client(monkeypatch, tmp_path)
    resp = client.get("/api/nonexistent")
    assert resp.status_code == 404
    assert SPA_SHELL_SENTINEL not in resp.text


def test_api_health_unchanged_with_dist_mounted(monkeypatch, tmp_path):
    client = _spa_client(monkeypatch, tmp_path)
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_non_get_unknown_path_is_not_served_the_shell(monkeypatch, tmp_path):
    """Only GET/HEAD fall back; a POST to an unknown path never sees the shell."""
    client = _spa_client(monkeypatch, tmp_path)
    resp = client.post("/listings")
    assert resp.status_code == 405
    assert SPA_SHELL_SENTINEL not in resp.text


def test_no_dist_serves_api_only(monkeypatch):
    """Without a frontend build nothing is mounted and client routes 404.

    The fallback must never fabricate a shell from nothing. _frontend_dist is
    forced to None (the repo checkout carries a real web/dist, so the default
    resolver cannot be used to prove the no-build posture hermetically).
    """
    monkeypatch.setattr("huntloop.api.app._frontend_dist", lambda: None)
    client = TestClient(create_app())
    resp = client.get("/listings")
    assert resp.status_code == 404
    health = client.get("/api/health")
    assert health.status_code == 200
    assert health.json() == {"status": "ok"}


def test_health_ok(client):
    resp = client.get("/api/health")
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_all_api_prefixes_mounted(client):
    """GET /openapi.json is 200 and reflects the routes this plan ships.

    The six stub routers (companies/jobs/runs/dashboard/settings/diagnostics)
    register no paths until their owning plans (04-02..04-06) add routes —
    their mounting is proven structurally by create_app() importing and
    including them. This assertion list is the growing record of the real
    /api contract as later plans land their routes.
    """
    resp = client.get("/openapi.json")
    assert resp.status_code == 200
    paths = set(resp.json()["paths"])
    assert "/api/health" in paths
    # The criteria contract landed by plan 04-01 Task 2:
    assert "/api/criteria" in paths
    assert "/api/criteria/versions" in paths
    assert "/api/criteria/versions/{version}" in paths
    assert "/api/criteria/describe" in paths
