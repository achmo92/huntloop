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
    except Exception:  # noqa: BLE001 - a diagnostic must report, not raise
        # T-04-09: never expose the internal endpoint URL or raw exception text.
        return DiagOut(
            status="fail",
            detail="the configured model endpoint did not respond",
            remedy=_LLM_REMEDY,
        )
    return DiagOut(
        status="pass",
        detail="the configured model endpoint responded",
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
    except Exception:  # noqa: BLE001 - a diagnostic must report, not raise
        # T-04-09: generic detail, never a raw DSN/exception string.
        return DiagOut(
            status="fail",
            detail="database probe failed",
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
    except UnsupportedPlatform:
        return DiagOut(
            status="fail",
            detail="no live board probe is available for this employer",
            remedy=_EMPLOYER_REMEDY,
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
    except Exception:  # noqa: BLE001 - a diagnostic must report, not raise
        # T-04-09: the company name is the user's own data; the exception is not.
        return DiagOut(
            status="fail",
            detail="the employer's board could not be read",
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
        detail="the employer's board did not return listings",
        remedy=_EMPLOYER_REMEDY,
    )
