"""Criteria API contract tests: current, versioned save, history, describe-first
extraction (INTK-01..04, INTK-07) over the shared tests/api/conftest.py harness.
"""

import json
from types import SimpleNamespace

from huntloop.api.deps import get_llm


def _valid_payload(**overrides) -> dict:
    payload = {
        "profile_summary": "Backend engineer at product companies, Python-heavy",
        "seniority_min": "mid",
        "seniority_max": "staff",
        "posting_age_days": 30,
        "locations": {
            "eligible_countries": ["US", "DE"],
            "eligible_regions": ["EU"],
            "preferred_cities": ["Berlin"],
        },
        "compensation_floor": {"amount": "120000", "currency": "USD", "period": "annual"},
        "exclusions": {"title_keywords": ["consultant"], "employers": ["Revify"]},
        "work_authorization": {"countries_authorized": ["US"], "requires_sponsorship": False},
        "dimension_weights": {
            "role_fit": 4.0,
            "seniority_fit": 3.0,
            "employer_fit": 2.0,
            "trajectory": 1.0,
        },
    }
    payload.update(overrides)
    return payload


def _fake_llm(content: dict | str):
    """An openai.OpenAI stand-in: chat.completions.create returns a canned
    response. A dict is JSON-encoded (the happy path); a str is returned raw
    so the REAL complete_json raises LlmResponseError on non-JSON content.
    """
    raw = content if isinstance(content, str) else json.dumps(content)
    response = SimpleNamespace(
        choices=[SimpleNamespace(message=SimpleNamespace(content=raw))],
        usage=SimpleNamespace(prompt_tokens=12, completion_tokens=8),
    )
    return SimpleNamespace(
        chat=SimpleNamespace(completions=SimpleNamespace(create=lambda **kwargs: response))
    )


EXTRACTION_RESULT = {
    "profile_summary": "Senior backend engineer in fintech, Berlin or remote EU, no crypto",
    "seniority_min": "senior",
    "seniority_max": "staff",
    "posting_age_days": 21,
    "locations": {
        "eligible_countries": ["DE", "NL"],
        "eligible_regions": ["EU"],
        "preferred_cities": ["Berlin", "Amsterdam"],
    },
    "compensation_floor": {"amount": 110000, "currency": "EUR", "period": "annual"},
    "exclusions": {"title_keywords": ["crypto"], "employers": []},
    "work_authorization": {"countries_authorized": ["DE"], "requires_sponsorship": False},
    "dimension_weights": {
        "role_fit": 4.0,
        "seniority_fit": 2.0,
        "employer_fit": 1.0,
        "trajectory": 3.0,
    },
}


# ---------------------------------------------------------------------------
# GET current
# ---------------------------------------------------------------------------


def test_get_current_empty(client):
    resp = client.get("/api/criteria")
    assert resp.status_code == 200
    assert resp.json() == {"current": None, "total_versions": 0}


# ---------------------------------------------------------------------------
# Versioned save (INTK-07: version-on-every-change, never overwrite)
# ---------------------------------------------------------------------------


def test_post_creates_version_1(client):
    resp = client.post("/api/criteria", json=_valid_payload())
    assert resp.status_code == 201
    assert resp.json() == {"version": 1}

    current = client.get("/api/criteria").json()
    assert current["total_versions"] == 1
    assert current["current"]["version"] == 1
    assert current["current"]["source"] == "manual_edit"
    assert current["current"]["created_at"] is not None
    assert current["current"]["payload"]["profile_summary"].startswith("Backend engineer")


def test_second_save_retains_both_versions(client):
    first = client.post("/api/criteria", json=_valid_payload())
    assert first.status_code == 201 and first.json() == {"version": 1}

    second_payload = _valid_payload(
        profile_summary="Platform engineer, infra-flavoured, EU only",
        dimension_weights={
            "role_fit": 1.0,
            "seniority_fit": 2.0,
            "employer_fit": 3.0,
            "trajectory": 4.0,
        },
    )
    second = client.post("/api/criteria", json=second_payload)
    assert second.status_code == 201
    assert second.json() == {"version": 2}

    # Both versions retained WITH their payloads; only version 2 is active.
    versions = client.get("/api/criteria/versions").json()
    assert [v["version"] for v in versions] == [1, 2]
    assert versions[0]["payload"]["profile_summary"].startswith("Backend engineer")
    assert versions[1]["payload"]["profile_summary"].startswith("Platform engineer")

    current = client.get("/api/criteria").json()
    assert current["total_versions"] == 2
    assert current["current"]["version"] == 2


def test_version_list_carries_full_payloads(client):
    client.post("/api/criteria", json=_valid_payload())
    client.post("/api/criteria", json=_valid_payload(profile_summary="Second distinct summary"))

    versions = client.get("/api/criteria/versions").json()
    assert len(versions) == 2
    for v in versions:
        assert set(v) == {"version", "created_at", "source", "payload"}
        # the full typed payload round-trips: nested structures survive
        assert v["payload"]["locations"]["eligible_countries"] == ["US", "DE"]
        assert v["payload"]["compensation_floor"]["currency"] == "USD"
        assert v["payload"]["dimension_weights"]["role_fit"] == 4.0


def test_get_version_by_number_and_404(client):
    client.post("/api/criteria", json=_valid_payload())

    resp = client.get("/api/criteria/versions/1")
    assert resp.status_code == 200
    body = resp.json()
    assert body["version"] == 1
    assert body["payload"]["profile_summary"].startswith("Backend engineer")

    missing = client.get("/api/criteria/versions/99")
    assert missing.status_code == 404
    assert missing.json()["detail"] == "criteria version 99 not found"


# ---------------------------------------------------------------------------
# Typed payload surface: invalid input is a 422, not a 500 (INTK-02/04)
# ---------------------------------------------------------------------------


def test_invalid_payload_returns_422_and_writes_nothing(client):
    bad_currency = client.post(
        "/api/criteria",
        json=_valid_payload(
            compensation_floor={"amount": "90000", "currency": "ZZZ", "period": "annual"}
        ),
    )
    assert bad_currency.status_code == 422
    assert "ZZZ" in bad_currency.text

    bad_seniority = client.post("/api/criteria", json=_valid_payload(seniority_min="wizard"))
    assert bad_seniority.status_code == 422
    assert "wizard" in bad_seniority.text

    # no row was written by either failed attempt
    current = client.get("/api/criteria").json()
    assert current == {"current": None, "total_versions": 0}


# ---------------------------------------------------------------------------
# Describe-first extraction (INTK-01: draft from a freeform paragraph,
# nothing persisted; INTK-03: weights from plain-language importance)
# ---------------------------------------------------------------------------


def _override_llm(client, content):
    client.app.dependency_overrides[get_llm] = lambda: _fake_llm(content)


def test_describe_returns_draft_and_persists_nothing(client):
    client.post("/api/criteria", json=_valid_payload())  # prior state: version 1
    _override_llm(client, EXTRACTION_RESULT)

    resp = client.post(
        "/api/criteria/describe",
        json={
            "text": "Senior backend person in fintech, Berlin or EU remote, "
            "role fit matters most, then trajectory, no crypto"
        },
    )
    assert resp.status_code == 200
    suggested = resp.json()["suggested"]
    assert suggested["locations"]["eligible_countries"] == ["DE", "NL"]
    assert suggested["compensation_floor"]["currency"] == "EUR"
    assert suggested["compensation_floor"]["amount"] == 110000.0
    assert suggested["seniority_min"] == "senior"

    # nothing was persisted by the describe call
    current = client.get("/api/criteria").json()
    assert current["total_versions"] == 1
    assert current["current"]["version"] == 1
    assert current["current"]["payload"]["profile_summary"].startswith("Backend engineer")


def test_describe_dimension_weights_reflect_model_ranking(client):
    _override_llm(client, EXTRACTION_RESULT)

    resp = client.post("/api/criteria/describe", json={"text": "role first, then trajectory"})
    assert resp.status_code == 200
    weights = resp.json()["suggested"]["dimension_weights"]
    # INTK-03: the plain-language importance ordering maps straight to weights
    assert weights == {
        "role_fit": 4.0,
        "seniority_fit": 2.0,
        "employer_fit": 1.0,
        "trajectory": 3.0,
    }
    assert max(weights, key=weights.get) == "role_fit"


def test_describe_normalizes_unusable_values_to_null(client):
    _override_llm(
        client,
        {
            **EXTRACTION_RESULT,
            "seniority_min": "archmage",
            "compensation_floor": {"amount": 110000, "currency": "GOLD", "period": "annual"},
        },
    )

    resp = client.post("/api/criteria/describe", json={"text": "anything"})
    assert resp.status_code == 200
    suggested = resp.json()["suggested"]
    # the draft must never be invalid enough to break the form (D-02): unknown
    # seniority/currency become null for the user to fix in the form
    assert suggested["seniority_min"] is None
    assert suggested["compensation_floor"]["currency"] is None


def test_describe_rejects_empty_text(client):
    _override_llm(client, EXTRACTION_RESULT)
    resp = client.post("/api/criteria/describe", json={"text": ""})
    assert resp.status_code == 422


def test_describe_without_api_key_returns_actionable_503(client, monkeypatch):
    monkeypatch.delenv("HUNTLOOP_OPENAI_API_KEY", raising=False)

    resp = client.post("/api/criteria/describe", json={"text": "senior backend in Berlin"})

    assert resp.status_code == 503
    assert resp.json()["detail"] == (
        "LLM API access is not configured. Add an API key in Settings → API access."
    )


def test_describe_llm_failure_returns_502(client):
    # raw non-JSON content -> the real complete_json raises LlmResponseError
    _override_llm(client, "definitely not json")

    resp = client.post("/api/criteria/describe", json={"text": "senior backend in Berlin"})
    assert resp.status_code == 502
    detail = resp.json()["detail"]
    assert detail.startswith("criteria extraction failed:")
    assert "not json" in detail
