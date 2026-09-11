"""Onboarding + coverage API contract tests (INTK-05, INTK-06, D-05, D-06).

Consumes tests/api/conftest.py unedited. The LLM is overridden via
`app.dependency_overrides[get_llm]` exactly as test_criteria_api.py does.

RED-phase note: no import of the onboarding router at module scope, so the
target test fails on a real 404 assertion against the unimplemented contract.
"""

import json
from datetime import UTC, datetime
from types import SimpleNamespace

from huntloop.api.deps import get_llm
from huntloop.db.models import AtsPlatform, Company

# ---------------------------------------------------------------------------
# Fixtures/helpers
# ---------------------------------------------------------------------------

CRITERIA = {
    "profile_summary": "Backend engineer, Python-heavy, EU remote",
    "seniority_min": "mid",
    "seniority_max": "staff",
    "locations": {
        "eligible_countries": ["DE"],
        "eligible_regions": ["EU"],
        "preferred_cities": ["Berlin"],
    },
    "compensation_floor": {"amount": "100000", "currency": "EUR", "period": "annual"},
    "exclusions": {"title_keywords": [], "employers": ["Revify"]},
    "dimension_weights": {
        "role_fit": 4.0,
        "seniority_fit": 3.0,
        "employer_fit": 2.0,
        "trajectory": 1.0,
    },
}

PROPOSALS = {
    "candidates": [
        {"name": "Acme", "reason": "hires Python backend engineers"},
        {"name": "Globex", "reason": "EU-remote platform team"},
    ]
}


def _fake_llm(content: dict | str):
    """An openai.OpenAI stand-in. A dict is JSON-encoded (happy path); a str is
    returned raw so the REAL complete_json raises LlmResponseError on non-JSON.
    """
    raw = content if isinstance(content, str) else json.dumps(content)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
    )
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )


def _override_llm(client, content):
    client.app.dependency_overrides[get_llm] = lambda: _fake_llm(content)


def _mk_company(session, name: str, *, resolved: bool, enabled: bool = True) -> Company:
    company = Company(
        name=name,
        ats=AtsPlatform.GREENHOUSE if resolved else None,
        ats_identifier="board" if resolved else None,
        resolved_at=datetime.now(UTC) if resolved else None,
        enabled=enabled,
    )
    session.add(company)
    session.flush()
    return company


# ---------------------------------------------------------------------------
# POST /api/onboarding/propose-employers (INTK-05, D-05)
# ---------------------------------------------------------------------------


def test_propose_returns_candidates_and_adds_nothing(client, make_session):
    client.post("/api/criteria", json=CRITERIA)
    _override_llm(client, PROPOSALS)

    resp = client.post("/api/onboarding/propose-employers", json={"count": 30})
    assert resp.status_code == 200
    body = resp.json()
    assert [c["name"] for c in body["candidates"]] == ["Acme", "Globex"]
    assert body["candidates"][0]["reason"]

    # D-05: proposals are proposals until the batch accept gate — nothing added
    assert client.get("/api/companies").json() == []


def test_propose_without_active_criteria_is_409(client):
    _override_llm(client, PROPOSALS)

    resp = client.post("/api/onboarding/propose-employers", json={"count": 10})
    assert resp.status_code == 409
    assert "criteria" in resp.json()["detail"].lower()


def test_propose_llm_failure_is_502(client):
    client.post("/api/criteria", json=CRITERIA)
    _override_llm(client, "definitely not json")

    resp = client.post("/api/onboarding/propose-employers", json={"count": 10})
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail.startswith("employer proposal failed:")
    assert "not json" in detail


def test_propose_dedupes_case_insensitively_and_caps(client):
    client.post("/api/criteria", json=CRITERIA)
    _override_llm(
        client,
        {
            "candidates": [
                {"name": "Acme", "reason": "first"},
                {"name": "acme", "reason": "duplicate"},
                {"name": "   ", "reason": "empty"},
                {"name": "Globex", "reason": "second"},
                {"name": "Initech", "reason": "third"},
            ]
        },
    )

    resp = client.post("/api/onboarding/propose-employers", json={"count": 2})
    assert resp.status_code == 200
    names = [c["name"] for c in resp.json()["candidates"]]
    assert names == ["Acme", "Globex"]


def test_propose_count_bounds_are_422(client):
    client.post("/api/criteria", json=CRITERIA)
    _override_llm(client, PROPOSALS)

    assert client.post("/api/onboarding/propose-employers", json={"count": 0}).status_code == 422
    assert client.post("/api/onboarding/propose-employers", json={"count": 51}).status_code == 422


# ---------------------------------------------------------------------------
# GET /api/companies/coverage (INTK-06, D-06)
# ---------------------------------------------------------------------------


def test_coverage_counts_watchable_and_needs_attention(client, make_session):
    session = make_session()
    try:
        _mk_company(session, "A", resolved=True)
        _mk_company(session, "B", resolved=True)
        _mk_company(session, "C", resolved=True)
        _mk_company(session, "D", resolved=False)
        session.commit()
    finally:
        session.close()

    body = client.get("/api/companies/coverage").json()
    assert body["added"] == 4
    assert body["watchable"] == 3
    assert body["needs_attention"] == 1
    assert body["resolved"] == 3


def test_coverage_excludes_disabled_from_watchable(client, make_session):
    session = make_session()
    try:
        _mk_company(session, "Watched", resolved=True, enabled=True)
        _mk_company(session, "Turned Off", resolved=True, enabled=False)
        _mk_company(session, "Unresolved", resolved=False)
        session.commit()
    finally:
        session.close()

    body = client.get("/api/companies/coverage").json()
    assert body["added"] == 3
    # Coverage counts what will ACTUALLY be watched: a resolved-but-disabled
    # employer is not watchable.
    assert body["watchable"] == 1
    assert body["resolved"] == 2
    assert body["needs_attention"] == 1
