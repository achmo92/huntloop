"""Diagnostics API: three one-click checks, each with a human remedy (UI-05,
D-16).

The checks are deliberately separate endpoints rather than one combined call:
the frontend streams them in parallel and renders each as it finishes, without
any server-push complexity. Every failure path returns a remedy that tells a
non-technical user what to DO ("check Settings → API access"), never just an
internal error string.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.api.deps import get_llm, get_session
from huntloop.config import load_config
from huntloop.credentials.base import get_credentials_engine
from huntloop.db.base import get_engine
from huntloop.db.models import Company
from huntloop.discovery.ats.base import FetchStatus, make_client
from huntloop.discovery.ats.registry import UnsupportedPlatform, get_adapter
from huntloop.llm.client import complete_json

router = APIRouter(prefix="/api/diagnostics", tags=["diagnostics"])

_EMPLOYER_REMEDY = (
    "The employer's board may be rate-limiting or unreachable — retry later or "
    "re-resolve the employer."
)
_DATABASE_REMEDY = (
    "Check that the HuntLoop data volume is mounted and the container was "
    "started with docker compose up."
)
_LLM_REMEDY = (
    "Check Settings → API access: verify the base URL and API key, then re-run "
    "diagnostics."
)


class DiagOut(BaseModel):
    status: str
    detail: str
    remedy: str | None = None


class _LlmPing(BaseModel):
    """Minimal schema for the reachability probe."""

    ok: bool = True


@router.post("/llm", response_model=DiagOut)
def check_llm(client: Any = Depends(get_llm)) -> DiagOut:
    """Probe the single OPS-06-configured model endpoint."""
    cfg = load_config()
    try:
        complete_json(
            client,
            model=cfg.triage_model,
            system='Reply with {"ok": true}',
            user="ping",
            schema=_LlmPing,
        )
    except Exception as exc:  # noqa: BLE001 - a diagnostic must report, not raise
        return DiagOut(
            status="fail",
            detail=(
                f"the model endpoint {cfg.openai_base_url} did not respond "
                f"cleanly: {exc}"
            ),
            remedy=_LLM_REMEDY,
        )
    return DiagOut(
        status="pass",
        detail=f"reached the model endpoint {cfg.openai_base_url}",
        remedy=None,
    )


@router.post("/database", response_model=DiagOut)
def check_database() -> DiagOut:
    """Probe BOTH stores: the main application DB and the credentials DB."""
    try:
        with get_engine().connect() as conn:
            conn.execute(select(1))
        with get_credentials_engine().connect() as conn:
            conn.execute(select(1))
    except Exception as exc:  # noqa: BLE001 - a diagnostic must report, not raise
        return DiagOut(
            status="fail",
            detail=f"database probe failed: {exc}",
            remedy=_DATABASE_REMEDY,
        )
    return DiagOut(
        status="pass",
        detail="main and credentials stores are reachable",
        remedy=None,
    )


@router.post("/employer-fetch", response_model=DiagOut)
def check_employer_fetch(session: Session = Depends(get_session)) -> DiagOut:
    """Probe one live board through the SAME ATS adapter discovery uses."""
    company = (
        session.execute(
            select(Company)
            .where(Company.resolved_at.is_not(None), Company.enabled.is_(True))
            .order_by(Company.name)
            .limit(1)
        )
        .scalars()
        .first()
    )
    if company is None:
        return DiagOut(
            status="fail",
            detail="no resolved employer to probe",
            remedy="Resolve at least one employer in the Employers section first",
        )

    try:
        adapter = get_adapter(company.ats.value if company.ats is not None else "")
    except UnsupportedPlatform as exc:
        return DiagOut(
            status="fail", detail=f"{company.name}: {exc}", remedy=_EMPLOYER_REMEDY
        )

    if not company.ats_identifier:
        return DiagOut(
            status="fail",
            detail=f"{company.name} has no board identifier",
            remedy=_EMPLOYER_REMEDY,
        )

    http_client = make_client()
    try:
        result = adapter.fetch(company.ats_identifier, client=http_client)
    except Exception as exc:  # noqa: BLE001 - a diagnostic must report, not raise
        return DiagOut(
            status="fail",
            detail=f"{company.name}: {exc}",
            remedy=_EMPLOYER_REMEDY,
        )
    finally:
        http_client.close()

    if result.status is FetchStatus.OK:
        return DiagOut(
            status="pass",
            detail=f"fetched {len(result.listings)} listings from {company.name}",
            remedy=None,
        )
    return DiagOut(
        status="fail",
        detail=(
            f"{company.name}: "
            f"{(result.error_kind or result.status).value}"
        ),
        remedy=_EMPLOYER_REMEDY,
    )
