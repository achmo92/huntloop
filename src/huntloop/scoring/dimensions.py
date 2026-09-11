"""Four-dimension scoring with one-line reasons (SCOR-06).

`score_dimensions` is the expensive model call that actually reads the posting.
It returns a `ScoringResponse` whose four dimension scores carry the one-line
reasons that make a score arguable rather than oracular.

Neither this module nor `triage.py` may import `openai` directly — all model
access flows through `complete_json` (OPS-06 confinement test).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from huntloop.config import load_config
from huntloop.llm.client import LlmCall, LlmResponseError, complete_json
from huntloop.scoring.config import DIMENSIONS, SCORING_PROMPT

if TYPE_CHECKING:
    pass

MAX_SCORING_DESCRIPTION_CHARS = 12000  # Scoring is the stage that must actually read the posting.
# 12k chars covers every real posting observed in research while bounding a pathological one.


class DimensionScore(BaseModel):
    model_config = ConfigDict(extra="ignore")
    score: int | None = Field(default=None, ge=1, le=5)
    reason: str = ""

    @field_validator("reason", mode="after")
    @classmethod
    def default_empty_reason(cls, v: str, info) -> str:
        """Replace an empty reason with a meaningful placeholder.

        A non-empty reason is required by SCOR-06 on every dimension.  An empty
        string is not one.
        """
        score = info.data.get("score")
        if not v:
            if score is None:
                return "posting gave no evidence for this dimension"
            return "no reason supplied by model"
        return v


class ScoringResponse(BaseModel):
    # extra="ignore" is what makes "the model must not produce the overall score"
    # structural rather than a code review rule — an `overall` key the model
    # volunteers cannot reach the persistence layer.
    model_config = ConfigDict(extra="ignore")

    @model_validator(mode="before")
    @classmethod
    def unwrap_nested_dimensions(cls, data):
        """Accept a ``{"dimensions": {...}}`` wrapper even though the prompt
        now requests the flat shape. Belt and braces: prompt-following varies
        by model, and a silently-unscored listing is the costliest failure
        mode this stage has (found live at the 02-12 checkpoint)."""
        if isinstance(data, dict) and isinstance(data.get("dimensions"), dict):
            merged = dict(data["dimensions"])
            for key, value in data.items():
                if key != "dimensions":
                    merged[key] = value
            return merged
        return data

    role_fit: DimensionScore
    seniority_fit: DimensionScore
    employer_fit: DimensionScore
    trajectory: DimensionScore
    summary: str = ""

    @field_validator("summary", mode="after")
    @classmethod
    def default_empty_summary(cls, v: str) -> str:
        return v or "no summary provided"

    def to_score_dimensions(self) -> dict:
        """Shape persisted to ``jobs.score_dimensions``.

        Iterates ``DIMENSIONS`` rather than hardcoding the four names a second time
        so adding a dimension to config cannot silently leave this method behind.
        """
        result = {}
        for dim in DIMENSIONS:
            dim_score: DimensionScore = getattr(self, dim.name)
            result[dim.name] = {"score": dim_score.score, "reason": dim_score.reason}
        return result

    def to_dimension_scores(self) -> dict:
        """Shape ``compute_overall_score`` consumes: ``{\"role_fit\": 4, ...}`` with None for unassessable."""
        return {dim.name: getattr(self, dim.name).score for dim in DIMENSIONS}


def build_scoring_user_message(listing, criteria) -> str:
    """Build the per-listing user message for the full scoring call.

    Full posting content goes in the user message, NEVER in the system prompt.
    The system prompt is a hash input for SCOR-10.
    """
    description = (listing.description_plain or "")[:MAX_SCORING_DESCRIPTION_CHARS]
    comp_raw = getattr(listing, "comp_raw", None) or ""
    company = getattr(listing, "company_name", None) or getattr(listing, "company", None) or ""

    # Compensation floor always has a period value (Literal["annual","monthly","hourly"]) —
    # render it directly, never as if it could be None.
    floor = criteria.compensation_floor
    floor_str = f"{floor.amount or 'none'} {floor.currency or ''} {floor.period}".strip()

    return (
        f"TITLE: {listing.title}\n"
        f"COMPANY: {company}\n"
        f"LOCATION: {listing.location_raw or 'unspecified'}\n"
        f"POSTED: {listing.posted_at or 'unknown'}\n"
        f"COMPENSATION: {comp_raw or 'not stated'}\n"
        f"DESCRIPTION: {description}\n"
        f"\n"
        f"CANDIDATE PROFILE: {criteria.profile_summary}\n"
        f"TARGET SENIORITY: {criteria.seniority_min or 'any'} to {criteria.seniority_max or 'any'}\n"
        f"ELIGIBLE COUNTRIES: {', '.join(criteria.locations.eligible_countries) or 'unrestricted'}\n"
        f"ELIGIBLE REGIONS: {', '.join(criteria.locations.eligible_regions) or 'unrestricted'}\n"
        f"COMPENSATION FLOOR: {floor_str}"
    )


def score_dimensions(
    client,
    listing,
    criteria,
    *,
    model: str | None = None,
) -> tuple[ScoringResponse, LlmCall]:
    """Issue the full four-dimension scoring call.

    Returns ``(ScoringResponse, LlmCall)`` so the caller can accumulate usage and
    record the exact model name on ``ScoredListing.model``.

    Unlike triage, a scoring failure is NOT papered over with a fabricated score.
    ``LlmResponseError`` propagates to the caller (pipeline.py), which decides what
    to do with an unscoreable listing.  One bad listing must not abort a run
    (DISC-03), so the pipeline catches it — but the error must be visible, not hidden.
    """
    model = model or load_config().scoring_model

    call: LlmCall = complete_json(
        client,
        model=model,
        system=SCORING_PROMPT,
        user=build_scoring_user_message(listing, criteria),
        schema=ScoringResponse,
        temperature=0.0,
        max_tokens=900,
    )

    response = ScoringResponse.model_validate(call.content)
    return response, call
