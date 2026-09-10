"""Advisory scoring flags (SCOR-09).

SCOR-09. These are OBSERVATIONS, not filters. Nothing in this module may drop
a listing or alter a score. A flag exists so the user can see why a listing is
unusual and decide for themselves — automating that decision is precisely the
judgement the user reserved.

Six flags, always returned as a dict with all six keys:

- ``stretch_role``      — title implies a higher seniority than seniority_max
- ``step_down``         — title implies a lower seniority than seniority_min
- ``language_requirement`` — description requires a non-English language
- ``location_ambiguity``   — location.ambiguous is True
- ``comp_below_floor``  — floor_comparison is BELOW (NOT_COMPARABLE / NO_DATA do not raise it)
- ``posting_stale``     — posting is old or has no date

No flag causes a drop.  No flag may receive or modify a score.
"""

import re
from datetime import datetime, timezone

import pycountry

from huntloop.criteria.schema import SENIORITY_LADDER, CriteriaPayload
from huntloop.discovery.normalize.compensation import FloorComparison, NormalizedCompensation
from huntloop.discovery.normalize.location import NormalizedLocation

# ---------------------------------------------------------------------------
# Fixed flag names — normative; spelling must match REQUIREMENTS.md exactly.
# ---------------------------------------------------------------------------

FLAG_NAMES: tuple[str, ...] = (
    "stretch_role",
    "step_down",
    "language_requirement",
    "location_ambiguity",
    "comp_below_floor",
    "posting_stale",
)

# ---------------------------------------------------------------------------
# Seniority detection
# ---------------------------------------------------------------------------

# Ordered HIGHEST rung first; the first match wins.  Every right-hand value is
# a member of SENIORITY_LADDER = ["intern","junior","mid","senior","staff",
# "principal","director","vp","c_level"].  "Manager" is deliberately absent:
# it is not a rung on this ladder, and inventing one would fabricate a
# direction the user never expressed.
SENIORITY_MARKERS: tuple[tuple[str, str], ...] = (
    (r"\b(chief|c[- ]level|cto|ceo|cfo|cpo)\b", "c_level"),
    (r"\b(vp|vice president)\b", "vp"),
    (r"\b(head of|director)\b", "director"),
    (r"\b(principal|distinguished|fellow)\b", "principal"),
    (r"\b(staff)\b", "staff"),
    (r"\b(senior|sr\.?|lead)\b", "senior"),
    (r"\b(mid[- ]level|intermediate)\b", "mid"),
    (r"\b(junior|jr\.?|associate|graduate|entry[- ]level)\b", "junior"),
    (r"\b(intern|internship)\b", "intern"),
)


def detect_seniority(title: str) -> str | None:
    """Map a job title to the closest ``SENIORITY_LADDER`` rung.

    Checks patterns case-insensitively, highest rung first, so
    "Senior Staff Engineer" resolves to ``staff``, not ``senior``.
    Returns ``None`` when no recognised marker is present.
    """
    for pattern, rung in SENIORITY_MARKERS:
        if re.search(pattern, title, re.IGNORECASE):
            if rung in SENIORITY_LADDER:
                return rung
    return None


# ---------------------------------------------------------------------------
# Language detection helpers
# ---------------------------------------------------------------------------

# Patterns that indicate an explicit language requirement in a job description.
# These are intentionally conservative — false positives here raise an
# advisory flag, not a filter drop.
LANGUAGE_REQUIREMENT_PATTERNS: tuple[str, ...] = (
    r"(?i)\b(fluent|native|proficient|business[- ]level)\s+(?:in\s+)?([A-Z][a-z]+)",
    r"(?i)\b([A-Z][a-z]+)\s+(?:language\s+)?(?:skills\s+)?(?:is\s+)?required\b",
)


def _detect_required_language(description: str) -> tuple[str, str] | None:
    """Return ``(language_name, matched_phrase)`` or ``None``."""
    for pattern in LANGUAGE_REQUIREMENT_PATTERNS:
        for match in re.finditer(pattern, description):
            # Capture group 1 or 2 may be the language name depending on pattern.
            for group_idx in (2, 1):
                try:
                    word = match.group(group_idx)
                except IndexError:
                    continue
                if word is None:
                    continue
                # Skip "English" — it is the assumed working language.
                if word.lower() == "english":
                    continue
                # Validate against pycountry language list.
                lang = pycountry.languages.get(name=word)
                if lang is not None:
                    phrase = match.group(0)[:120]
                    return word, phrase
    return None


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------


def compute_flags(
    *,
    listing,         # RawListing — avoids circular import at module level
    location: NormalizedLocation,
    comp: NormalizedCompensation,
    floor_comparison: FloorComparison,
    criteria: CriteriaPayload,
    posting_age_decision,  # FilterDecision from filters.py
) -> dict[str, dict]:
    """Compute all six advisory flags for one listing.

    Returns a dict shaped ``{flag_name: {"raised": bool, "detail": str}}``
    with EXACTLY the six keys from ``FLAG_NAMES``.  Always iterates
    ``FLAG_NAMES`` to build the result so all six keys are guaranteed present
    even when a flag is not raised.

    This function takes no ``score`` argument and returns no score: flags are
    OBSERVATIONS.  The signature itself makes this structural.

    Direction is the whole point (REQUIREMENTS.md SCOR-09): stretch_role and
    step_down are opposite meanings, so they are two separate flags and can
    never both be raised at once.
    """
    flags: dict[str, dict] = {name: {"raised": False, "detail": ""} for name in FLAG_NAMES}

    # ---- stretch_role / step_down -------------------------------------------
    level = detect_seniority(listing.title)
    if level is not None:
        idx = SENIORITY_LADDER.index(level)
        max_idx = (
            SENIORITY_LADDER.index(criteria.seniority_max)
            if criteria.seniority_max
            else None
        )
        min_idx = (
            SENIORITY_LADDER.index(criteria.seniority_min)
            if criteria.seniority_min
            else None
        )
        # Direction is the whole point (REQUIREMENTS.md SCOR-09): stretch_role and
        # step_down are two separate flags and are never raised together.
        if max_idx is not None and idx > max_idx:
            flags["stretch_role"] = {
                "raised": True,
                "detail": f"title implies {level}, target ceiling is {criteria.seniority_max}",
            }
        elif min_idx is not None and idx < min_idx:
            flags["step_down"] = {
                "raised": True,
                "detail": f"title implies {level}, target floor is {criteria.seniority_min}",
            }

    # ---- language_requirement -----------------------------------------------
    # CriteriaPayload carries no 'languages' field (02-03), so there is no
    # user-declared list to compare against.  English is the assumed working
    # language and any other stated requirement is surfaced as an OBSERVATION
    # for the user to judge — which is exactly what an advisory flag is for.
    # Do not filter on it, and do not invent a criteria field to hold it.
    description = getattr(listing, "description_plain", "") or ""
    lang_result = _detect_required_language(description)
    if lang_result is not None:
        lang_name, phrase = lang_result
        flags["language_requirement"] = {
            "raised": True,
            "detail": f"requires {lang_name}: {phrase!r}",
        }

    # ---- location_ambiguity -------------------------------------------------
    if location.ambiguous:
        flags["location_ambiguity"] = {
            "raised": True,
            "detail": f"could not resolve location from {location.raw!r}",
        }

    # ---- comp_below_floor ---------------------------------------------------
    # NOT_COMPARABLE and NO_DATA deliberately do not raise this — 02-03 refuses
    # currency conversion, so "not comparable" is not evidence of "below".
    if floor_comparison is FloorComparison.BELOW:
        flags["comp_below_floor"] = {
            "raised": True,
            "detail": (
                f"listing minimum {comp.minimum} {comp.currency} {comp.period} "
                f"is below floor {criteria.compensation_floor.amount} "
                f"{criteria.compensation_floor.currency} "
                f"{criteria.compensation_floor.period}"
            ),
        }

    # ---- posting_stale ------------------------------------------------------
    # A stale posting is normally already dropped by the filter; the flag exists
    # for when the age filter is disabled or for advisory context.
    posting_age_detail = getattr(posting_age_decision, "detail", "")
    posting_age_uncertain = getattr(posting_age_decision, "uncertain", False)

    if posting_age_uncertain and "no reliable date" in posting_age_detail:
        flags["posting_stale"] = {
            "raised": True,
            "detail": "posting has no date; freshness unknown",
        }
    else:
        # Check whether the age exceeds the configured limit.
        age_days_limit = criteria.posting_age_days
        posted_at = getattr(listing, "posted_at", None)
        if posted_at is not None and age_days_limit is not None:
            now = datetime.now(timezone.utc)
            if posted_at.tzinfo is None:
                posted_at = posted_at.replace(tzinfo=timezone.utc)
            age_days = (now - posted_at).days
            if age_days > age_days_limit:
                flags["posting_stale"] = {
                    "raised": True,
                    "detail": f"posted {age_days}d ago, limit is {age_days_limit}d",
                }

    return flags
