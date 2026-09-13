"""UI-04 settings API: one sectioned surface, four independently-saveable PUTs.

D-15: API access (key + base URL), model per stage, schedule (time + timezone),
and spend cap. Env vars remain boot-time defaults the UI overrides via the
`Setting` table — `load_effective_config` is the single resolution layer.

The API key NEVER lands in the settings table and never appears in a response:
it is written through the encrypted `CredentialStore` (Phase 1's OPS-05
posture), and the settings table keeps only secret metadata.
"""

from __future__ import annotations

import os
from typing import Literal
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

import httpx
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy.orm import Session

from huntloop.api.deps import get_credentials_session, get_session
from huntloop.config import (
    Config,
    ConfigError,
    load_effective_config,
    parse_run_at,
    resolve_llm_api_key,
)
from huntloop.credentials.store import CredentialStore
from huntloop.db.repository import SettingsRepository

router = APIRouter(prefix="/api/settings", tags=["settings"])

_API_KEY = "openai_api_key"

# GAP-5: the typed fallback when the configured provider's /models cannot be
# read. Common OpenAI-compatible ids — the model picker's escape hatch covers
# anything a custom endpoint exposes under other names.
FALLBACK_MODELS: tuple[str, ...] = (
    "gpt-4o-mini",
    "gpt-4o",
    "gpt-4.1-mini",
    "gpt-4.1",
    "o4-mini",
)


# ---------------------------------------------------------------------------
# Response / request models (colocated — later plans colocate theirs likewise)
# ---------------------------------------------------------------------------


class ApiAccessOut(BaseModel):
    base_url: str
    has_api_key: bool


class ModelsOut(BaseModel):
    triage: str
    scoring: str
    extraction: str


class AvailableModelsOut(BaseModel):
    """The model picker's options (GAP-5): provider list or typed fallback."""

    models: list[str]
    source: Literal["provider", "fallback"]


class ScheduleOut(BaseModel):
    run_at: str
    timezone: str


class SpendCapOut(BaseModel):
    cap_usd: float | None = None


class SettingsOut(BaseModel):
    api_access: ApiAccessOut
    models: ModelsOut
    schedule: ScheduleOut
    spend_cap: SpendCapOut


class ApiAccessIn(BaseModel):
    base_url: str | None = None
    api_key: str | None = None


class ModelsIn(BaseModel):
    triage: str | None = None
    scoring: str | None = None
    extraction: str | None = None


class ScheduleIn(BaseModel):
    run_at: str
    timezone: str


class SpendCapIn(BaseModel):
    cap_usd: float | None = None


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _has_api_key(credentials_session: Session) -> bool:
    """What a run would actually use: the encrypted store first, env second.

    Mirrors `resolve_llm_api_key`'s resolution order honestly without raising
    when neither is present (the UI needs a boolean, not a failure).
    """
    if CredentialStore(credentials_session).has(_API_KEY):
        return True
    return bool(os.environ.get("HUNTLOOP_OPENAI_API_KEY"))


def _models_out(cfg: Config) -> ModelsOut:
    return ModelsOut(
        triage=cfg.triage_model,
        scoring=cfg.scoring_model,
        extraction=cfg.extraction_model,
    )


def _schedule_out(cfg: Config) -> ScheduleOut:
    return ScheduleOut(run_at=cfg.run_at, timezone=cfg.timezone)


def _spend_cap_out(cfg: Config) -> SpendCapOut:
    return SpendCapOut(
        cap_usd=float(cfg.run_spend_cap_usd) if cfg.run_spend_cap_usd is not None else None
    )


def _validate_base_url(value: str) -> str:
    url = value.strip()
    if not url:
        raise HTTPException(status_code=422, detail="Base URL must not be empty.")
    if not url.startswith(("http://", "https://")):
        raise HTTPException(
            status_code=422,
            detail=(
                "Base URL must start with http:// or https:// — all model access "
                "routes through one OpenAI-compatible endpoint."
            ),
        )
    return url


def _fetch_provider_models(base_url: str, api_key: str) -> list[str] | None:
    """List the configured endpoint's models, or None when it cannot be read.

    Uses httpx directly (never the openai SDK) so the stored key stays
    server-side; this is a backend proxy, OPS-06-safe. Any failure — refused
    connection, 401, timeout, malformed body — degrades to None so the caller
    can serve a typed fallback rather than an error.
    """
    try:
        with httpx.Client(timeout=5.0) as http_client:
            response = http_client.get(
                f"{base_url.rstrip('/')}/models",
                headers={"Authorization": f"Bearer {api_key}"},
            )
        if response.status_code // 100 != 2:
            return None
        payload = response.json()
    except Exception:  # noqa: BLE001 - any provider failure degrades to fallback
        return None

    data = payload.get("data") if isinstance(payload, dict) else None
    if not isinstance(data, list):
        return None
    ids = {
        item["id"].strip()
        for item in data
        if isinstance(item, dict)
        and isinstance(item.get("id"), str)
        and item["id"].strip()
    }
    return sorted(ids) if ids else None


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=SettingsOut)
def read_settings(
    session: Session = Depends(get_session),
    credentials_session: Session = Depends(get_credentials_session),
) -> SettingsOut:
    """The four sections, overlay-resolved. Never exposes key material."""
    cfg = load_effective_config(session)
    return SettingsOut(
        api_access=ApiAccessOut(
            base_url=cfg.openai_base_url,
            has_api_key=_has_api_key(credentials_session),
        ),
        models=_models_out(cfg),
        schedule=_schedule_out(cfg),
        spend_cap=_spend_cap_out(cfg),
    )


@router.get("/models", response_model=AvailableModelsOut)
def read_available_models(
    session: Session = Depends(get_session),
    credentials_session: Session = Depends(get_credentials_session),
) -> AvailableModelsOut:
    """Which models can this endpoint use? (GAP-5)

    The list is fetched through the backend with the stored key, so the key
    never reaches the browser. Any provider failure returns a typed fallback
    that still includes the saved per-stage models, so the dropdown always has
    options; free-text entry remains the escape hatch for custom endpoints.
    """
    cfg = load_effective_config(session)
    try:
        api_key: str | None = resolve_llm_api_key(credentials_session)
    except ConfigError:
        api_key = None

    provider_models: list[str] | None = None
    if api_key:
        try:
            provider_models = _fetch_provider_models(cfg.openai_base_url, api_key)
        except Exception:  # noqa: BLE001 - never fail the section on a provider call
            provider_models = None

    if provider_models:
        return AvailableModelsOut(
            models=sorted(set(provider_models)), source="provider"
        )

    saved = {
        model.strip()
        for model in (cfg.triage_model, cfg.scoring_model, cfg.extraction_model)
        if model and model.strip()
    }
    return AvailableModelsOut(
        models=sorted(set(FALLBACK_MODELS) | saved), source="fallback"
    )


@router.put("/api-access", response_model=ApiAccessOut)
def update_api_access(
    body: ApiAccessIn,
    session: Session = Depends(get_session),
    credentials_session: Session = Depends(get_credentials_session),
) -> ApiAccessOut:
    """base_url -> Setting row; api_key -> encrypted store + secret metadata.

    The settings table records only that the key exists and is sensitive; the
    ciphertext lives exclusively in credentials.db (OPS-05).
    """
    repo = SettingsRepository(session)
    if body.base_url is not None:
        repo.set_value("openai_base_url", _validate_base_url(body.base_url))
    if body.api_key is not None:
        key = body.api_key.strip()
        if not key:
            raise HTTPException(status_code=422, detail="API key must not be empty.")
        CredentialStore(credentials_session).set(_API_KEY, key)
        # Commit the encrypted write before touching the main store: the two
        # stores are separate files in production, but a single-file test
        # harness would otherwise hold a write lock across the second store.
        credentials_session.commit()
        repo.set_secret_metadata(_API_KEY)
    session.commit()
    cfg = load_effective_config(session)
    return ApiAccessOut(
        base_url=cfg.openai_base_url,
        has_api_key=_has_api_key(credentials_session),
    )


@router.put("/models", response_model=ModelsOut)
def update_models(
    body: ModelsIn,
    session: Session = Depends(get_session),
) -> ModelsOut:
    """Per-stage model choice. Only the keys present are written — sections are
    independently saveable."""
    repo = SettingsRepository(session)
    provided = {
        "triage_model": body.triage,
        "scoring_model": body.scoring,
        "extraction_model": body.extraction,
    }
    for setting_key, value in provided.items():
        if value is None:
            continue
        model = value.strip()
        if not model:
            raise HTTPException(
                status_code=422,
                detail=f"{setting_key.replace('_model', '')} model must not be empty.",
            )
        repo.set_value(setting_key, model)
    session.commit()
    return _models_out(load_effective_config(session))


@router.put("/schedule", response_model=ScheduleOut)
def update_schedule(
    body: ScheduleIn,
    session: Session = Depends(get_session),
) -> ScheduleOut:
    """Validate BOTH fields before writing either — a rejected timezone must not
    leave a half-applied schedule."""
    try:
        parse_run_at(body.run_at)
    except ConfigError as exc:
        raise HTTPException(status_code=422, detail=f"Invalid run time: {exc}") from exc
    try:
        ZoneInfo(body.timezone)
    except (ZoneInfoNotFoundError, ValueError) as exc:
        raise HTTPException(status_code=422, detail=f"Invalid timezone: {exc}") from exc

    repo = SettingsRepository(session)
    repo.set_value("run_at", body.run_at.strip())
    repo.set_value("timezone", body.timezone.strip())
    session.commit()
    return _schedule_out(load_effective_config(session))


@router.put("/spend-cap", response_model=SpendCapOut)
def update_spend_cap(
    body: SpendCapIn,
    session: Session = Depends(get_session),
) -> SpendCapOut:
    """A positive cap, or null to clear the override (falls back to env default)."""
    if body.cap_usd is not None and body.cap_usd <= 0:
        raise HTTPException(
            status_code=422,
            detail="Spend cap must be greater than 0, or null to clear it.",
        )
    repo = SettingsRepository(session)
    repo.set_value("run_spend_cap_usd", body.cap_usd)
    session.commit()
    return _spend_cap_out(load_effective_config(session))
