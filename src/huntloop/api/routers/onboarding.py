"""Onboarding API: candidate employers proposed from the stated criteria
(INTK-05, D-05).

Proposals are proposals: this router performs NO company writes. It reads the
active criteria, asks the single OPS-06 LLM endpoint for candidate employers,
and returns them for the user's batch review. Adding happens only through the
batch accept gate (``POST /api/companies/batch``); resolution is a further,
separate user-gated step. Onboarding order is criteria first, then employers —
without active criteria this is a 409, not an empty proposal list.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from huntloop.api.deps import get_llm, get_session
from huntloop.config import load_config
from huntloop.criteria.loader import get_active_criteria
from huntloop.criteria.schema import CriteriaPayload
from huntloop.llm.client import LlmResponseError, complete_json

router = APIRouter(prefix="/api/onboarding", tags=["onboarding"])


# ---------------------------------------------------------------------------
# Request / response models (colocated here)
# ---------------------------------------------------------------------------


class CandidateOut(BaseModel):
    name: str
    reason: str = ""


class ProposeEmployersRequest(BaseModel):
    count: int = Field(default=30, ge=1, le=50)


class ProposeEmployersResult(BaseModel):
    candidates: list[CandidateOut]


class _CandidateDraft(BaseModel):
    name: str
    reason: str = ""


class _EmployerProposalDraft(BaseModel):
    """The schema the extraction model must satisfy for propose-employers."""

    candidates: list[_CandidateDraft] = Field(default_factory=list)


_SYSTEM_PROMPT = """\
You propose real employers likely to post roles matching a person's stated
job-search criteria. Return ONE JSON object matching this schema:
{"candidates": [{"name": string, "reason": string}]}.

Rules:
- Propose only real employers (companies), never job boards or recruiters.
- Never propose an employer the person has explicitly excluded.
- Each reason is one short line explaining why the employer fits the profile.
- Respond with only the JSON object; no prose.
"""


def _build_user_prompt(payload: CriteriaPayload, count: int) -> str:
    locations = payload.locations
    exclusions = payload.exclusions
    lines = [
        f"Profile: {payload.profile_summary}",
        (
            f"Seniority range: {payload.seniority_min or 'any'} to "
            f"{payload.seniority_max or 'any'}"
        ),
        f"Eligible countries: {', '.join(locations.eligible_countries) or 'any'}",
        f"Eligible regions: {', '.join(locations.eligible_regions) or 'any'}",
        f"Preferred cities: {', '.join(locations.preferred_cities) or 'any'}",
        f"Excluded employers (never propose): {', '.join(exclusions.employers) or 'none'}",
        f"Propose about {count} employers.",
    ]
    return "\n".join(lines)


def _normalize_candidates(raw_candidates: list[dict], count: int) -> list[CandidateOut]:
    """Dedupe case-insensitively, strip empties, cap at ``count``."""
    seen: set[str] = set()
    out: list[CandidateOut] = []
    for candidate in raw_candidates:
        name = str(candidate.get("name") or "").strip()
        if not name:
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)
        reason = str(candidate.get("reason") or "").strip()
        out.append(CandidateOut(name=name, reason=reason))
        if len(out) >= count:
            break
    return out


@router.post("/propose-employers", response_model=ProposeEmployersResult)
def propose_employers(
    body: ProposeEmployersRequest,
    session: Session = Depends(get_session),
    # Duck-typed openai.OpenAI from get_llm: OPS-06 confines the openai import
    # to huntloop/llm/client.py; tests override get_llm with fakes.
    client: Any = Depends(get_llm),
) -> ProposeEmployersResult:
    """Propose candidate employers from the active criteria (INTK-05).

    409 when no criteria are active (onboarding order: criteria first), 502
    when the model response cannot be parsed/validated. Writes nothing.
    """
    active = get_active_criteria(session)
    if active is None:
        raise HTTPException(
            status_code=409,
            detail="no active criteria: define your criteria before proposing employers",
        )
    _version, payload = active

    cfg = load_config()
    try:
        call = complete_json(
            client,
            model=cfg.extraction_model,
            system=_SYSTEM_PROMPT,
            user=_build_user_prompt(payload, body.count),
            schema=_EmployerProposalDraft,
        )
    except LlmResponseError as exc:
        raise HTTPException(
            status_code=502, detail=f"employer proposal failed: {exc}"
        ) from exc

    candidates = _normalize_candidates(call.content.get("candidates") or [], body.count)
    return ProposeEmployersResult(candidates=candidates)
