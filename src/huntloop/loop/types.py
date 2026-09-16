"""The adaptive feedback loop's shared contract module (LOOP-03, LOOP-08).

Every other module in ``huntloop.loop`` and every API router that fronts it
imports its vocabulary from here rather than inventing its own: the enumerated
edit surface (:data:`PERMITTED_EDIT_FIELDS`), the per-edit observation
threshold (:data:`MIN_OBSERVATIONS`), the stored-signal vocabulary
(:class:`Signal`), the edit-direction vocabulary (:class:`EditDirection`), and
the ``proposed_changes`` JSON shape built by :func:`build_proposed_changes`.

LOOP-03: the edit surface is a single named constant. A proposal may only ever
touch one of the nine dotted paths enumerated below — never the rubric, whose
configuration module is deliberately outside this package's import graph
(LOOP-08, guarded by ``tests/loop/test_boundaries.py``). A proposal that names
anything else raises :class:`UnsupportedEditField`, which is the runtime gate
that makes "the rubric is out of bounds" a structural fact rather than a policy.
"""

from __future__ import annotations

import enum
from collections.abc import Mapping
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import date, datetime
from decimal import Decimal
from typing import Any

from pydantic import BaseModel

from huntloop.criteria.schema import CriteriaPayload

__all__ = [
    "FAST_REJECT_DWELL_DAYS",
    "MIN_OBSERVATIONS",
    "PERMITTED_EDIT_FIELDS",
    "EditCandidate",
    "EditDirection",
    "Signal",
    "UnsupportedEditField",
    "build_proposed_changes",
    "edit_signature",
    "resolve_field",
]

# ---------------------------------------------------------------------------
# Detection constants
# ---------------------------------------------------------------------------

# D-01 / 05-RESEARCH open question 1: a rejection within this many days of a
# listing first being seen counts as "fast" — the score was most likely wrong
# from the start, rather than the role looking right on paper and failing in
# practice. An internal tuning knob, deliberately not a user Setting.
FAST_REJECT_DWELL_DAYS = 10


class Signal(str, enum.Enum):
    """What actually happened to a surfaced listing (LOOP-01).

    Derived from stored ``StatusEvent`` history — never asked of the user and
    never asked of a model.
    """

    FAST_REJECT = "fast_reject"  # NEW|SHORTLISTED -> REJECTED within FAST_REJECT_DWELL_DAYS
    LATE_REJECT = "late_reject"  # APPLIED|INTERVIEWING -> REJECTED
    PROGRESSED = "progressed"  # advanced along SHORTLISTED->APPLIED->INTERVIEWING->OFFER
    NEUTRAL = "neutral"  # everything else, including WITHDRAWN


class EditDirection(str, enum.Enum):
    """Which way a proposed edit moves a criteria field."""

    TIGHTEN = "tighten"  # narrows what discovery surfaces
    LOOSEN = "loosen"  # widens what discovery surfaces (reserved; not emitted in v1)
    INCREASE = "increase"  # a dimension weight goes up
    DECREASE = "decrease"  # a dimension weight goes down (reserved; not emitted in v1)


# ---------------------------------------------------------------------------
# LOOP-03: the enumerated edit surface
# ---------------------------------------------------------------------------

# D-04: this IS the full editable payload surface from
# ``huntloop.criteria.schema`` — no field is arbitrarily excluded. Nested
# fields are addressed by dotted leaf path so a proposal still touches exactly
# one thing (D-06a). The freeform profile narrative is deliberately absent: it
# is prose, not a decision rule.
PERMITTED_EDIT_FIELDS: tuple[str, ...] = (
    "seniority_min",
    "seniority_max",
    "posting_age_days",
    "locations",
    "compensation_floor",
    "exclusions.title_keywords",
    "exclusions.employers",
    "work_authorization",
    "dimension_weights",
)

# D-05: a fixed count per edit type, scaled by blast radius.
# dimension_weights rescores the whole backlog -> highest bar.
# hard filters drop listings outright -> high bar.
# advisory-flag-only fields change presentation, not inclusion -> medium bar.
# exclusions/work_authorization are informational in v1 (not yet consumed by the
# pipeline per 05-RESEARCH "Pitfall 1") -> lowest bar.
MIN_OBSERVATIONS: dict[str, int] = {
    "dimension_weights": 8,
    "posting_age_days": 5,
    "locations": 5,
    "seniority_min": 4,
    "seniority_max": 4,
    "compensation_floor": 4,
    "work_authorization": 4,
    "exclusions.title_keywords": 3,
    "exclusions.employers": 3,
}


class UnsupportedEditField(ValueError):
    """Raised whenever a field outside :data:`PERMITTED_EDIT_FIELDS` is used.

    This is LOOP-03's runtime gate: it is raised by
    :func:`build_proposed_changes`, :func:`edit_signature` and
    :func:`resolve_field`, so no code path can construct, dedup or apply a
    proposal against an unenumerated field.
    """


# ---------------------------------------------------------------------------
# Candidate shape
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class EditCandidate:
    """One detected, evidence-backed candidate edit — before it is a proposal.

    Produced by detection and consumed by the threshold gate: a candidate only
    becomes a ``CriteriaProposal`` once :attr:`meets_threshold` is true.
    """

    field: str
    direction: EditDirection
    current_value: Any
    proposed_value: Any
    signal: Signal
    observations: list[dict[str, Any]] = dataclass_field(default_factory=list)
    # D-08: the user's own words count toward the LOOP-04 gate as evidence —
    # they never bypass it.
    feedback_quotes: list[dict[str, str]] = dataclass_field(default_factory=list)

    @property
    def observation_count(self) -> int:
        """Implicit observations plus quoted explicit feedback (D-08)."""
        return len(self.observations) + len(self.feedback_quotes)

    @property
    def threshold(self) -> int:
        """The LOOP-04 minimum observation count for this edit type (D-05)."""
        return MIN_OBSERVATIONS[self.field]

    @property
    def meets_threshold(self) -> bool:
        """Whether this candidate has earned the right to become a proposal."""
        return self.observation_count >= self.threshold


# ---------------------------------------------------------------------------
# Serialisation helpers
# ---------------------------------------------------------------------------


def _jsonable(value: Any) -> Any:
    """Convert ``value`` into something the JSON column can round-trip.

    ``Decimal`` becomes a string (JSON has no decimal type and a float would
    lose money precision) and ``date``/``datetime`` become ISO-8601 strings.
    Dicts, lists and Pydantic models are recursed into so a whole nested
    criteria model can be stored as ``current_value``.
    """
    if isinstance(value, BaseModel):
        return _jsonable(value.model_dump(mode="python"))
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Mapping):
        return {key: _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    return value


def _direction_value(direction: EditDirection | str) -> str:
    """Normalise a direction to its stored string value."""
    if isinstance(direction, EditDirection):
        return direction.value
    return str(direction)


# ---------------------------------------------------------------------------
# Public contract functions
# ---------------------------------------------------------------------------


def build_proposed_changes(
    field: str,
    direction: EditDirection | str,
    current_value: Any,
    proposed_value: Any,
) -> dict[str, Any]:
    """Build the ``criteria_proposals.proposed_changes`` JSON payload.

    Raises :class:`UnsupportedEditField` if ``field`` is not one of the
    enumerated :data:`PERMITTED_EDIT_FIELDS`, so an unenumerated edit can never
    reach the database. Both values are made JSON-safe by :func:`_jsonable`.
    """
    if field not in PERMITTED_EDIT_FIELDS:
        raise UnsupportedEditField(
            f"{field!r} is not a permitted edit field. "
            f"Permitted: {', '.join(PERMITTED_EDIT_FIELDS)}"
        )
    return {
        "field": field,
        "direction": _direction_value(direction),
        "current_value": _jsonable(current_value),
        "proposed_value": _jsonable(proposed_value),
    }


def edit_signature(proposed_changes: Mapping[str, Any]) -> tuple[str, str]:
    """Return D-10's dedup key — ``(field, direction)`` — from a stored row.

    Computable from ``proposed_changes`` alone, so no extra column is needed to
    find the rejection history of a repeatedly-rejected suggestion.
    """
    field = proposed_changes["field"]
    if field not in PERMITTED_EDIT_FIELDS:
        raise UnsupportedEditField(
            f"{field!r} is not a permitted edit field. "
            f"Permitted: {', '.join(PERMITTED_EDIT_FIELDS)}"
        )
    return (field, _direction_value(proposed_changes["direction"]))


def resolve_field(payload: CriteriaPayload, field: str) -> Any:
    """Return the current value of ``field`` on ``payload``.

    Walks the dotted path (``exclusions.title_keywords`` descends into the
    nested model) so a caller can populate ``current_value`` without duplicating
    the permitted-path enumeration. Raises :class:`UnsupportedEditField` for any
    path outside :data:`PERMITTED_EDIT_FIELDS`.
    """
    if field not in PERMITTED_EDIT_FIELDS:
        raise UnsupportedEditField(
            f"{field!r} is not a permitted edit field. "
            f"Permitted: {', '.join(PERMITTED_EDIT_FIELDS)}"
        )
    target: Any = payload
    for part in field.split("."):
        target = getattr(target, part)
    return target
