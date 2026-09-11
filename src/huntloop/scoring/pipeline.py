"""The scoring pipeline (SCOR-05, SCOR-06, DISC-03).

The order in this function is the cost model of the entire product.
Deterministic filters remove volume for free; triage removes more for a
fraction of a scoring call; only what survives both is scored properly.
Reordering these stages would not break a test elsewhere, so
`test_triage_before_score` in tests/scoring/test_graph.py asserts the
call sequence directly.
"""

from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from huntloop.db.models import FilterTier
from huntloop.discovery.ats.base import RawListing
from huntloop.discovery.normalize.compensation import compare_to_floor, normalize_compensation
from huntloop.llm.client import LlmUsage, LlmResponseError
from huntloop.scoring.aggregate import UnscoreableError, compute_overall_score
from huntloop.scoring.config import active_scoring_config
from huntloop.scoring.dimensions import score_dimensions
from huntloop.scoring.filters import apply_deterministic_filters
from huntloop.scoring.flags import compute_flags
from huntloop.scoring.spend_cap import SpendCapReached, SpendTracker  # noqa: F401
from huntloop.scoring.triage import triage_listing


@dataclass(frozen=True)
class ScoredListing:
    listing: RawListing
    scored: bool
    tier_reached: FilterTier
    overall: Decimal | None = None
    dimensions: dict | None = None        # to_score_dimensions() shape, for jobs.score_dimensions
    flags: dict | None = None             # compute_flags() shape, for jobs.score_flags
    summary: str = ""                     # for jobs.score_summary
    criteria_version: int | None = None   # for jobs.scored_criteria_version
    rubric_version: str | None = None     # for jobs.scored_rubric_version
    model: str | None = None              # for jobs.scored_with_model
    drop_reason: str = ""
    error: str = ""
    note: str = ""
    usage: tuple[LlmUsage, ...] = ()


def score_listing(
    client,
    listing,
    criteria,
    *,
    criteria_version: int | None,
    now=None,
    spend_tracker: SpendTracker | None = None,
    triage_model: str | None = None,
    scoring_model: str | None = None,
) -> ScoredListing:
    """Run one listing through the full evaluation pipeline.

    1. Deterministic filters (free, removes noise).
    2. Triage model pass (cheap, fail-open).
    3. Full dimension scoring (expensive, strict).
    4. Aggregate scoring and flag computation.

    This function never mutates the database. It takes no session and imports
    no repository.

    RUN-08: when `spend_tracker` is supplied, the cap is checked immediately
    before EACH of the two model calls, and each call's usage is recorded the
    moment it returns. A trip raises SpendCapReached out of this function on
    purpose — it does NOT return a ScoredListing with a drop_reason, because
    write_batch persists every ScoredListing it is handed (TRAK-06), and the
    locked decision is that a cap-blocked listing is held back this run and
    naturally retried on the next one via the existing dedup path.
    """
    # 1. Deterministic filters
    outcome = apply_deterministic_filters(listing, criteria, now=now)
    if not outcome.passed:
        # Return before constructing any model call
        return ScoredListing(
            listing=listing,
            scored=False,
            tier_reached=outcome.tier_reached,
            drop_reason=outcome.first_drop.detail if outcome.first_drop else "dropped by filters",
        )

    # 2. Triage — cap checked BEFORE the call, usage recorded the moment it returns.
    if spend_tracker is not None:
        spend_tracker.check()
    verdict = triage_listing(client, listing, criteria, model=triage_model)
    usage = (verdict.usage,) if verdict.usage else ()
    if spend_tracker is not None:
        spend_tracker.record(verdict.usage)
    
    if not verdict.keep:
        return ScoredListing(
            listing=listing,
            scored=False,
            tier_reached=FilterTier.TRIAGE,
            drop_reason=f"triage: {verdict.reason}",
            usage=usage,
        )

    # 3. Dimensions — the expensive call. Pitfall C: this second check is not
    #    redundant with the one above; a listing can pass cheap triage and only
    #    then push the run over the cap.
    if spend_tracker is not None:
        spend_tracker.check()
    try:
        response, call = score_dimensions(client, listing, criteria, model=scoring_model)
        usage = usage + (call.usage,)
        if spend_tracker is not None:
            spend_tracker.record(call.usage)
    except LlmResponseError as exc:
        return ScoredListing(
            listing=listing,
            scored=False,
            tier_reached=FilterTier.TRIAGE,  # It passed triage, but failed to score
            error=f"scoring failed: {exc}",
            usage=usage,
        )

    # 4. Flags
    comp = normalize_compensation(listing)
    floor_comparison = compare_to_floor(comp, criteria.compensation_floor)
    
    # Get the posting_age decision from the deterministic outcome
    posting_age_decision = next(
        (d for d in outcome.decisions if d.rule == "posting_age"), 
        None
    )

    flags = compute_flags(
        listing=listing,
        location=outcome.location,
        comp=comp,
        floor_comparison=floor_comparison,
        criteria=criteria,
        posting_age_decision=posting_age_decision,
    )

    # 5. Aggregate
    try:
        overall = compute_overall_score(
            response.to_dimension_scores(), 
            criteria.dimension_weights.model_dump()
        )
        note = ""
    except UnscoreableError as exc:
        overall, note = None, f"overall not computable: {exc}"

    return ScoredListing(
        listing=listing,
        scored=True,
        tier_reached=FilterTier.FULL,
        overall=overall,
        dimensions=response.to_score_dimensions(),
        flags=flags,
        summary=response.summary,
        criteria_version=criteria_version,
        rubric_version=active_scoring_config().version,
        model=call.model,
        note=note,
        usage=usage,
    )
