"""Deterministic pre-model filter chain (SCOR-01, SCOR-03).

Rules here are the only thing standing between the raw listing volume and
every model call in the pipeline.  Their two invariants are:

1. **Deterministic only.**  Neither this module nor any function it calls may
   import ``huntloop.llm`` or ``openai``.  Anything requiring judgement belongs
   to the model stage in 02-07.

2. **Uncertainty survives.**  An unknown posting date, an unparseable location,
   or a parse failure inside a rule must result in PASS + ``uncertain=True``,
   never DROP.  A wrong drop is invisible to the user; a wrong keep costs one
   triage call.  The load-bearing step is ``check_geography`` step 6 — see its
   docstring.
"""

import enum
from dataclasses import dataclass
from datetime import datetime, timezone

from huntloop.criteria.schema import CriteriaPayload, LocationCriteria
from huntloop.db.models import FilterTier
from huntloop.discovery.normalize.location import (
    NormalizedLocation,
    RemoteScopeGuess,
    normalize_location,
)


# ---------------------------------------------------------------------------
# Core types
# ---------------------------------------------------------------------------


class FilterVerdict(str, enum.Enum):
    PASS = "pass"
    DROP = "drop"
    SKIPPED = "skipped"


@dataclass(frozen=True)
class FilterDecision:
    """One rule's verdict with the concrete values it compared.

    ``uncertain=True`` means the rule lacked enough data to DROP — the listing
    is kept as a candidate and ``location_ambiguity`` / ``posting_stale`` flags
    will be raised by ``flags.py``.
    """

    rule: str  # "posting_age" | "geography" | ...
    verdict: FilterVerdict
    detail: str  # human-readable; MUST name the concrete values compared
    uncertain: bool = False  # passed because the data was insufficient to drop


@dataclass(frozen=True)
class FilterOutcome:
    """The result of running the whole deterministic filter chain.

    ``location`` is exposed here so 02-07's pipeline can pass it directly to
    ``compute_flags`` without recomputing it (the 5-argument ``normalize_location``
    call uses all platform fields and is non-trivial to re-derive).
    """

    passed: bool
    decisions: tuple[FilterDecision, ...]
    tier_reached: FilterTier
    location: NormalizedLocation  # already built by this chain; reuse it downstream

    @property
    def first_drop(self) -> FilterDecision | None:
        for d in self.decisions:
            if d.verdict is FilterVerdict.DROP:
                return d
        return None


# ---------------------------------------------------------------------------
# Individual rule functions
# ---------------------------------------------------------------------------


def check_posting_age(
    posted_at: datetime | None,
    max_age_days: int | None,
    *,
    now: datetime | None = None,
) -> FilterDecision:
    """Posting-age filter (SCOR-01).

    Returns PASS with ``uncertain=True`` for any case where we cannot
    positively confirm the posting is stale: no date, a future date, or no
    limit configured.  Only a confirmed-old date produces DROP.

    Args:
        posted_at: The listing's publication datetime (may be naive; assumed UTC).
        max_age_days: Maximum age the user configured.  ``None`` means filter off.
        now: Injected clock for test determinism.  Defaults to ``datetime.now(UTC)``.
    """
    now = now or datetime.now(timezone.utc)

    if max_age_days is None:
        return FilterDecision(
            rule="posting_age",
            verdict=FilterVerdict.PASS,
            detail="no posting_age_days limit supplied",
        )

    if posted_at is None:
        return FilterDecision(
            rule="posting_age",
            verdict=FilterVerdict.PASS,
            detail="posting has no reliable date; kept as candidate",
            uncertain=True,
        )

    # Normalise to UTC-aware.
    if posted_at.tzinfo is None:
        posted_at = posted_at.replace(tzinfo=timezone.utc)
    else:
        posted_at = posted_at.astimezone(timezone.utc)

    age_days = (now - posted_at).days

    if age_days < 0:
        return FilterDecision(
            rule="posting_age",
            verdict=FilterVerdict.PASS,
            detail=f"posted_at is in the future by {abs(age_days)}d; treating as fresh",
            uncertain=True,
        )

    if age_days > max_age_days:
        return FilterDecision(
            rule="posting_age",
            verdict=FilterVerdict.DROP,
            detail=f"posted {age_days}d ago, limit is {max_age_days}d",
        )

    return FilterDecision(
        rule="posting_age",
        verdict=FilterVerdict.PASS,
        detail=f"posted {age_days}d ago, within {max_age_days}d",
    )


def check_geography(
    location: NormalizedLocation,
    criteria: LocationCriteria,
) -> FilterDecision:
    """Geography eligibility filter (SCOR-03).

    Evaluate in this exact order and return on the first conclusive answer.
    Step 6 is the load-bearing one: an unresolvable location is kept, not
    dropped.  Dropping on unknown geography is the single highest-volume
    silent-failure mode in a discovery pipeline — the user never sees what was
    removed, so the bug is invisible until they find the job elsewhere.

    Note: ``preferred_cities`` from ``LocationCriteria`` is intentionally
    ignored here.  A preferred city is a *preference*, not an eligibility rule,
    and must not cause any listing to be dropped.
    """
    eligible_countries = criteria.eligible_countries
    eligible_regions = criteria.eligible_regions

    # Step 1: Filter off — both lists empty means any geography is eligible.
    if not eligible_countries and not eligible_regions:
        return FilterDecision(
            rule="geography",
            verdict=FilterVerdict.PASS,
            detail="no geographic eligibility configured",
        )

    # Step 2: Worldwide remote — scoped to no geography.
    if location.remote_scope is RemoteScopeGuess.GLOBAL:
        return FilterDecision(
            rule="geography",
            verdict=FilterVerdict.PASS,
            detail="remote worldwide; scoped to no geography",
        )

    # Step 3: Region match — runs BEFORE country check so an eligible region
    # rescues a country not individually listed (the two lists are OR'd).
    if location.region is not None and location.region in eligible_regions:
        return FilterDecision(
            rule="geography",
            verdict=FilterVerdict.PASS,
            detail=f"region {location.region} is in eligible_regions",
        )

    # Step 4: Country decision.
    if location.country_code is not None and eligible_countries:
        if location.country_code in eligible_countries:
            return FilterDecision(
                rule="geography",
                verdict=FilterVerdict.PASS,
                detail=f"located in {location.country_code}, which is in eligible_countries",
            )
        return FilterDecision(
            rule="geography",
            verdict=FilterVerdict.DROP,
            detail=(
                f"located in {location.country_code}, "
                f"eligible countries are {sorted(eligible_countries)} "
                f"and eligible regions are {sorted(eligible_regions)}"
            ),
        )

    # Step 5: Region miss with no country code.
    if location.country_code is None and location.region is not None and eligible_regions:
        return FilterDecision(
            rule="geography",
            verdict=FilterVerdict.DROP,
            detail=(
                f"scoped to region {location.region}, "
                f"eligible regions are {sorted(eligible_regions)}"
            ),
        )

    # Step 6: Nothing positively resolved — keep as candidate.
    return FilterDecision(
        rule="geography",
        verdict=FilterVerdict.PASS,
        detail=f"could not resolve a country or region from {location.raw!r}; kept as candidate",
        uncertain=True,
    )


# ---------------------------------------------------------------------------
# Pipeline entry point
# ---------------------------------------------------------------------------


def apply_deterministic_filters(
    listing,  # RawListing — imported lazily to avoid circular deps at module level
    criteria: CriteriaPayload,
    *,
    now: datetime | None = None,
) -> FilterOutcome:
    """Run the full deterministic filter chain on one listing.

    Builds ``NormalizedLocation`` using the full 5-argument signature so that
    all platform fields (country_code, is_remote, workplace_type,
    secondary_locations) are available to the parser.  The resulting
    ``NormalizedLocation`` is exposed on ``FilterOutcome.location`` so 02-07's
    pipeline can pass it to ``compute_flags`` without recomputing.

    Short-circuits on the first DROP: remaining rules get a SKIPPED decision
    with a detail naming the blocking rule.

    Each rule call is wrapped in a try/except: a parse failure becomes a PASS
    with ``uncertain=True`` so a malformed listing never aborts the pipeline.
    """
    location = normalize_location(
        listing.location_raw,
        platform_country=listing.country_code,
        platform_is_remote=listing.is_remote,
        platform_workplace_type=listing.workplace_type,
        secondary_locations=listing.secondary_locations,
    )

    rules: list[tuple[str, FilterDecision | None]] = []

    # --- Rule 1: posting age ---
    try:
        age_decision = check_posting_age(listing.posted_at, criteria.posting_age_days, now=now)
    except Exception as exc:
        age_decision = FilterDecision(
            rule="posting_age",
            verdict=FilterVerdict.PASS,
            detail=f"parse error in posting_age rule: {type(exc).__name__}: {exc}",
            uncertain=True,
        )
    rules.append(("posting_age", age_decision))

    # --- Rule 2: geography ---
    if age_decision.verdict is FilterVerdict.DROP:
        geo_decision = FilterDecision(
            rule="geography",
            verdict=FilterVerdict.SKIPPED,
            detail=f"not evaluated: dropped by {age_decision.rule}",
        )
    else:
        try:
            geo_decision = check_geography(location, criteria.locations)
        except Exception as exc:
            geo_decision = FilterDecision(
                rule="geography",
                verdict=FilterVerdict.PASS,
                detail=f"parse error in geography rule: {type(exc).__name__}: {exc}",
                uncertain=True,
            )
    rules.append(("geography", geo_decision))

    decisions = tuple(d for _, d in rules)
    first_drop = next((d for d in decisions if d.verdict is FilterVerdict.DROP), None)
    passed = first_drop is None

    return FilterOutcome(
        passed=passed,
        decisions=decisions,
        tier_reached=FilterTier.DETERMINISTIC,
        location=location,
    )
