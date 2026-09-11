"""App-factory contract tests: health endpoint and the mounted /api surface."""


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
