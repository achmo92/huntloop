"""LOOP-03/04/05: turn stored pipeline history into gated edit candidates.

This is the analytical core of the adaptive loop and it makes **zero model
calls** (D-06). Everything it needs is already stored: ``StatusEvent`` history,
``Job.score_dimensions`` and ``Job.score_flags``. It therefore has to be cheap
enough to run at the tail of every discovery run.

Three deterministic tracks run in parallel over the fast-rejected listings:

``Track A``
    Attribute each fast rejection to its lowest-scoring dimension (D-03) and, if
    one dimension is the modal suspect, propose raising its weight by one
    relative unit. Increase-only in v1 — a weight *decrease* is weaker-justified.

``Track B``
    Map a repeatedly-raised advisory flag straight onto the criteria field it
    implies (``stretch_role`` -> ``seniority_max``, ``step_down`` ->
    ``seniority_min``, ``comp_below_floor`` -> ``compensation_floor``,
    ``posting_stale`` -> ``posting_age_days``) and tighten it by the smallest
    step that addresses the evidence. ``location_ambiguity`` and
    ``language_requirement`` are deliberately never auto-mapped.

``Track C``
    Find a title token or employer name that repeats across fast rejections and
    never appears among progressed listings, and propose an exclusion.

Detection is *ungated*: it returns every candidate it finds. :func:`gate` is the
single place the per-field observation threshold is consulted (LOOP-04), and
:func:`build_evidence` names the exact listings behind a candidate (LOOP-05).

LOOP-03 is enforced structurally: every candidate is built through
:func:`huntloop.loop.types.build_proposed_changes`, which raises
``UnsupportedEditField`` for anything outside the enumerated surface, so this
module cannot invent a field name even by mistake. Nothing here may reach the
scoring rubric or the model client (LOOP-08).
"""

from __future__ import annotations

import re
from collections import Counter, defaultdict
from collections.abc import Sequence
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from huntloop.criteria.loader import get_active_criteria
from huntloop.criteria.schema import SENIORITY_LADDER, CriteriaPayload, DimensionWeights
from huntloop.db.models import Company, Job
from huntloop.loop.signals import JobSignal, collect_signals
from huntloop.loop.types import (
    MIN_OBSERVATIONS,
    EditCandidate,
    EditDirection,
    Signal,
    build_proposed_changes,
)

__all__ = [
    "EVIDENCE_LISTING_CAP",
    "attribute_dimension",
    "build_evidence",
    "detect_candidates",
    "gate",
    "raised_flags",
]

# LOOP-05: evidence names the specific listings that produced a candidate, but a
# proposal card only needs enough to be believed.
EVIDENCE_LISTING_CAP = 10

# Track A: the modal suspect dimension must explain at least half of the
# attributable fast rejections before it is worth reconstructing scores over.
TRACK_A_MODAL_SHARE = 0.5
# One relative unit; compute_overall_score renormalises, so the absolute scale
# of the weights is not meaningful on its own.
TRACK_A_WEIGHT_STEP = 1.0

# Track B: never tighten the accepted posting age below this, however stale the
# rejections look.
POSTING_AGE_FLOOR_DAYS = 7
POSTING_AGE_SHRINK = 2 / 3

# Track C: a title token must be at least this long and must not be one of the
# words every job posting uses, or every listing would look like a repeat.
TITLE_TOKEN_MIN_LEN = 3
TITLE_STOPWORDS = frozenset(
    {
        "and", "the", "for", "with", "our", "you", "your", "new",
        "remote", "hybrid", "onsite", "full", "part", "time", "contract",
        "engineer", "engineering", "developer", "software", "manager",
    }
)

# D-03 attribution order. Also the deterministic tie-break when two dimensions
# share the numerically lowest score.
DIMENSION_ORDER: tuple[str, ...] = (
    "role_fit",
    "seniority_fit",
    "employer_fit",
    "trajectory",
)

# RESEARCH open question: a bare location_ambiguity or language_requirement flag
# does not imply a concrete country/region delta or exclusion value, so neither
# is auto-mapped. They can still surface through the explicit-feedback extractor.
UNMAPPED_FLAGS: tuple[str, ...] = ("location_ambiguity", "language_requirement")

# The canonical flag order (``huntloop.scoring.flags.FLAG_NAMES``) is restated
# rather than imported: this package may not import the scoring package, and
# ``raised_flags`` only needs a stable iteration order for reproducibility.
FLAG_ORDER: tuple[str, ...] = (
    "stretch_role",
    "step_down",
    "language_requirement",
    "location_ambiguity",
    "comp_below_floor",
    "posting_stale",
)

FLAG_TO_FIELD: dict[str, str] = {
    "stretch_role": "seniority_max",
    "step_down": "seniority_min",
    "comp_below_floor": "compensation_floor",
    "posting_stale": "posting_age_days",
}

# Self-documenting invariant: an unmapped flag can never gain a field mapping by
# accident, because the two sets are disjoint by construction.
assert not set(UNMAPPED_FLAGS) & set(FLAG_TO_FIELD), "unmapped flags must not map to a field"

_DIMENSION_INDEX = {name: position for position, name in enumerate(DIMENSION_ORDER)}


@dataclass(frozen=True)
class _Record:
    """One classified listing: its signal, its Job row and its employer name."""

    signal: JobSignal
    job: Job
    company_name: str

    @property
    def raised(self) -> tuple[str, ...]:
        return raised_flags(self.job)

    @property
    def dimension(self) -> str | None:
        return attribute_dimension(self.job)


# ---------------------------------------------------------------------------
# Attribution helpers (D-03)
# ---------------------------------------------------------------------------


def attribute_dimension(job: Job) -> str | None:
    """Return the dimension a rejection is most likely attributable to.

    D-03: the numerically lowest ``score_dimensions`` entry is the prime
    suspect. Ties break by :data:`DIMENSION_ORDER` so the output is
    deterministic. Returns ``None`` when the job is unscored, which keeps
    unscored listings out of Track A entirely.
    """
    dimensions = job.score_dimensions
    if not dimensions:
        return None

    numeric = [
        (name, float(value))
        for name, value in dimensions.items()
        if isinstance(value, (int, float)) and not isinstance(value, bool)
    ]
    if not numeric:
        return None

    best_name, _ = min(
        numeric,
        key=lambda item: (item[1], _DIMENSION_INDEX.get(item[0], len(DIMENSION_ORDER))),
    )
    return best_name


def _flag_is_raised(entry: Any) -> bool:
    """Whether one ``score_flags`` entry says the flag is raised.

    Tolerates both the current ``{"raised": bool, "detail": str}`` shape and a
    legacy bare boolean.
    """
    if isinstance(entry, bool):
        return entry
    if isinstance(entry, dict):
        return bool(entry.get("raised"))
    return False


def raised_flags(job: Job) -> tuple[str, ...]:
    """The raised advisory flags on ``job``, in canonical flag order."""
    flags = job.score_flags
    if not flags:
        return ()
    return tuple(name for name in FLAG_ORDER if _flag_is_raised(flags.get(name)))


# ---------------------------------------------------------------------------
# Candidate / evidence shaping
# ---------------------------------------------------------------------------


def _observation(
    signal: JobSignal,
    job: Job,
    company_name: str,
    attributed_to: str | None,
) -> dict[str, Any]:
    """One listing's evidence entry — JSON-safe with no custom encoder."""
    return {
        "job_id": str(job.id),
        "title": job.title,
        "company": company_name,
        "status": job.status.value,
        "dwell_days": signal.dwell_days,
        "transitions": signal.transitions,
        "attributed_to": attributed_to,
    }


def _observations(records: Sequence[_Record]) -> list[dict[str, Any]]:
    return [
        _observation(record.signal, record.job, record.company_name, record.dimension)
        for record in records
    ]


def _candidate_from_changes(
    changes: dict[str, Any],
    *,
    signal: Signal,
    observations: Sequence[dict[str, Any]],
    feedback_quotes: Sequence[dict[str, str]] | None = None,
) -> EditCandidate:
    """Wrap a validated ``proposed_changes`` payload into an ``EditCandidate``.

    Building the payload through ``build_proposed_changes`` before this point is
    what makes LOOP-03 a runtime guarantee rather than a convention: an
    unenumerated field raises before a candidate can exist.
    """
    return EditCandidate(
        field=changes["field"],
        direction=EditDirection(changes["direction"]),
        current_value=changes["current_value"],
        proposed_value=changes["proposed_value"],
        signal=signal,
        observations=list(observations),
        feedback_quotes=list(feedback_quotes) if feedback_quotes else [],
    )


# ---------------------------------------------------------------------------
# Track A — dimension attribution
# ---------------------------------------------------------------------------


def _track_a(fast: Sequence[_Record], criteria: CriteriaPayload) -> EditCandidate | None:
    attributable = [(record, record.dimension) for record in fast]
    attributable = [(record, dimension) for record, dimension in attributable if dimension]

    if not attributable:
        return None

    counts = Counter(dimension for _, dimension in attributable)
    modal_dimension = min(
        counts.items(),
        key=lambda item: (-item[1], _DIMENSION_INDEX.get(item[0], len(DIMENSION_ORDER))),
    )[0]
    share = counts[modal_dimension] / len(attributable)
    if share < TRACK_A_MODAL_SHARE:
        return None

    current_value = criteria.dimension_weights.model_dump()
    proposed_value = {
        **current_value,
        modal_dimension: current_value[modal_dimension] + TRACK_A_WEIGHT_STEP,
    }
    try:
        # RESEARCH Pitfall 3: a weights delta is never written without passing
        # the same model validation a manual criteria save would.
        DimensionWeights.model_validate(proposed_value)
    except ValueError:
        return None

    observations = [
        _observation(record.signal, record.job, record.company_name, dimension)
        for record, dimension in attributable
        if dimension == modal_dimension
    ]
    changes = build_proposed_changes(
        "dimension_weights", EditDirection.INCREASE, current_value, proposed_value
    )
    return _candidate_from_changes(
        changes, signal=Signal.FAST_REJECT, observations=observations
    )


# ---------------------------------------------------------------------------
# Track B — flag-driven field tightening
# ---------------------------------------------------------------------------


def _median(values: Sequence[Decimal]) -> Decimal:
    """Median of a non-empty value list, quantized to two places."""
    ordered = sorted(values)
    middle = len(ordered) // 2
    if len(ordered) % 2 == 1:
        result = ordered[middle]
    else:
        result = (ordered[middle - 1] + ordered[middle]) / 2
    return result.quantize(Decimal("0.01"))


def _track_b(fast: Sequence[_Record], criteria: CriteriaPayload) -> list[EditCandidate]:
    candidates: list[EditCandidate] = []

    # --- seniority_max <- stretch_role -------------------------------------
    flagged = [record for record in fast if "stretch_role" in record.raised]
    if flagged and criteria.seniority_max is not None:
        position = SENIORITY_LADDER.index(criteria.seniority_max)
        if position > 0:
            proposed_value = SENIORITY_LADDER[position - 1]
            min_position = (
                SENIORITY_LADDER.index(criteria.seniority_min)
                if criteria.seniority_min is not None
                else None
            )
            if min_position is None or SENIORITY_LADDER.index(proposed_value) >= min_position:
                changes = build_proposed_changes(
                    "seniority_max",
                    EditDirection.TIGHTEN,
                    criteria.seniority_max,
                    proposed_value,
                )
                candidates.append(
                    _candidate_from_changes(
                        changes,
                        signal=Signal.FAST_REJECT,
                        observations=_observations(flagged),
                    )
                )

    # --- seniority_min <- step_down ----------------------------------------
    flagged = [record for record in fast if "step_down" in record.raised]
    if flagged and criteria.seniority_min is not None:
        position = SENIORITY_LADDER.index(criteria.seniority_min)
        if position < len(SENIORITY_LADDER) - 1:
            proposed_value = SENIORITY_LADDER[position + 1]
            max_position = (
                SENIORITY_LADDER.index(criteria.seniority_max)
                if criteria.seniority_max is not None
                else None
            )
            if max_position is None or SENIORITY_LADDER.index(proposed_value) <= max_position:
                changes = build_proposed_changes(
                    "seniority_min",
                    EditDirection.TIGHTEN,
                    criteria.seniority_min,
                    proposed_value,
                )
                candidates.append(
                    _candidate_from_changes(
                        changes,
                        signal=Signal.FAST_REJECT,
                        observations=_observations(flagged),
                    )
                )

    # --- compensation_floor <- comp_below_floor -----------------------------
    flagged = [record for record in fast if "comp_below_floor" in record.raised]
    floor = criteria.compensation_floor
    if flagged and floor.amount is not None and floor.currency is not None:
        comparable: list[Decimal] = []
        for record in flagged:
            job = record.job
            if job.comp_min is None:
                continue
            period = job.comp_period.value if job.comp_period is not None else None
            # Never compare across currencies or periods (SCOR-04): a flagged set
            # that mixes them carries no usable floor signal, so it is dropped
            # entirely rather than quietly narrowed.
            if job.comp_currency != floor.currency or period != floor.period:
                comparable = []
                break
            comparable.append(job.comp_min)
        if len(comparable) >= 2:
            proposed_amount = _median(comparable)
            if proposed_amount > floor.amount:
                proposed_value = {
                    "amount": str(proposed_amount),
                    "currency": floor.currency,
                    "period": floor.period,
                }
                changes = build_proposed_changes(
                    "compensation_floor",
                    EditDirection.TIGHTEN,
                    floor,
                    proposed_value,
                )
                candidates.append(
                    _candidate_from_changes(
                        changes,
                        signal=Signal.FAST_REJECT,
                        observations=_observations(flagged),
                    )
                )

    # --- posting_age_days <- posting_stale ---------------------------------
    flagged = [record for record in fast if "posting_stale" in record.raised]
    if flagged:
        current_value = criteria.posting_age_days
        proposed_value = max(
            POSTING_AGE_FLOOR_DAYS, int(current_value * POSTING_AGE_SHRINK)
        )
        if proposed_value < current_value:
            changes = build_proposed_changes(
                "posting_age_days",
                EditDirection.TIGHTEN,
                current_value,
                proposed_value,
            )
            candidates.append(
                _candidate_from_changes(
                    changes,
                    signal=Signal.FAST_REJECT,
                    observations=_observations(flagged),
                )
            )

    return candidates


# ---------------------------------------------------------------------------
# Track C — literal-repeat exclusions
# ---------------------------------------------------------------------------


def _title_tokens(title: str) -> set[str]:
    """Casefolded title tokens worth considering as an exclusion keyword."""
    return {
        token
        for token in re.split(r"[^a-z0-9]+", title.casefold())
        if len(token) >= TITLE_TOKEN_MIN_LEN and token not in TITLE_STOPWORDS
    }


def _track_c(
    fast: Sequence[_Record],
    progressed: Sequence[_Record],
    criteria: CriteriaPayload,
) -> list[EditCandidate]:
    candidates: list[EditCandidate] = []

    # --- exclusions.title_keywords -----------------------------------------
    existing_keywords = {
        keyword.casefold() for keyword in criteria.exclusions.title_keywords
    }
    token_titles: dict[str, set[str]] = defaultdict(set)
    for record in fast:
        for token in _title_tokens(record.job.title):
            token_titles[token].add(record.job.title)

    progressed_tokens: set[str] = set()
    for record in progressed:
        progressed_tokens |= _title_tokens(record.job.title)

    keyword_minimum = MIN_OBSERVATIONS["exclusions.title_keywords"]
    qualifying_tokens = [
        (token, len(titles))
        for token, titles in token_titles.items()
        if len(titles) >= keyword_minimum
        and token not in progressed_tokens
        and token not in existing_keywords
    ]
    if qualifying_tokens:
        token, _ = min(qualifying_tokens, key=lambda item: (-item[1], item[0]))
        observations = [
            _observation(record.signal, record.job, record.company_name, record.dimension)
            for record in fast
            if token in _title_tokens(record.job.title)
        ]
        proposed_value = [*criteria.exclusions.title_keywords, token]
        changes = build_proposed_changes(
            "exclusions.title_keywords",
            EditDirection.TIGHTEN,
            criteria.exclusions.title_keywords,
            proposed_value,
        )
        candidates.append(
            _candidate_from_changes(
                changes, signal=Signal.FAST_REJECT, observations=observations
            )
        )

    # --- exclusions.employers ----------------------------------------------
    existing_employers = {
        employer.casefold() for employer in criteria.exclusions.employers
    }
    progressed_companies = {record.company_name.casefold() for record in progressed}
    company_counts = Counter(record.company_name for record in fast)
    employer_minimum = MIN_OBSERVATIONS["exclusions.employers"]
    qualifying_employers = [
        (name, count)
        for name, count in company_counts.items()
        if count >= employer_minimum
        and name.casefold() not in progressed_companies
        and name.casefold() not in existing_employers
    ]
    if qualifying_employers:
        name, _ = min(qualifying_employers, key=lambda item: (-item[1], item[0]))
        observations = [
            _observation(record.signal, record.job, record.company_name, record.dimension)
            for record in fast
            if record.company_name == name
        ]
        proposed_value = [*criteria.exclusions.employers, name]
        changes = build_proposed_changes(
            "exclusions.employers",
            EditDirection.TIGHTEN,
            criteria.exclusions.employers,
            proposed_value,
        )
        candidates.append(
            _candidate_from_changes(
                changes, signal=Signal.FAST_REJECT, observations=observations
            )
        )

    return candidates


# ---------------------------------------------------------------------------
# Public entry points
# ---------------------------------------------------------------------------


def detect_candidates(session: Session, *, now: Any = None) -> list[EditCandidate]:
    """Run every detection track over stored history and return the candidates.

    The result is **ungated** (LOOP-04 is :func:`gate`). Returns an empty list —
    never an exception — when there is no active criteria version or no status
    history to reason over.
    """
    active = get_active_criteria(session)
    if active is None:
        return []
    _version, criteria = active

    signals = collect_signals(session, now=now)
    if not signals:
        return []

    job_ids = [signal.job_id for signal in signals if signal.job_id is not None]
    rows = session.execute(
        select(Job, Company.name)
        .join(Company, Job.company_id == Company.id)
        .where(Job.id.in_(job_ids))
    ).all()
    jobs = {job.id: job for job, _company_name in rows}
    company_names = {job.id: company_name for job, company_name in rows}

    fast = [
        _Record(signal, jobs[signal.job_id], company_names[signal.job_id])
        for signal in signals
        if signal.signal is Signal.FAST_REJECT and signal.job_id in jobs
    ]
    progressed = [
        _Record(signal, jobs[signal.job_id], company_names[signal.job_id])
        for signal in signals
        if signal.signal is Signal.PROGRESSED and signal.job_id in jobs
    ]

    candidates: list[EditCandidate] = []
    weights_candidate = _track_a(fast, criteria)
    if weights_candidate is not None:
        candidates.append(weights_candidate)
    candidates.extend(_track_b(fast, criteria))
    candidates.extend(_track_c(fast, progressed, criteria))
    return candidates


def gate(candidates: Sequence[EditCandidate]) -> list[EditCandidate]:
    """LOOP-04: keep only candidates that have earned their own threshold.

    A pure function of ``EditCandidate.meets_threshold`` (which reads the
    candidate's own field threshold), so the minimum-observation table is
    consulted in exactly one place. Strongest evidence surfaces first.
    """
    return sorted(
        (candidate for candidate in candidates if candidate.meets_threshold),
        key=lambda candidate: (-candidate.observation_count, candidate.field),
    )


def build_evidence(candidate: EditCandidate) -> dict[str, Any]:
    """LOOP-05: name the specific listings and transitions behind a candidate.

    The payload is JSON-safe as-is (every value is a plain scalar, list or dict)
    and the listing list is capped at :data:`EVIDENCE_LISTING_CAP` with an
    explicit ``truncated`` flag so the cap is visible rather than silent.
    """
    return {
        "signal": candidate.signal.value,
        "observation_count": candidate.observation_count,
        "threshold": candidate.threshold,
        "truncated": len(candidate.observations) > EVIDENCE_LISTING_CAP,
        "listings": candidate.observations[:EVIDENCE_LISTING_CAP],
        "feedback_quotes": candidate.feedback_quotes,
    }
