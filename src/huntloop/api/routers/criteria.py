"""Criteria API: current version, versioned save, version history, and the
describe-first LLM extraction (INTK-01..04, INTK-07).

The CLI proved the domain; these endpoints are the same functions behind
HTTP. Versioning goes through `huntloop.criteria.loader.save_new_criteria_version`
(it already implements INTK-07's version-on-every-change) — this router never
constructs `Criteria` rows directly.
"""

from __future__ import annotations

import pycountry
from datetime import datetime

import openai
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from huntloop.api.deps import get_llm, get_session
from huntloop.config import load_config
from huntloop.criteria.loader import get_active_criteria, save_new_criteria_version
from huntloop.criteria.schema import SENIORITY_LADDER, CriteriaPayload
from huntloop.db.models import Criteria
from huntloop.llm.client import LlmResponseError, complete_json

router = APIRouter(prefix="/api/criteria", tags=["criteria"])


# ---------------------------------------------------------------------------
# Response models (colocated here — later plans colocate theirs likewise)
# ---------------------------------------------------------------------------
# Decimal hygiene: no Decimal crosses this router's wire — criteria payloads
# are stored via `model_dump(mode="json")` (Decimal already float-serialized),
# so these dicts are JSON-native by construction.


class CriteriaVersionOut(BaseModel):
    version: int
    created_at: datetime
    source: str | None = None
    payload: dict


class CriteriaOut(CriteriaVersionOut):
    """The active-criteria view of the same row."""


class CriteriaCurrentOut(BaseModel):
    current: CriteriaOut | None
    total_versions: int


class SaveResult(BaseModel):
    version: int


class DescribeRequest(BaseModel):
    text: str = Field(min_length=1)


class DescribeResult(BaseModel):
    suggested: dict


# ---------------------------------------------------------------------------
# Describe-first extraction schema (INTK-01) — a DRAFT, not a saveable payload
# ---------------------------------------------------------------------------

_VALID_REGIONS = {"EMEA", "APAC", "LATAM", "NA", "EU"}
_VALID_PERIODS = {"annual", "monthly", "hourly"}


class _DraftLocationCriteria(BaseModel):
    eligible_countries: list[str] | None = None
    eligible_regions: list[str] | None = None
    preferred_cities: list[str] | None = None


class _DraftCompensationFloor(BaseModel):
    amount: float | None = None
    currency: str | None = None
    period: str | None = None


class _DraftExclusions(BaseModel):
    title_keywords: list[str] | None = None
    employers: list[str] | None = None


class _DraftWorkAuthorization(BaseModel):
    countries_authorized: list[str] | None = None
    requires_sponsorship: bool | None = None


class _DraftDimensionWeights(BaseModel):
    role_fit: float | None = None
    seniority_fit: float | None = None
    employer_fit: float | None = None
    trajectory: float | None = None


class CriteriaExtraction(BaseModel):
    """The schema the extraction model must satisfy for /api/criteria/describe.

    Deliberately looser than CriteriaPayload: this is a draft for the
    correction form (D-01/D-02), not a saveable payload. Every field is
    optional and unknown extra keys are ignored; unusable values are
    normalized to null (see `_normalize_draft`) so the draft can never be
    invalid enough to break the form — the form is where the user fixes them.
    """

    profile_summary: str | None = None
    seniority_min: str | None = None
    seniority_max: str | None = None
    posting_age_days: int | None = None
    locations: _DraftLocationCriteria | None = None
    compensation_floor: _DraftCompensationFloor | None = None
    exclusions: _DraftExclusions | None = None
    work_authorization: _DraftWorkAuthorization | None = None
    dimension_weights: _DraftDimensionWeights | None = None


_EXTRACTION_SYSTEM_PROMPT = f"""\
You extract structured job-search criteria from a freeform description of \
what a person is looking for. Return ONE JSON object matching the schema \
below. Every field is optional: include a field only when the description \
states it; never invent facts.

Schema (the fields of a CriteriaPayload):
- profile_summary: string, one sentence restating what the person wants.
- seniority_min, seniority_max: one of {SENIORITY_LADDER} \
(seniority_min must be ranked at or below seniority_max).
- posting_age_days: integer >= 1, how old a job posting may be.
- locations: object with
  - eligible_countries: list of ISO 3166-1 alpha-2 country codes (e.g. ["US", "DE"])
  - eligible_regions: list drawn from {sorted(_VALID_REGIONS)}
  - preferred_cities: list of freeform city names
- compensation_floor: object with
  - amount: number (the minimum acceptable pay)
  - currency: ISO 4217 alpha-3 currency code (e.g. "USD", "EUR")
  - period: one of {sorted(_VALID_PERIODS)}
- exclusions: object with
  - title_keywords: list of strings that disqualify a posting by its title
  - employers: list of employer names to exclude
- work_authorization: object with
  - countries_authorized: list of ISO 3166-1 alpha-2 country codes
  - requires_sponsorship: boolean
- dimension_weights: object with role_fit, seniority_fit, employer_fit, \
trajectory — each a number >= 0.

The four scoring dimensions are: role_fit (does the day-to-day role match \
what they want to do), seniority_fit (is the level right), employer_fit (is \
the employer acceptable to them), trajectory (does the role move their \
career forward).

When the description states an importance ordering in plain language (for \
example "role fit matters most, then trajectory"), map that ordering \
directly to dimension_weights: the most important dimension gets the \
largest weight. Use numbers like 4, 3, 2, 1 assigned in the stated order \
(any non-negative numbers that preserve the ordering work). When no \
ordering is stated, give all four dimensions equal weights.
"""


def _is_valid_currency(code: str) -> bool:
    return pycountry.currencies.get(alpha_3=code.upper()) is not None


def _valid_alpha2(codes: list[str] | None) -> list[str] | None:
    if codes is None:
        return None
    return [
        c.upper()
        for c in codes
        if isinstance(c, str) and pycountry.countries.get(alpha_2=c.upper()) is not None
    ]


def _valid_regions(regions: list[str] | None) -> list[str] | None:
    if regions is None:
        return None
    return [r.upper() for r in regions if isinstance(r, str) and r.upper() in _VALID_REGIONS]


def _normalize_draft(draft: dict) -> dict:
    """Make the model's draft safe for the correction form (D-02).

    Unknown seniorities/currencies/periods become null, invalid country/region
    codes are dropped, nonsense numbers (age < 1, negative amounts) become
    null. The draft must never be invalid enough to break the form — the form
    is where the user fixes things.
    """
    out = dict(draft)
    for key in ("seniority_min", "seniority_max"):
        value = out.get(key)
        if value is not None and value not in SENIORITY_LADDER:
            out[key] = None
    age = out.get("posting_age_days")
    if age is not None and age < 1:
        out["posting_age_days"] = None

    comp = out.get("compensation_floor")
    if comp is not None:
        comp = dict(comp)
        currency = comp.get("currency")
        if currency is not None and not (
            isinstance(currency, str) and currency.isalpha() and _is_valid_currency(currency)
        ):
            comp["currency"] = None
        period = comp.get("period")
        if period is not None and period not in _VALID_PERIODS:
            comp["period"] = None
        amount = comp.get("amount")
        if amount is not None and amount < 0:
            comp["amount"] = None
        out["compensation_floor"] = comp

    locations = out.get("locations")
    if locations is not None:
        locations = dict(locations)
        locations["eligible_countries"] = _valid_alpha2(locations.get("eligible_countries"))
        locations["eligible_regions"] = _valid_regions(locations.get("eligible_regions"))
        out["locations"] = locations

    auth = out.get("work_authorization")
    if auth is not None:
        auth = dict(auth)
        auth["countries_authorized"] = _valid_alpha2(auth.get("countries_authorized"))
        out["work_authorization"] = auth

    return out


def _row_to_out(row: Criteria) -> CriteriaOut:
    return CriteriaOut(
        version=row.version,
        created_at=row.created_at,
        source=row.source.value if row.source is not None else None,
        payload=row.payload,
    )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("", response_model=CriteriaCurrentOut)
def read_current(session: Session = Depends(get_session)) -> CriteriaCurrentOut:
    """The active criteria plus the total version count.

    A null current with zero versions is a valid empty state, not an error —
    the intake flow starts from exactly this.
    """
    total_versions: int = session.execute(
        select(func.count()).select_from(Criteria)
    ).scalar_one()
    active = get_active_criteria(session)
    if active is None:
        return CriteriaCurrentOut(current=None, total_versions=total_versions)
    version, _payload = active  # payload validated by the loader's CriteriaPayload
    row = session.execute(select(Criteria).where(Criteria.version == version)).scalar_one()
    return CriteriaCurrentOut(current=_row_to_out(row), total_versions=total_versions)


@router.get("/versions", response_model=list[CriteriaVersionOut])
def list_versions(session: Session = Depends(get_session)) -> list[CriteriaVersionOut]:
    """Every version with its full payload, oldest first — the UI diffs
    client-side between any two (D-04)."""
    rows = session.execute(select(Criteria).order_by(Criteria.version.asc())).scalars().all()
    return [_row_to_out(row) for row in rows]


@router.get("/versions/{version}", response_model=CriteriaVersionOut)
def read_version(version: int, session: Session = Depends(get_session)) -> CriteriaVersionOut:
    row = session.execute(
        select(Criteria).where(Criteria.version == version)
    ).scalar_one_or_none()
    if row is None:
        raise HTTPException(status_code=404, detail=f"criteria version {version} not found")
    return _row_to_out(row)


@router.post("", status_code=201, response_model=SaveResult)
def save_criteria(
    payload: CriteriaPayload, session: Session = Depends(get_session)
) -> SaveResult:
    """Versioned save. CriteriaPayload is the request body, so Pydantic gives
    the 422s (pycountry validators included) before anything is written; the
    row itself is created by the proven loader — every form save is a NEW
    version (INTK-07), never an overwrite. Source defaults to MANUAL_EDIT,
    which is exactly what a form save is.
    """
    version = save_new_criteria_version(session, payload)
    session.commit()
    return SaveResult(version=version)


@router.post("/describe", response_model=DescribeResult)
def describe_criteria(
    body: DescribeRequest,
    client: openai.OpenAI = Depends(get_llm),
) -> DescribeResult:
    """Describe-first extraction (D-01): freeform paragraph in, a
    CriteriaPayload-shaped draft out — persisted NOTHING. The draft lands in
    the editable form for review (D-02); saving goes through POST /api/criteria.
    """
    cfg = load_config()
    try:
        call = complete_json(
            client,
            model=cfg.extraction_model,
            system=_EXTRACTION_SYSTEM_PROMPT,
            user=body.text,
            schema=CriteriaExtraction,
        )
    except LlmResponseError as exc:
        raise HTTPException(
            status_code=502, detail=f"criteria extraction failed: {exc}"
        ) from exc
    return DescribeResult(suggested=_normalize_draft(call.content))
