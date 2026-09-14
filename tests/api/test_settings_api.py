"""UI-04 settings API: GET four sections + four independently-saveable PUTs.

D-15: one sectioned settings surface; the API key goes through the encrypted
credential store (never the settings table, never a response).
"""

from __future__ import annotations

from typing import ClassVar, Self

import pytest

from huntloop.api.routers import settings
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
        "HUNTLOOP_ALLOW_PRIVATE_ENDPOINT",
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
    assert body["api_access"]["api_key_reentry_required"] is False
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
    assert resp.json() == {
        "base_url": "https://llm.example/v1",
        "has_api_key": False,
        "api_key_reentry_required": False,
    }
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


# ---------------------------------------------------------------------------
# GAP-5: GET /api/settings/models — provider list via the stored key, or a
# typed fallback that still reflects the saved models. Never 5xxs; never
# leaks the key (OPS-06-safe).
# ---------------------------------------------------------------------------


class _FakeModelsResponse:
    def __init__(self, status_code: int, payload: dict) -> None:
        self.status_code = status_code
        self._payload = payload

    def json(self) -> dict:
        return self._payload


class _FakeModelsClient:
    """Stands in for `httpx.Client` so the real helper seam is exercised."""

    captured: ClassVar[dict] = {}
    status_code: ClassVar[int] = 200
    payload: ClassVar[dict] = {"data": []}

    def __init__(self, *args, **kwargs) -> None:
        _FakeModelsClient.captured["init_kwargs"] = kwargs

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc) -> bool:
        return False

    def get(self, url: str, headers: dict | None = None) -> _FakeModelsResponse:
        _FakeModelsClient.captured["url"] = url
        _FakeModelsClient.captured["headers"] = headers
        return _FakeModelsResponse(_FakeModelsClient.status_code, _FakeModelsClient.payload)


def _patch_provider(monkeypatch, result):
    # raising=False: the seam is created by the implementation; before GREEN the
    # target test must fail on the endpoint's own assertion, not an AttributeError.
    monkeypatch.setattr(settings, "_fetch_provider_models", lambda *a, **k: result, raising=False)


def _store_key(client, key: str = "sk-test") -> None:
    assert client.put("/api/settings/api-access", json={"api_key": key}).status_code == 200


def test_fetch_provider_models_sends_bearer_to_models_path(monkeypatch):
    _FakeModelsClient.captured = {}
    _FakeModelsClient.status_code = 200
    _FakeModelsClient.payload = {"data": [{"id": "m-b"}, {"id": "m-a"}, {"id": "m-a"}]}
    monkeypatch.setattr(settings.httpx, "Client", _FakeModelsClient)

    assert settings._fetch_provider_models("https://llm.example/v1/", "sk-secret") == [
        "m-a",
        "m-b",
    ]
    assert _FakeModelsClient.captured["url"] == "https://llm.example/v1/models"
    assert _FakeModelsClient.captured["headers"] == {"Authorization": "Bearer sk-secret"}


def test_fetch_provider_models_returns_none_on_error_status(monkeypatch):
    _FakeModelsClient.captured = {}
    _FakeModelsClient.status_code = 401
    _FakeModelsClient.payload = {"error": "unauthorized"}
    monkeypatch.setattr(settings.httpx, "Client", _FakeModelsClient)

    assert settings._fetch_provider_models("https://llm.example/v1", "bad") is None


def test_available_models_returns_sorted_provider_list(client, monkeypatch):
    _store_key(client)
    _patch_provider(monkeypatch, ["m-b", "m-a", "m-a"])

    resp = client.get("/api/settings/models")
    assert resp.status_code == 200
    assert resp.json() == {"models": ["m-a", "m-b"], "source": "provider"}


def test_available_models_provider_failure_is_a_200_fallback(client, monkeypatch):
    _store_key(client)
    _patch_provider(monkeypatch, None)

    resp = client.get("/api/settings/models")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "fallback"
    assert set(settings.FALLBACK_MODELS) <= set(body["models"])


def test_available_models_provider_raising_is_still_a_200_fallback(client, monkeypatch):
    _store_key(client)

    def _boom(*_args, **_kwargs):
        raise RuntimeError("connection refused")

    monkeypatch.setattr(settings, "_fetch_provider_models", _boom, raising=False)

    resp = client.get("/api/settings/models")
    assert resp.status_code == 200
    assert resp.json()["source"] == "fallback"


def test_available_models_without_a_key_goes_straight_to_fallback(client, monkeypatch):
    called = {"count": 0}

    def _should_not_run(*_args, **_kwargs):
        called["count"] += 1
        return ["m-a"]

    monkeypatch.setattr(settings, "_fetch_provider_models", _should_not_run)

    resp = client.get("/api/settings/models")
    assert resp.status_code == 200
    assert resp.json()["source"] == "fallback"
    assert called["count"] == 0


def test_available_models_fallback_includes_the_saved_models(client, make_session, monkeypatch):
    _patch_provider(monkeypatch, None)
    session = make_session()
    try:
        repo = SettingsRepository(session)
        repo.set_value("triage_model", "my-custom-triage-7b")
        repo.set_value("scoring_model", "my-custom-score-70b")
        repo.set_value("extraction_model", "my-custom-extract-3b")
        session.commit()
    finally:
        session.close()

    body = client.get("/api/settings/models").json()
    assert body["source"] == "fallback"
    saved = {"my-custom-triage-7b", "my-custom-score-70b", "my-custom-extract-3b"}
    assert saved <= set(body["models"])
    assert set(settings.FALLBACK_MODELS) <= set(body["models"])


def test_available_models_uses_the_stored_key_and_never_returns_it(client, monkeypatch):
    _store_key(client, "sk-super-secret")
    captured: dict = {}

    def _capture(base_url: str, api_key: str):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return ["m-a"]

    monkeypatch.setattr(settings, "_fetch_provider_models", _capture, raising=False)

    resp = client.get("/api/settings/models")
    assert resp.status_code == 200
    assert captured["api_key"] == "sk-super-secret"
    assert captured["base_url"].startswith("http")
    # The key never reaches the response body.
    assert "sk-super-secret" not in resp.text


# ---------------------------------------------------------------------------
# T-04-04: the stored key is bound to the base URL it was saved for. Changing
# the base URL without re-entering the key must never forward the key to the
# new (possibly attacker-controlled) endpoint.
# ---------------------------------------------------------------------------


def test_models_proxy_does_not_send_key_to_a_changed_base_url(client, monkeypatch):
    _store_key(client, "sk-secret")
    assert (
        client.put(
            "/api/settings/api-access",
            json={"base_url": "https://attacker.example/v1"},
        ).status_code
        == 200
    )

    called = {"count": 0}

    def _should_not_run(*_args, **_kwargs):
        called["count"] += 1
        return ["m-a"]

    monkeypatch.setattr(settings, "_fetch_provider_models", _should_not_run, raising=False)

    resp = client.get("/api/settings/models")
    assert resp.status_code == 200
    assert resp.json()["source"] == "fallback"
    assert called["count"] == 0

    body = client.get("/api/settings").json()
    assert body["api_access"]["api_key_reentry_required"] is True
    assert body["api_access"]["has_api_key"] is True


def test_reentering_key_rebinds_to_the_new_base(client, monkeypatch):
    _store_key(client, "sk-secret")
    assert (
        client.put(
            "/api/settings/api-access",
            json={"base_url": "https://attacker.example/v1"},
        ).status_code
        == 200
    )
    assert (
        client.put("/api/settings/api-access", json={"api_key": "sk-2"}).status_code
        == 200
    )

    captured: dict = {}

    def _capture(base_url: str, api_key: str):
        captured["base_url"] = base_url
        captured["api_key"] = api_key
        return ["m-a"]

    monkeypatch.setattr(settings, "_fetch_provider_models", _capture, raising=False)

    resp = client.get("/api/settings/models")
    assert resp.status_code == 200
    assert resp.json()["source"] == "provider"
    assert captured["base_url"] == "https://attacker.example/v1"
    assert captured["api_key"] == "sk-2"
    assert (
        client.get("/api/settings").json()["api_access"]["api_key_reentry_required"]
        is False
    )


def test_put_api_access_rejects_non_https_and_private_base_url(client):
    # Plain http is rejected on the no-auth private-network posture.
    assert (
        client.put(
            "/api/settings/api-access", json={"base_url": "http://llm.example/v1"}
        ).status_code
        == 422
    )
    # An https URL that resolves to loopback is rejected too.
    assert (
        client.put(
            "/api/settings/api-access", json={"base_url": "https://127.0.0.1/v1"}
        ).status_code
        == 422
    )


def test_allow_private_endpoint_env_permits_loopback_base_url(client, monkeypatch):
    monkeypatch.setenv("HUNTLOOP_ALLOW_PRIVATE_ENDPOINT", "1")
    resp = client.put(
        "/api/settings/api-access", json={"base_url": "https://127.0.0.1/v1"}
    )
    assert resp.status_code == 200
    assert resp.json()["base_url"] == "https://127.0.0.1/v1"
