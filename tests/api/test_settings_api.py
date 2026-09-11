"""UI-04 settings API: GET four sections + four independently-saveable PUTs.

D-15: one sectioned settings surface; the API key goes through the encrypted
credential store (never the settings table, never a response).
"""

from __future__ import annotations

import pytest

from huntloop.config import load_config
from huntloop.credentials.store import CredentialStore
from huntloop.db.models import Setting
from huntloop.db.repository import SettingsRepository


@pytest.fixture(autouse=True)
def _clean_settings_env(monkeypatch):
    """Pin the env-default layer so assertions do not depend on a developer's shell."""
    for name in (
        "HUNTLOOP_OPENAI_BASE_URL",
        "HUNTLOOP_TRIAGE_MODEL",
        "HUNTLOOP_SCORING_MODEL",
        "HUNTLOOP_EXTRACTION_MODEL",
        "HUNTLOOP_RUN_AT",
        "HUNTLOOP_TIMEZONE",
        "HUNTLOOP_RUN_SPEND_CAP_USD",
        "HUNTLOOP_OPENAI_API_KEY",
    ):
        monkeypatch.delenv(name, raising=False)


def test_get_settings_fresh_db(client):
    cfg = load_config()  # the env-default layer (a .env may legitimately supply base_url)
    resp = client.get("/api/settings")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body.keys()) == {"api_access", "models", "schedule", "spend_cap"}
    assert body["api_access"]["base_url"] == cfg.openai_base_url
    assert body["api_access"]["has_api_key"] is False
    # The key VALUE is never present anywhere in the response.
    assert "api_key" not in body["api_access"]
    assert body["models"] == {
        "triage": cfg.triage_model,
        "scoring": cfg.scoring_model,
        "extraction": cfg.extraction_model,
    }
    assert body["schedule"] == {"run_at": cfg.run_at, "timezone": cfg.timezone}
    expected_cap = None if cfg.run_spend_cap_usd is None else float(cfg.run_spend_cap_usd)
    assert body["spend_cap"] == {"cap_usd": expected_cap}


def test_put_api_access_base_url_writes_setting_row(client, make_session):
    resp = client.put("/api/settings/api-access", json={"base_url": "https://llm.example/v1"})
    assert resp.status_code == 200
    assert resp.json() == {"base_url": "https://llm.example/v1", "has_api_key": False}
    assert client.get("/api/settings").json()["api_access"]["base_url"] == "https://llm.example/v1"

    session = make_session()
    try:
        assert (
            SettingsRepository(session).get_value("openai_base_url") == "https://llm.example/v1"
        )
    finally:
        session.close()


def test_put_api_access_key_goes_to_encrypted_store(client, make_session):
    resp = client.put("/api/settings/api-access", json={"api_key": "sk-x"})
    assert resp.status_code == 200
    assert resp.json()["has_api_key"] is True

    session = make_session()
    try:
        row = session.get(Setting, "openai_api_key")
        assert row is not None
        assert row.is_secret is True
        assert row.value is None, "key material must never land in the settings table"
        assert CredentialStore(session).get("openai_api_key") == "sk-x"
    finally:
        session.close()

    assert client.get("/api/settings").json()["api_access"]["has_api_key"] is True


def test_put_api_access_invalid_base_url_422(client):
    resp = client.put("/api/settings/api-access", json={"base_url": "not-a-url"})
    assert resp.status_code == 422


def test_put_models_rejects_empty_and_overlay_picks_valid(client):
    assert client.put("/api/settings/models", json={"triage": ""}).status_code == 422
    resp = client.put("/api/settings/models", json={"scoring": "gpt-4.1"})
    assert resp.status_code == 200
    assert resp.json() == {
        "triage": "gpt-4o-mini",
        "scoring": "gpt-4.1",
        "extraction": "gpt-4o-mini",
    }
    assert client.get("/api/settings").json()["models"]["scoring"] == "gpt-4.1"


def test_put_schedule_validates_then_writes(client):
    bad = client.put(
        "/api/settings/schedule", json={"run_at": "25:99", "timezone": "Mars/Olympus"}
    )
    assert bad.status_code == 422

    good = client.put(
        "/api/settings/schedule", json={"run_at": "07:15", "timezone": "Europe/Berlin"}
    )
    assert good.status_code == 200
    assert good.json() == {"run_at": "07:15", "timezone": "Europe/Berlin"}
    assert client.get("/api/settings").json()["schedule"] == {
        "run_at": "07:15",
        "timezone": "Europe/Berlin",
    }


def test_put_spend_cap_set_and_clear(client):
    set_resp = client.put("/api/settings/spend-cap", json={"cap_usd": 2.00})
    assert set_resp.status_code == 200
    assert set_resp.json()["cap_usd"] == 2.0
    assert client.get("/api/settings").json()["spend_cap"]["cap_usd"] == 2.0

    clear_resp = client.put("/api/settings/spend-cap", json={"cap_usd": None})
    assert clear_resp.status_code == 200
    assert clear_resp.json()["cap_usd"] is None
    assert client.get("/api/settings").json()["spend_cap"]["cap_usd"] is None


def test_sections_are_independently_saveable(client, make_session):
    client.put("/api/settings/models", json={"triage": "t1"})

    session = make_session()
    try:
        repo = SettingsRepository(session)
        assert repo.get_value("triage_model") == "t1"
        assert repo.get_value("scoring_model") is None
        assert repo.get_value("extraction_model") is None
        assert repo.get_value("run_at") is None
        assert repo.get_value("timezone") is None
        assert repo.get_value("openai_base_url") is None
        assert repo.get_value("run_spend_cap_usd") is None
    finally:
        session.close()
