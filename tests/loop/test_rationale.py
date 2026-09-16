"""LOOP-02/D-08: the loop package's one gated LLM call. Owned by plan 05-05.

``rationale.py`` is the only module in ``huntloop.loop`` allowed to reach
``huntloop.llm`` (``tests/loop/test_boundaries.py`` enforces that). Two calls
live there and both are deliberately fragile-by-design:

- :func:`extract_feedback_claims` reads the user's unconsumed written feedback
  and maps it onto the enumerated edit surface. It is skipped entirely when
  there is no client or no feedback (D-06's cost guard).
- :func:`synthesize_rationale` narrates an already-gated candidate. When the
  model is unavailable or answers badly, a deterministic string is returned
  instead — ``criteria_proposals.rationale`` is NOT NULL, so a fallback is the
  last line of defence.

Every test here injects a double for ``complete_json`` and never imports
``openai`` or touches the network.
"""

from __future__ import annotations

import uuid

import pytest
from pydantic import ValidationError

import huntloop.loop.rationale as rationale_mod
from huntloop.db.models import FeedbackNote, FeedbackSource
from huntloop.llm.client import LlmCall, LlmResponseError, LlmUsage
from huntloop.loop.predicted_effect import FIELD_TIERS
from huntloop.loop.rationale import (
    FeedbackClaim,
    build_fallback_rationale,
    extract_feedback_claims,
    synthesize_rationale,
)
from huntloop.loop.types import (
    PERMITTED_EDIT_FIELDS,
    EditCandidate,
    EditDirection,
    Signal,
)


class FakeLlmClient:
    """A callable double for ``huntloop.loop.rationale.complete_json``.

    Records every call's kwargs and returns a canned payload — enough to prove
    "exactly one call" and "this exact content was parsed", without importing
    ``openai`` or standing up an HTTP double.
    """

    def __init__(self, *, content: dict | None = None, error: Exception | None = None) -> None:
        self.content = content if content is not None else {}
        self.error = error
        self.calls: list[dict] = []

    def __call__(self, client, *, model, system, user, schema, temperature=0.0,
                 max_tokens=None) -> LlmCall:
        self.calls.append(
            {
                "client": client,
                "model": model,
                "system": system,
                "user": user,
                "schema": schema,
                "temperature": temperature,
            }
        )
        if self.error is not None:
            raise self.error
        return LlmCall(
            content=self.content,
            usage=LlmUsage(prompt_tokens=3, completion_tokens=5, model=model),
            model=model,
        )


def _note(text: str, *, source: FeedbackSource = FeedbackSource.CHAT,
          job_id: uuid.UUID | None = None) -> FeedbackNote:
    """An unpersisted general feedback note with a stable id."""
    return FeedbackNote(
        id=uuid.uuid4(), job_id=job_id, text=text, source=source
    )


def _observation(index: int = 0) -> dict:
    return {
        "job_id": str(uuid.uuid4()),
        "title": f"Engineer {index}",
        "company": f"Employer {index}",
        "status": "rejected",
        "dwell_days": 3.0,
        "transitions": ["new->rejected"],
        "attributed_to": "role_fit",
    }


def _candidate(
    field: str,
    *,
    direction: EditDirection = EditDirection.TIGHTEN,
    observations: int = 0,
    quotes: list[dict[str, str]] | None = None,
) -> EditCandidate:
    return EditCandidate(
        field=field,
        direction=direction,
        current_value="current-value",
        proposed_value="proposed-value",
        signal=Signal.FAST_REJECT,
        observations=[_observation(index) for index in range(observations)],
        feedback_quotes=list(quotes or []),
    )


def _evidence(observations: int = 3, quotes: list[dict[str, str]] | None = None) -> dict:
    return {
        "signal": Signal.FAST_REJECT.value,
        "observation_count": observations,
        "threshold": 3,
        "truncated": False,
        "listings": [],
        "feedback_quotes": list(quotes or []),
    }


_EFFECT_BY_KIND: dict[str, dict] = {
    "filter_dry_run": {
        "kind": "filter_dry_run",
        "field": "locations",
        "would_exclude": 2,
        "would_include": 1,
        "backlog_size": 12,
        "sample": [],
    },
    "flag_recompute": {
        "kind": "flag_recompute",
        "field": "seniority_max",
        "flag": "stretch_role",
        "would_flag": 3,
        "would_unflag": 1,
        "unknown": 2,
        "backlog_size": 12,
    },
    "literal_count": {
        "kind": "literal_count",
        "field": "exclusions.title_keywords",
        "term": "contract",
        "matches": 4,
        "enforced": False,
        "backlog_size": 12,
        "note": "informational only",
    },
    "score_recompute": {
        "kind": "score_recompute",
        "field": "dimension_weights",
        "affected": 5,
        "mean_delta": 0.25,
        "max_delta": 0.5,
        "backlog_size": 12,
        "skipped": 1,
    },
}


# ---------------------------------------------------------------------------
# Extraction (the D-08 call)
# ---------------------------------------------------------------------------


def test_extraction_schema_restricts_field_to_permitted_set():
    """The extraction schema is a closed vocabulary, not free text."""
    with pytest.raises(ValidationError):
        FeedbackClaim.model_validate(
            {"field": "scoring_rubric", "direction": "tighten", "quote": "x", "note_index": 0}
        )

    claim = FeedbackClaim.model_validate(
        {
            "field": PERMITTED_EDIT_FIELDS[0],
            "direction": "tighten",
            "quote": "x",
            "note_index": 0,
        }
    )
    assert claim.field == PERMITTED_EDIT_FIELDS[0]


def test_extract_returns_empty_without_client():
    """No configured client means no claims — never a crash."""
    notes = [_note("stop showing me contract roles")]

    assert extract_feedback_claims(None, notes=notes) == []


def test_extract_returns_empty_without_notes(monkeypatch):
    """D-06: no unconsumed feedback means zero model spend."""
    fake = FakeLlmClient(content={"claims": []})
    monkeypatch.setattr(rationale_mod, "complete_json", fake)

    assert extract_feedback_claims(object(), notes=[]) == []
    assert fake.calls == []


def test_extract_parses_claims(monkeypatch):
    """A well-formed claim carries the note id it was quoted from."""
    fake = FakeLlmClient(
        content={
            "claims": [
                {
                    "field": "exclusions.title_keywords",
                    "direction": "tighten",
                    "quote": "stop showing me contract roles",
                    "note_index": 0,
                }
            ]
        }
    )
    monkeypatch.setattr(rationale_mod, "complete_json", fake)
    note = _note("stop showing me contract roles")

    claims = extract_feedback_claims(object(), notes=[note])

    assert claims == [
        {
            "note_id": str(note.id),
            "quote": "stop showing me contract roles",
            "field": "exclusions.title_keywords",
            "direction": "tighten",
        }
    ]
    assert "[0]" in fake.calls[0]["user"]
    assert fake.calls[0]["schema"].__name__ == "FeedbackClaims"


def test_extract_drops_unpermitted_claims(monkeypatch):
    """The model's output is filtered, never trusted."""
    fake = FakeLlmClient(
        content={
            "claims": [
                # A field outside the enumerated edit surface.
                {
                    "field": "profile_summary",
                    "direction": "tighten",
                    "quote": "make it better",
                    "note_index": 0,
                },
                # A permitted field, but a note that was never sent.
                {
                    "field": "exclusions.employers",
                    "direction": "tighten",
                    "quote": "drop that employer",
                    "note_index": 7,
                },
            ]
        }
    )
    monkeypatch.setattr(rationale_mod, "complete_json", fake)

    assert extract_feedback_claims(object(), notes=[_note("make it better")]) == []


def test_extract_survives_llm_error(monkeypatch):
    """Generation must never die because extraction failed."""
    fake = FakeLlmClient(error=LlmResponseError("model returned nonsense"))
    monkeypatch.setattr(rationale_mod, "complete_json", fake)

    assert extract_feedback_claims(object(), notes=[_note("less contract work")]) == []
    assert len(fake.calls) == 1


def test_extract_makes_exactly_one_call(monkeypatch):
    """Five notes, one extraction call — not five."""
    fake = FakeLlmClient(
        content={
            "claims": [
                {
                    "field": "exclusions.title_keywords",
                    "direction": "tighten",
                    "quote": "q",
                    "note_index": 0,
                }
            ]
        }
    )
    monkeypatch.setattr(rationale_mod, "complete_json", fake)
    notes = [_note(f"note {index}") for index in range(5)]

    claims = extract_feedback_claims(object(), notes=notes)

    assert len(fake.calls) == 1
    assert fake.calls[0]["temperature"] == 0.0
    assert claims[0]["note_id"] == str(notes[0].id)


# ---------------------------------------------------------------------------
# Rationale synthesis (the narration call + its fallback)
# ---------------------------------------------------------------------------


def test_synthesize_uses_fallback_without_client():
    """No client: the deterministic string names field, count and threshold."""
    candidate = _candidate(
        "dimension_weights", direction=EditDirection.INCREASE, observations=4
    )

    text = synthesize_rationale(
        None,
        candidate=candidate,
        evidence=_evidence(4),
        predicted_effect=_EFFECT_BY_KIND["score_recompute"],
    )

    assert candidate.field in text
    assert str(candidate.observation_count) in text
    assert str(candidate.threshold) in text


def test_synthesize_falls_back_on_llm_error(monkeypatch):
    """A failing model degrades to the fallback rather than raising."""
    fake = FakeLlmClient(error=LlmResponseError("boom"))
    monkeypatch.setattr(rationale_mod, "complete_json", fake)
    candidate = _candidate("posting_age_days", observations=5)
    effect = _EFFECT_BY_KIND["filter_dry_run"]

    text = synthesize_rationale(
        object(), candidate=candidate, evidence=_evidence(5), predicted_effect=effect
    )

    assert text == build_fallback_rationale(candidate, _evidence(5), effect)


def test_synthesize_returns_model_text(monkeypatch):
    """A well-formed response is used verbatim."""
    fake = FakeLlmClient(
        content={"rationale": "Because the recent rejections all share this pattern."}
    )
    monkeypatch.setattr(rationale_mod, "complete_json", fake)
    candidate = _candidate("seniority_max", observations=4)

    text = synthesize_rationale(
        object(),
        candidate=candidate,
        evidence=_evidence(4),
        predicted_effect=_EFFECT_BY_KIND["flag_recompute"],
    )

    assert text == "Because the recent rejections all share this pattern."
    assert fake.calls[0]["schema"].__name__ == "RationaleOut"


def test_rationale_is_never_empty():
    """Every permitted field has a >=40 character fallback (rationale is NOT NULL)."""
    for field in PERMITTED_EDIT_FIELDS:
        candidate = _candidate(field, observations=3)
        effect = _EFFECT_BY_KIND[FIELD_TIERS[field]]

        text = build_fallback_rationale(candidate, _evidence(3), effect)

        assert isinstance(text, str), field
        assert len(text) >= 40, (field, text)
