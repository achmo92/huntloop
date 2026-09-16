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
    """Signed backlog score deltas for a proposed ``dimension_weights`` change.

    This MUST stay an in-memory dry run. ``recompute_backlog_overall_scores()``
    assigns ``score_overall`` on every Job row and flushes; D-09 requires a
    prediction that mutates nothing, so it is deliberately never called here.
    Do not "simplify" this back into that helper — the write is the whole
    difference between a preview and an applied change.
    """
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
