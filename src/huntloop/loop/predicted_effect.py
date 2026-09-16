"""LOOP-06 predicted effect: what a proposed criteria change would do to the backlog.

A user cannot consent to a change whose blast radius is invisible, so every
permitted edit field must produce a predicted-effect payload before it can reach
the review surface. The load-bearing fact this module is built around is that
the deterministic pre-model filter chain only enforces **two** of the nine
permitted fields: ``posting_age_days`` and ``locations``. A single generic
"dry-run the filters" answer would therefore be a lie for the other seven, so
each field is dispatched to the tier that can actually tell the truth about it.

Four tiers, each returning a payload whose ``kind`` names it:

- ``filter_dry_run``   — Tier A. Before/after verdict deltas from the real
  filter chain, run twice per listing: once at the current value, once at the
  proposed value. ``locations`` is marked ``approximate`` because the platform
  hints the parser originally saw are not persisted on ``Job``.
- ``flag_recompute``   — Tier B. Advisory-flag deltas for the three
  seniority/compensation fields, recomputed from persisted columns with the same
  primitives the scorer used. An explicit ``unknown`` bucket exists so a value
  the data cannot judge is never silently counted as either direction.
- ``literal_count``    — Tier C. Nothing in the pipeline consumes
  ``exclusions`` or ``work_authorization`` today, so this tier reports an honest
  mention count and says so in ``note`` rather than inventing a filter count.
- ``score_recompute``  — Tier D. Signed backlog score deltas for
  ``dimension_weights``, computed entirely in memory.

Structural guarantees:

- This module never writes. It opens no transaction and calls no mutating
  session method; the only database access is read-only selection.
- This module never makes a model call. Everything here is deterministic
  arithmetic over already-stored rows (the same discipline
  ``huntloop.scoring.aggregate`` already enforces for SCOR-11) — see
  ``tests/loop/test_boundaries.py`` for the enforced import boundary.
- :data:`FIELD_TIERS` is asserted total over the enumerated edit surface at
  import time, so adding a tenth permitted field without a tier fails loudly
  instead of shipping a proposal with no predicted effect.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from sqlalchemy import select

from huntloop.criteria.schema import (
    SENIORITY_LADDER,
    CompensationFloor,
    LocationCriteria,
)
from huntloop.db.models import Company, Job, JobStatus
from huntloop.discovery.normalize.compensation import (
    FloorComparison,
    NormalizedCompensation,
    compare_to_floor,
)
from huntloop.discovery.normalize.location import (
    NormalizedLocation,
    normalize_location,
)
from huntloop.loop.types import PERMITTED_EDIT_FIELDS, UnsupportedEditField
from huntloop.scoring.aggregate import UnscoreableError, compute_overall_score
from huntloop.scoring.filters import FilterVerdict, check_geography, check_posting_age
from huntloop.scoring.flags import detect_seniority

__all__ = [
    "DELTA_EPSILON",
    "FIELD_TIERS",
    "LIVE_STATUSES",
    "SAMPLE_CAP",
    "job_location",
    "load_live_backlog",
    "predict_effect",
    "predict_filter_change",
    "predict_flag_change",
    "predict_literal_count",
    "predict_score_change",
]

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

#: How many concrete listings a payload carries as evidence. The counts are the
#: headline; the sample exists so the review card can name real rows.
SAMPLE_CAP = 5

#: A score delta below this is rounding noise, not a change worth reporting.
DELTA_EPSILON = Decimal("0.01")

#: Rejected and withdrawn listings are history, not backlog. Counting them would
#: inflate every predicted effect: the user is deciding what to do about the
#: listings still in play.
LIVE_STATUSES = (
    JobStatus.NEW,
    JobStatus.SHORTLISTED,
    JobStatus.APPLIED,
    JobStatus.INTERVIEWING,
    JobStatus.OFFER,
)

#: Tier B recomputes exactly one advisory flag per field — the flag that field
#: feeds. The mapping is data so the dispatcher stays declarative.
FIELD_TO_FLAG: dict[str, str] = {
    "seniority_max": "stretch_role",
    "seniority_min": "step_down",
    "compensation_floor": "comp_below_floor",
}


# ---------------------------------------------------------------------------
# Backlog access
# ---------------------------------------------------------------------------


def load_live_backlog(session) -> list[Job]:
    """Every listing still in play, in one read-only query."""
    return list(
        session.execute(select(Job).where(Job.status.in_(LIVE_STATUSES))).scalars().all()
    )


def job_location(job: Job) -> NormalizedLocation:
    """Reconstruct a listing's location from persisted columns only.

    Only ``location_normalized`` (or the raw string) and ``is_remote`` survive on
    ``Job``. The platform-specific hints the parser originally received are not
    persisted, so they are deliberately not passed: feeding anything in for them
    would be fabrication. Every payload derived from this function carries
    ``approximate: True`` for that reason.
    """
    return normalize_location(
        job.location_normalized or job.location_raw,
        platform_is_remote=job.is_remote,
    )


# ---------------------------------------------------------------------------
# Tier A — deterministic filter dry-run
# ---------------------------------------------------------------------------


def _filter_decision(job: Job, field: str, value: Any, now: datetime | None):
    """Evaluate one live listing against one candidate value of ``field``."""
    if field == "posting_age_days":
        return check_posting_age(job.posted_at, value, now=now)
    return check_geography(job_location(job), LocationCriteria.model_validate(value))


def predict_filter_change(
    session,
    *,
    field: str,
    current_value: Any,
    proposed_value: Any,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Before/after verdict deltas for the two fields discovery actually filters on.

    ``would_exclude``/``would_include`` are *verdict deltas*, not the total DROP
    set: a listing already excluded under the current value is not "newly
    excluded" by the proposal, and counting it would overstate the blast radius.

    The listing is never run through the pipeline's own filter entry point —
    that one takes a raw listing with platform hints this module does not have.
    """
    live = load_live_backlog(session)

    would_exclude = 0
    would_include = 0
    excluded_sample: list[dict[str, Any]] = []
    included_sample: list[dict[str, Any]] = []

    for job in live:
        before = _filter_decision(job, field, current_value, now)
        after = _filter_decision(job, field, proposed_value, now)
        entry = {"job_id": str(job.id), "title": job.title, "reason": after.detail}

        if before.verdict is FilterVerdict.PASS and after.verdict is FilterVerdict.DROP:
            would_exclude += 1
            excluded_sample.append(entry)
        elif before.verdict is FilterVerdict.DROP and after.verdict is FilterVerdict.PASS:
            would_include += 1
            included_sample.append(entry)

    # The sample prefers what the change takes away; if the change only gives
    # listings back, it samples those instead.
    sample = (excluded_sample or included_sample)[:SAMPLE_CAP]

    return {
        "kind": "filter_dry_run",
        "field": field,
        "would_exclude": would_exclude,
        "would_include": would_include,
        "backlog_size": len(live),
        "approximate": field == "locations",
        "sample": sample,
    }


# ---------------------------------------------------------------------------
# Tier B — advisory-flag recompute
# ---------------------------------------------------------------------------


def _seniority_flag_raised(detected: str, bound: str | None, *, ceiling: bool) -> bool:
    """Whether the scorer's seniority flag would be raised for this title.

    Mirrors the direction rule the scorer applies: a title above the ceiling is
    a stretch, a title below the floor is a step down. A missing bound means the
    user expressed no limit on that side, so nothing can be flagged.
    """
    if bound is None or bound not in SENIORITY_LADDER:
        return False
    detected_index = SENIORITY_LADDER.index(detected)
    bound_index = SENIORITY_LADDER.index(bound)
    return detected_index > bound_index if ceiling else detected_index < bound_index


def _comp_below_floor(job: Job, floor_value: Any) -> bool | None:
    """Whether this listing sits below ``floor_value``, or ``None`` if unknown.

    ``None`` is the honest answer for the two non-comparable outcomes: this
    codebase refuses currency conversion, so "not comparable" is never evidence
    of "below".
    """
    comp = NormalizedCompensation(
        minimum=job.comp_min,
        maximum=job.comp_max,
        currency=job.comp_currency,
        period=job.comp_period.value if job.comp_period else None,
        raw=job.comp_raw,
        source="structured",
    )
    comparison = compare_to_floor(comp, CompensationFloor.model_validate(floor_value))
    if comparison is FloorComparison.BELOW:
        return True
    if comparison is FloorComparison.ABOVE:
        return False
    return None


def predict_flag_change(
    session,
    *,
    field: str,
    current_value: Any,
    proposed_value: Any,
) -> dict[str, Any]:
    """Before/after advisory-flag deltas for the three seniority/compensation fields.

    The scorer's own flag routine cannot be reused here — it needs a raw listing
    and a normalised compensation object that only exist mid-run. What *can* be
    reused, and is, are the same primitives it calls, applied to the columns
    that did survive on ``Job``, so this recomputation and the scorer's own
    judgement cannot drift apart.

    ``unknown`` is deliberately separate from both directions: a title the
    ladder does not recognise, or compensation the floor cannot be compared to,
    is not evidence either way and must never be counted as one.
    """
    live = load_live_backlog(session)
    ceiling = field == "seniority_max"

    would_flag = 0
    would_unflag = 0
    unknown = 0

    for job in live:
        if field == "compensation_floor":
            before = _comp_below_floor(job, current_value)
            after = _comp_below_floor(job, proposed_value)
        else:
            detected = detect_seniority(job.title)
            if detected is None:
                unknown += 1
                continue
            before = _seniority_flag_raised(detected, current_value, ceiling=ceiling)
            after = _seniority_flag_raised(detected, proposed_value, ceiling=ceiling)

        if before is None or after is None:
            unknown += 1
        elif after and not before:
            would_flag += 1
        elif before and not after:
            would_unflag += 1

    return {
        "kind": "flag_recompute",
        "field": field,
        "flag": FIELD_TO_FLAG[field],
        "would_flag": would_flag,
        "would_unflag": would_unflag,
        "unknown": unknown,
        "backlog_size": len(live),
    }


# ---------------------------------------------------------------------------
# Tier C — honest literal mention count
# ---------------------------------------------------------------------------


def _added_term(current_value: Any, proposed_value: Any) -> str | None:
    """The single item ``proposed_value`` adds to ``current_value``, if any."""
    current = set(current_value or [])
    added = sorted({item for item in (proposed_value or []) if item not in current})
    return added[0] if added else None


def _company_names(session) -> dict[Any, str]:
    """Id -> name for every employer, so a job's company can be matched by name."""
    return dict(session.execute(select(Company.id, Company.name)).all())


def predict_literal_count(
    session,
    *,
    field: str,
    current_value: Any,
    proposed_value: Any,
) -> dict[str, Any]:
    """How many live listings literally mention what the proposal would exclude.

    None of these three fields is consumed by the discovery pipeline today, so a
    filter-style count would be fiction — the change would not actually remove
    anything. Rather than invent a number, this tier reports the honest mention
    count and says in ``note`` that the field is informational only. The note's
    wording is part of the UI copy contract; keep the sentence intact.
    """
    live = load_live_backlog(session)
    term: str | None = None
    matches = 0

    if field == "exclusions.title_keywords":
        term = _added_term(current_value, proposed_value)
        if term is not None:
            needle = term.casefold()
            matches = sum(1 for job in live if needle in job.title.casefold())
    elif field == "exclusions.employers":
        term = _added_term(current_value, proposed_value)
        if term is not None:
            needle = term.casefold()
            names = _company_names(session)
            matches = sum(
                1 for job in live if names.get(job.company_id, "").casefold() == needle
            )
    else:
        matches = sum(1 for job in live if (job.work_auth_required or "").strip())

    return {
        "kind": "literal_count",
        "field": field,
        "term": term,
        "matches": matches,
        "enforced": False,
        "backlog_size": len(live),
        "note": f"{field} isn't enforced by discovery yet, so this is informational only.",
    }


# ---------------------------------------------------------------------------
# Tier D — in-memory score recompute
# ---------------------------------------------------------------------------


def _stored_dimension_scores(raw: Any) -> dict[str, Any]:
    """Extract the per-dimension scores ``compute_overall_score`` consumes.

    ``jobs.score_dimensions`` is stored as ``{dim: {"score": n, "reason": s}}``;
    a flat ``{dim: n}`` shape is also tolerated. Mirrors the extraction the
    backlog rescorer already performs, without importing that module's write
    path.
    """
    if not isinstance(raw, Mapping):
        return {}
    scores: dict[str, Any] = {}
    for name, value in raw.items():
        scores[name] = value.get("score") if isinstance(value, Mapping) else value
    return scores


def _as_weight_mapping(weights: Any) -> Any:
    """Accept either a weights model or the plain mapping the JSON column holds."""
    dump = getattr(weights, "model_dump", None)
    if callable(dump):
        return dump(mode="python")
    return weights


def predict_score_change(
    session,
    *,
    current_value: Any,
    proposed_value: Any,
) -> dict[str, Any]:
    """Signed backlog score deltas for a proposed ``dimension_weights`` change."""
    # This MUST stay an in-memory dry run. recompute_backlog_overall_scores()
    # assigns score_overall on every Job row and flushes; D-09 requires a
    # prediction that mutates nothing, so it is deliberately never called here.
    # Do not "simplify" this back into that helper — the write is the whole
    # difference between a preview and an applied change.
    live = load_live_backlog(session)
    before_weights = _as_weight_mapping(current_value)
    after_weights = _as_weight_mapping(proposed_value)

    deltas: list[Decimal] = []
    skipped = 0

    for job in live:
        scores = _stored_dimension_scores(job.score_dimensions)
        try:
            before = compute_overall_score(scores, before_weights)
            after = compute_overall_score(scores, after_weights)
        except (UnscoreableError, TypeError, KeyError, ArithmeticError):
            # An unscored or all-null listing carries no opinion about the
            # proposal; one bad row must never abort the whole prediction.
            skipped += 1
            continue

        delta = after - before
        if abs(delta) >= DELTA_EPSILON:
            deltas.append(delta)

    affected = len(deltas)
    if affected:
        mean = (sum(deltas) / Decimal(affected)).quantize(
            Decimal("0.01"), rounding=ROUND_HALF_UP
        )
        largest = max(deltas, key=abs)
    else:
        mean = Decimal("0.00")
        largest = Decimal("0.00")

    return {
        "kind": "score_recompute",
        "field": "dimension_weights",
        "affected": affected,
        # Floats, not Decimals: this payload lands in a JSON column.
        "mean_delta": float(mean),
        "max_delta": float(largest),
        "backlog_size": len(live),
        "skipped": skipped,
    }


# ---------------------------------------------------------------------------
# The dispatcher — total over the enumerated edit surface
# ---------------------------------------------------------------------------

#: The tier map as data, so the dispatcher's totality is checkable rather than
#: asserted in prose. The module-scope check below fails at import time the
#: moment a permitted field is added without a tier — which is what makes "no
#: proposal reaches the user without a predicted effect" structural.
FIELD_TIERS: dict[str, str] = {
    "posting_age_days": "filter_dry_run",
    "locations": "filter_dry_run",
    "seniority_min": "flag_recompute",
    "seniority_max": "flag_recompute",
    "compensation_floor": "flag_recompute",
    "exclusions.title_keywords": "literal_count",
    "exclusions.employers": "literal_count",
    "work_authorization": "literal_count",
    "dimension_weights": "score_recompute",
}

assert set(FIELD_TIERS) == set(PERMITTED_EDIT_FIELDS), (
    "LOOP-06: every permitted edit field must have a predicted-effect tier"
)


def predict_effect(
    session,
    proposed_changes: Mapping[str, Any],
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Route one proposed change to the tier that can tell the truth about it.

    ``proposed_changes`` is the dict shape stored on ``CriteriaProposal``: a
    ``field``, a ``current_value`` and a ``proposed_value``. An unenumerated
    field raises rather than returning an empty payload, so a proposal can never
    reach the review surface with an invisible blast radius.

    This writes nothing: no transaction is opened and no mutating session method
    is called on any path.
    """
    field = proposed_changes["field"]
    tier = FIELD_TIERS.get(field)
    if tier is None:
        raise UnsupportedEditField(
            f"{field!r} has no predicted-effect tier. "
            f"Permitted: {', '.join(PERMITTED_EDIT_FIELDS)}"
        )

    current_value = proposed_changes.get("current_value")
    proposed_value = proposed_changes.get("proposed_value")

    if tier == "filter_dry_run":
        return predict_filter_change(
            session,
            field=field,
            current_value=current_value,
            proposed_value=proposed_value,
            now=now,
        )
    if tier == "flag_recompute":
        return predict_flag_change(
            session,
            field=field,
            current_value=current_value,
            proposed_value=proposed_value,
        )
    if tier == "literal_count":
        return predict_literal_count(
            session,
            field=field,
            current_value=current_value,
            proposed_value=proposed_value,
        )
    return predict_score_change(
        session,
        current_value=current_value,
        proposed_value=proposed_value,
    )
