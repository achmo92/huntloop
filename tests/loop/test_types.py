"""LOOP-03 edit-surface contract tests for ``huntloop.loop.types``.

Owned by plan 05-01. These six tests are the contract every later plan in
Phase 5 builds against — the enumerated edit surface, the per-edit observation
threshold, the dedup signature, and the JSON round-trip of ``proposed_changes``.
"""

import json
from datetime import date
from decimal import Decimal

import pytest
from pydantic import BaseModel

from huntloop.criteria.schema import CompensationFloor, CriteriaPayload
from huntloop.loop.types import (
    MIN_OBSERVATIONS,
    PERMITTED_EDIT_FIELDS,
    EditDirection,
    UnsupportedEditField,
    build_proposed_changes,
    edit_signature,
)


def _nested_model(annotation):
    """Return the BaseModel subclass behind an annotation, or None."""
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation
    return None


def test_permitted_fields_resolve_against_criteria_payload():
    """Every permitted path is a real field (or dotted leaf) on the payload.

    Walks ``model_fields`` rather than constructing an instance so the test
    stays valid if any default changes.
    """
    for path in PERMITTED_EDIT_FIELDS:
        parts = path.split(".")
        model = CriteriaPayload
        for index, part in enumerate(parts):
            assert part in model.model_fields, (
                f"{path!r} does not resolve against CriteriaPayload "
                f"(missing {'.'.join(parts[: index + 1])!r})"
            )
            if index < len(parts) - 1:
                model = _nested_model(model.model_fields[part].annotation)
                assert model is not None, (
                    f"{'.'.join(parts[: index + 1])!r} is not a nested model, "
                    f"so {path!r} cannot descend into it"
                )


def test_permitted_fields_exclude_profile_summary():
    """profile_summary is prose, not a decision rule, so it is never editable."""
    assert "profile_summary" not in PERMITTED_EDIT_FIELDS


def test_every_permitted_field_has_a_threshold():
    """One threshold per permitted field, and every threshold is >= 3 (D-05)."""
    assert set(MIN_OBSERVATIONS) == set(PERMITTED_EDIT_FIELDS)
    assert len(MIN_OBSERVATIONS) == len(PERMITTED_EDIT_FIELDS)
    assert all(value >= 3 for value in MIN_OBSERVATIONS.values())


def test_edit_signature_is_field_and_direction():
    """D-10's dedup key is exactly (field, direction)."""
    assert edit_signature(
        {
            "field": "posting_age_days",
            "direction": "tighten",
            "current_value": 30,
            "proposed_value": 20,
        }
    ) == ("posting_age_days", "tighten")


def test_edit_signature_rejects_unknown_field():
    """A stored row naming a non-permitted field is rejected, never trusted."""
    with pytest.raises(UnsupportedEditField):
        edit_signature({"field": "scoring_rubric", "direction": "tighten"})


def test_build_proposed_changes_round_trips_through_json():
    """Decimal, date and nested-model values must survive json.dumps/loads."""
    changes = build_proposed_changes(
        "compensation_floor",
        EditDirection.INCREASE,
        CompensationFloor(amount=Decimal("150000.00"), currency="USD", period="annual"),
        {
            "amount": Decimal("165000.00"),
            "currency": "USD",
            "period": "annual",
            "effective_from": date(2026, 9, 16),
        },
    )

    assert json.loads(json.dumps(changes)) == changes
    assert changes["field"] == "compensation_floor"
    assert changes["direction"] == "increase"
    assert changes["current_value"]["amount"] == "150000.00"
    assert changes["current_value"]["currency"] == "USD"
    assert changes["proposed_value"]["amount"] == "165000.00"
    assert changes["proposed_value"]["effective_from"] == "2026-09-16"
