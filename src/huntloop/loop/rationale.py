"""LOOP-02/D-08: the loop package's single gated model call.

This is the **only** module under ``huntloop.loop`` permitted to reach
``huntloop.llm`` — ``tests/loop/test_boundaries.py`` enforces that boundary, so
detection, thresholding and predicted-effect computation stay deterministic and
model-free by construction (D-06). Nothing here decides whether a proposal is
warranted; the LOOP-04 observation count does. These functions only *narrate*
and *corroborate* what stored data already established.

Two calls live here:

``extract_feedback_claims``
    One call per generation pass that reads the user's unconsumed ``FeedbackNote``
    rows and maps each concrete complaint onto exactly one field of the
    enumerated edit surface (:data:`PERMITTED_EDIT_FIELDS`). The schema is a
    closed vocabulary, so the model selects rather than invents, and the returned
    claims are re-checked before they are trusted. A model failure yields no
    claims — never an exception. Skipped entirely with no client or no notes, so
    a run with nothing new to read costs nothing (D-06).

``synthesize_rationale``
    Narrates an already-gated candidate from its evidence and predicted effect.
    Any failure degrades to :func:`build_fallback_rationale`, a deterministic
    string built from the same numbers — ``criteria_proposals.rationale`` is a
    NOT NULL column, so a non-empty fallback is the last line of defence.

Explicit feedback never bypasses the gate: claims are folded onto candidates as
``feedback_quotes`` and the observation count (D-08) is what crosses a
threshold. Claims that match no detected candidate are discarded, because v1
never lets the model author a ``proposed_value`` — only corroborate one.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Sequence
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from huntloop.config import load_config
from huntloop.db.models import FeedbackNote
from huntloop.llm.client import LlmCall, LlmResponseError, complete_json
from huntloop.loop.types import PERMITTED_EDIT_FIELDS, EditDirection

logger = logging.getLogger(__name__)

__all__ = [
    "FeedbackClaim",
    "FeedbackClaims",
    "RationaleOut",
    "build_fallback_rationale",
    "extract_feedback_claims",
    "synthesize_rationale",
]

# A quote longer than this is the model padding, not the user's words.
_MAX_QUOTE_CHARS = 300
# The model gets two sentences; the column is Text, but a short rationale is a
# better product decision than an essay.
_MAX_RATIONALE_CHARS = 600
# Values are printed into the fallback sentence; long payloads are truncated.
_VALUE_PREVIEW_CHARS = 60


# ---------------------------------------------------------------------------
# Schemas — a closed vocabulary, mirroring criteria.py's describe-first draft
# ---------------------------------------------------------------------------


class FeedbackClaim(BaseModel):
    """One actionable complaint, mapped onto the enumerated edit surface.

    ``field`` is a ``Literal`` over :data:`PERMITTED_EDIT_FIELDS`, so an
    unenumerated field is a validation error rather than a proposal the loop
    would have to filter downstream. Unknown extra keys are ignored: the model
    is not asked to be exact about keys it was not given.
    """

    model_config = ConfigDict(extra="ignore")

    field: Literal[*PERMITTED_EDIT_FIELDS]
    direction: EditDirection
    quote: str = Field(min_length=1, max_length=_MAX_QUOTE_CHARS)
    # The index of the note in the numbered list the prompt supplies — never the
    # note's database id, which the model has no way to know.
    note_index: int = Field(ge=0)


class FeedbackClaims(BaseModel):
    """The extraction response. An empty list is a valid, expected answer."""

    model_config = ConfigDict(extra="ignore")

    claims: list[FeedbackClaim] = Field(default_factory=list)


class RationaleOut(BaseModel):
    """The narration response — non-empty, bounded, nothing else required."""

    model_config = ConfigDict(extra="ignore")

    rationale: str = Field(min_length=1, max_length=_MAX_RATIONALE_CHARS)


# ---------------------------------------------------------------------------
# Prompts (module-level constants: byte-identical on every call)
# ---------------------------------------------------------------------------

_FIELD_LIST = "\n".join(f"- {field}" for field in PERMITTED_EDIT_FIELDS)
_DIRECTION_LIST = ", ".join(direction.value for direction in EditDirection)

_EXTRACTION_SYSTEM_PROMPT = f"""\
The user is describing what they want changed about which job listings they are \
shown. Each note may contain a concrete complaint about the listings.

Map each concrete complaint to exactly ONE field and ONE direction.

Fields (spell the field exactly as listed):
{_FIELD_LIST}

Directions: {_DIRECTION_LIST}
- tighten: narrows what discovery surfaces.
- loosen: widens what discovery surfaces.
- increase: a scoring dimension should matter more.
- decrease: a scoring dimension should matter less.

Rules:
- "quote" must be the user's own words, copied verbatim from the note.
- "note_index" is the number in brackets that precedes the note.
- Emit nothing when the text expresses no actionable preference about the \
listings; an empty "claims" list is a correct answer.
- Never invent a numeric value, a threshold, or a field name outside the list \
above. Values are computed deterministically elsewhere; you only name the \
field and the direction.
Return ONE JSON object: {{"claims": []}}.
"""

_RATIONALE_SYSTEM_PROMPT = f"""\
Explain, in two sentences, why this specific change to the user's job-search \
criteria is being proposed. Address the user directly, as "you".

Rules:
- Ground the explanation ONLY in the supplied evidence and predicted effect.
- The change touches one field, one of: {", ".join(PERMITTED_EDIT_FIELDS)}.
- Its direction is one of: {_DIRECTION_LIST}.
- Never invent numbers. Use only the counts given to you.
Return ONE JSON object: {{"rationale": "..."}}.
"""


# ---------------------------------------------------------------------------
# Small helpers
# ---------------------------------------------------------------------------


def _direction_value(direction: Any) -> str:
    """Normalise an enum member or raw string to its stored string value."""
    if isinstance(direction, EditDirection):
        return direction.value
    return str(direction)


def _format_value(value: Any) -> str:
    """A short, JSON-ish preview of a criteria value for the fallback sentence."""
    try:
        text = json.dumps(value, default=str)
    except (TypeError, ValueError):
        text = str(value)
    if len(text) > _VALUE_PREVIEW_CHARS:
        return text[: _VALUE_PREVIEW_CHARS - 3] + "..."
    return text


def _number(value: Any, default: float = 0.0) -> float:
    """Coerce a payload field to a float, tolerating strings and None."""
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


# ---------------------------------------------------------------------------
# Deterministic fallback rationale
# ---------------------------------------------------------------------------


def _effect_sentence(predicted_effect: Any) -> str:
    """One plain sentence derived from ``predicted_effect['kind']`` (LOOP-06)."""
    effect = predicted_effect if isinstance(predicted_effect, dict) else {}
    kind = effect.get("kind")
    backlog = int(_number(effect.get("backlog_size")))

    if kind == "filter_dry_run":
        return (
            f"It would exclude {int(_number(effect.get('would_exclude')))} and return "
            f"{int(_number(effect.get('would_include')))} of {backlog} listings still "
            f"in play."
        )
    if kind == "flag_recompute":
        return (
            f"It would newly flag {int(_number(effect.get('would_flag')))} and clear "
            f"{int(_number(effect.get('would_unflag')))} of {backlog} listings still "
            f"in play."
        )
    if kind == "literal_count":
        return (
            f"{int(_number(effect.get('matches')))} live listings mention it, though "
            f"discovery does not enforce this field yet."
        )
    if kind == "score_recompute":
        return (
            f"It would shift {int(_number(effect.get('affected')))} of {backlog} "
            f"backlog scores by {_number(effect.get('mean_delta')):+.2f} on average."
        )
    return "This change would affect the listings discovery surfaces from now on."


def build_fallback_rationale(candidate, evidence, predicted_effect) -> str:
    """The model-free rationale — always a non-empty string.

    Names the field, the observation count and the threshold, so the card is
    still honest about *why* the change is being proposed when no model is
    configured or the model failed. ``rationale`` is NOT NULL, so this is the
    last line of defence: it must never be empty, for any permitted field.
    """
    headline = (
        f"{candidate.observation_count} listings support changing {candidate.field} "
        f"from {_format_value(candidate.current_value)} to "
        f"{_format_value(candidate.proposed_value)} ({candidate.threshold} needed)."
    )
    parts = [headline]

    quotes = evidence.get("feedback_quotes") if isinstance(evidence, dict) else None
    if quotes:
        parts.append(f"{len(quotes)} of them are your own words.")

    parts.append(_effect_sentence(predicted_effect))
    return " ".join(parts)


# ---------------------------------------------------------------------------
# The extraction call (D-08)
# ---------------------------------------------------------------------------


def _claims_from_call(call: LlmCall, notes: Sequence[FeedbackNote]) -> list[dict[str, str]]:
    """Keep only claims that name a permitted field and a note we actually sent.

    The model's output is filtered, never trusted: an unenumerated field, an
    out-of-range ``note_index`` or an unknown direction is dropped with a log
    line rather than allowed to shape a proposal.
    """
    content = call.content if isinstance(call.content, dict) else {}
    raw_claims = content.get("claims") or []
    claims: list[dict[str, str]] = []

    for raw in raw_claims:
        if not isinstance(raw, dict):
            logger.warning("dropping malformed feedback claim: %r", raw)
            continue

        field = raw.get("field")
        if field not in PERMITTED_EDIT_FIELDS:
            logger.warning("dropping feedback claim for unpermitted field %r", field)
            continue

        index = raw.get("note_index")
        if isinstance(index, bool) or not isinstance(index, int) or not (0 <= index < len(notes)):
            logger.warning("dropping feedback claim with unusable note_index %r", index)
            continue

        quote = raw.get("quote")
        if not isinstance(quote, str) or not quote.strip():
            logger.warning("dropping feedback claim with no quote for %r", field)
            continue

        try:
            direction = EditDirection(raw.get("direction"))
        except ValueError:
            logger.warning("dropping feedback claim with unknown direction %r", raw.get("direction"))
            continue

        claims.append(
            {
                "note_id": str(notes[index].id),
                "quote": quote,
                "field": str(field),
                "direction": direction.value,
            }
        )

    return claims


def extract_feedback_claims(
    client,
    *,
    notes: Sequence[FeedbackNote],
    model: str | None = None,
) -> list[dict[str, str]]:
    """Turn unconsumed written feedback into claims against the edit surface.

    Returns entries shaped exactly like :attr:`EditCandidate.feedback_quotes`:
    ``{"note_id", "quote", "field", "direction"}``. Returns ``[]`` — never
    raising — when there is no client, no feedback, or the model misbehaves.

    D-06's cost guard lives in the first line: a pass with no new notes makes
    zero model calls.
    """
    if client is None or not notes:
        return []

    user = "\n".join(f"[{index}] {note.text}" for index, note in enumerate(notes))

    try:
        call = complete_json(
            client,
            model=model or load_config().extraction_model,
            system=_EXTRACTION_SYSTEM_PROMPT,
            user=user,
            schema=FeedbackClaims,
            temperature=0.0,
        )
    except LlmResponseError as exc:
        logger.warning("feedback extraction failed: %s", exc)
        return []
    except Exception:
        # Generation must never die because extraction did.
        logger.exception("feedback extraction raised unexpectedly; extracting no claims")
        return []

    return _claims_from_call(call, notes)


# ---------------------------------------------------------------------------
# The narration call (LOOP-02's fallback-guaranteed rationale)
# ---------------------------------------------------------------------------


def synthesize_rationale(
    client,
    *,
    candidate,
    evidence,
    predicted_effect,
    model: str | None = None,
) -> str:
    """Explain, in the user's terms, why ``candidate`` is worth their attention.

    With no client, or on any failure, returns
    :func:`build_fallback_rationale` — this function never raises and never
    returns an empty string.
    """
    fallback = build_fallback_rationale(candidate, evidence, predicted_effect)
    if client is None:
        return fallback

    user = json.dumps(
        {
            "field": candidate.field,
            "direction": _direction_value(candidate.direction),
            "current_value": candidate.current_value,
            "proposed_value": candidate.proposed_value,
            "observation_count": candidate.observation_count,
            "threshold": candidate.threshold,
            "evidence": evidence,
            "predicted_effect": predicted_effect,
        },
        default=str,
    )

    try:
        call = complete_json(
            client,
            model=model or load_config().extraction_model,
            system=_RATIONALE_SYSTEM_PROMPT,
            user=user,
            schema=RationaleOut,
            temperature=0.0,
        )
    except LlmResponseError as exc:
        logger.warning("rationale synthesis failed: %s", exc)
        return fallback
    except Exception:
        logger.exception("rationale synthesis raised unexpectedly; using the fallback")
        return fallback

    content = call.content if isinstance(call.content, dict) else {}
    text = content.get("rationale")
    if isinstance(text, str) and text.strip():
        return text.strip()

    logger.warning("rationale synthesis returned no usable text; using the fallback")
    return fallback
