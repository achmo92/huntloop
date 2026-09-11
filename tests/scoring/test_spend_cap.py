"""RUN-08 spend-cap tests. Owned by plan 03-02.

Owning test names (03-VALIDATION.md): test_cap_reached_before_triage_stops_listing,
test_cap_reached_before_scoring_stops_listing, test_spend_tracker_thread_safe.
"""
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import pytest

from huntloop.criteria.schema import (
    CompensationFloor,
    CriteriaPayload,
    DimensionWeights,
    LocationCriteria,
)
from huntloop.discovery.ats.base import RawListing
from huntloop.llm.client import LlmUsage
from huntloop.pricing.table import cost_for_usage
from huntloop.scoring.pipeline import score_listing
from huntloop.scoring.spend_cap import SpendCapReached, SpendTracker


class TestSpendTrackerCheck:
    def test_no_cap_never_raises(self):
        tracker = SpendTracker(cap_usd=None)
        assert tracker.check() is None
        tracker.record(LlmUsage(prompt_tokens=10_000_000, completion_tokens=0, model="gpt-4o"))
        assert tracker.check() is None

    def test_check_returns_none_while_under_cap(self):
        tracker = SpendTracker(cap_usd=Decimal("1.00"))
        assert tracker.check() is None
        tracker.record(LlmUsage(prompt_tokens=0, completion_tokens=10_000, model="gpt-4o"))
        # 10k output tokens of gpt-4o = $0.10 — still under the $1.00 cap.
        assert tracker.check() is None

    def test_check_raises_once_cap_met(self):
        tracker = SpendTracker(cap_usd=Decimal("1.00"))
        # 100k output tokens of gpt-4o = exactly $1.00.
        tracker.record(LlmUsage(prompt_tokens=0, completion_tokens=100_000, model="gpt-4o"))
        with pytest.raises(SpendCapReached):
            tracker.check()

    def test_message_contains_cap_and_spend_as_usd(self):
        tracker = SpendTracker(cap_usd=Decimal("2.00"))
        # 201k output tokens of gpt-4o = $2.01.
        tracker.record(LlmUsage(prompt_tokens=0, completion_tokens=201_000, model="gpt-4o"))
        with pytest.raises(SpendCapReached) as excinfo:
            tracker.check()
        message = str(excinfo.value)
        assert "$2.00" in message
        assert "$2.01" in message


class TestSpendTrackerRecord:
    def test_record_accumulates_tokens_and_usd(self):
        tracker = SpendTracker(cap_usd=None)
        tracker.record(LlmUsage(prompt_tokens=1000, completion_tokens=500, model="gpt-4o"))
        assert tracker.tokens_in == 1000
        assert tracker.tokens_out == 500
        # 1000 gpt-4o input tokens = $0.0025; 500 output = $0.005.
        assert tracker.spent_usd == Decimal("0.007500")

    def test_record_none_is_a_noop(self):
        # triage_listing returns usage=None on a failed call.
        tracker = SpendTracker(cap_usd=None)
        tracker.record(None)
        assert tracker.tokens_in == 0
        assert tracker.tokens_out == 0
        assert tracker.spent_usd == Decimal("0.000000")
        assert tracker.unpriced_models == frozenset()

    def test_record_unpriced_model_accumulates_tokens_not_dollars(self):
        tracker = SpendTracker(cap_usd=None)
        tracker.record(
            LlmUsage(prompt_tokens=1000, completion_tokens=500, model="some-local-llama")
        )
        assert tracker.tokens_in == 1000
        assert tracker.tokens_out == 500
        assert tracker.spent_usd == Decimal("0.000000")
        assert "some-local-llama" in tracker.unpriced_models


def test_spend_tracker_thread_safe():
    """16 threads x 100 record() calls must lose nothing (Pitfall D).

    LangGraph's sync invoke runs each Send-dispatched employer node in a real
    OS thread, so this accumulator is genuinely concurrent, not cooperatively
    scheduled.
    """
    tracker = SpendTracker(cap_usd=None)
    usage = LlmUsage(prompt_tokens=100, completion_tokens=50, model="gpt-4o")

    def record_100(_):
        for _ in range(100):
            tracker.record(usage)

    with ThreadPoolExecutor(max_workers=16) as pool:
        list(pool.map(record_100, range(16)))

    # 1600 calls x (100 in / 50 out) tokens; each call costs $0.00075.
    assert tracker.tokens_in == 160_000
    assert tracker.tokens_out == 80_000
    assert tracker.spent_usd == Decimal("1.200000")


def test_reason_is_human_readable():
    tracker = SpendTracker(cap_usd=Decimal("2.00"))
    tracker.record(LlmUsage(prompt_tokens=0, completion_tokens=201_000, model="gpt-4o"))
    assert (
        tracker.reason()
        == "stopped: spend cap of $2.00 reached after $2.01 of model spend"
    )


# ---------------------------------------------------------------------------
# score_listing cap enforcement (Task 3). The client double mirrors
# tests/scoring/test_graph.py's RecordingClient: usage is always 10 prompt /
# 20 completion tokens on the configured model.
# ---------------------------------------------------------------------------


class _RecordingClient:
    """Minimal fake OpenAI client: returns queued JSON responses, 10/20 usage."""

    def __init__(self, responses: list[dict]) -> None:
        self.responses = responses
        self.call_idx = 0

    class _Completions:
        def __init__(self, parent):
            self.parent = parent

        def create(self, **kwargs):
            from collections import namedtuple

            self.parent.call_idx += 1
            resp = self.parent.responses[self.parent.call_idx - 1]
            Choice = namedtuple("Choice", ["message"])
            Message = namedtuple("Message", ["content"])
            Usage = namedtuple("Usage", ["prompt_tokens", "completion_tokens"])
            choice = Choice(message=Message(content=json.dumps(resp)))

            class FakeCompletion:
                choices = [choice]
                usage = Usage(prompt_tokens=10, completion_tokens=20)
                model = kwargs.get("model", "fake-model")

            return FakeCompletion()

    @property
    def chat(self):
        client = self

        class _Chat:
            completions = client._Completions(client)

        return _Chat()


def _cap_criteria():
    return CriteriaPayload(
        profile_summary="Senior Python backend developer",
        seniority_min="senior",
        seniority_max="principal",
        dimension_weights=DimensionWeights(
            role_fit=0.4,
            seniority_fit=0.2,
            employer_fit=0.2,
            trajectory=0.2,
        ),
        locations=LocationCriteria(eligible_countries=["US"]),
        compensation_floor=CompensationFloor(amount="120000", currency="USD", period="annual"),
    )


def _cap_listing():
    return RawListing(
        external_id="ext-1",
        url="https://example.com/job/1",
        title="Senior Software Engineer",
        description_plain="We are looking for a Python engineer...",
        location_raw="New York, NY",
        comp_raw="$130k - $150k",
        posted_at=datetime.now(timezone.utc),
    )


class TestScoreListingCapEnforcement:
    def test_cap_reached_before_triage_stops_listing(self, monkeypatch):
        """Cap already met -> SpendCapReached raised, triage never called."""
        from huntloop.config import load_config

        tracker = SpendTracker(cap_usd=Decimal("0.01"))
        # One gpt-4o call with 1M input tokens = $2.50, far past the $0.01 cap.
        tracker.record(LlmUsage(prompt_tokens=1_000_000, completion_tokens=0, model="gpt-4o"))

        calls: list = []
        monkeypatch.setattr(
            "huntloop.scoring.pipeline.triage_listing",
            lambda *a, **k: calls.append("triage") or _raise(),
        )

        with pytest.raises(SpendCapReached):
            score_listing(
                _RecordingClient([]),
                _cap_listing(),
                _cap_criteria(),
                criteria_version=1,
                spend_tracker=tracker,
            )
        assert calls == []

    def test_cap_reached_before_scoring_stops_listing(self, monkeypatch):
        """Cap met by the triage call's own cost -> scoring never called."""
        from huntloop.config import load_config

        triage_model = load_config().triage_model
        # The cap is exactly the triage call's cost (10 in / 20 out tokens).
        cap = cost_for_usage(triage_model, 10, 20)
        assert cap > 0, f"triage model {triage_model!r} must be priced for this test"
        tracker = SpendTracker(cap_usd=cap)

        scoring_calls: list = []
        monkeypatch.setattr(
            "huntloop.scoring.pipeline.score_dimensions",
            lambda *a, **k: scoring_calls.append("scoring"),
        )

        with pytest.raises(SpendCapReached):
            score_listing(
                _RecordingClient([{"keep": True, "reason": "Looks good"}]),
                _cap_listing(),
                _cap_criteria(),
                criteria_version=1,
                spend_tracker=tracker,
            )
        assert scoring_calls == []
        # The triage call's usage IS recorded on the tracker: the tracker, not
        # the (never-constructed) ScoredListing, is the authoritative ledger.
        assert tracker.tokens_in == 10
        assert tracker.tokens_out == 20

    def test_no_tracker_default_path_unchanged(self):
        """spend_tracker=None behaves exactly as before: a ScoredListing comes back."""
        result = score_listing(
            _RecordingClient(
                [
                    {"keep": True, "reason": "Looks good"},
                    {
                        "role_fit": {"score": 5, "reason": "R"},
                        "seniority_fit": {"score": None, "reason": "R"},
                        "employer_fit": {"score": 3, "reason": "R"},
                        "trajectory": {"score": 3, "reason": "R"},
                        "summary": "Solid",
                    },
                ]
            ),
            _cap_listing(),
            _cap_criteria(),
            criteria_version=1,
        )
        assert result.scored is True

    def test_filter_dropped_listing_never_touches_tracker(self):
        """A deterministic-filter drop returns normally even with the cap met."""
        tracker = SpendTracker(cap_usd=Decimal("0.01"))
        tracker.record(LlmUsage(prompt_tokens=1_000_000, completion_tokens=0, model="gpt-4o"))

        criteria = _cap_criteria().model_copy()
        criteria.posting_age_days = 7
        stale = RawListing(
            external_id="1",
            url="1",
            title="Engineering Manager",
            description_plain="desc",
            location_raw="Remote",
            posted_at=datetime.now(timezone.utc) - timedelta(days=10),
        )

        result = score_listing(
            _RecordingClient([]),
            stale,
            criteria,
            criteria_version=1,
            spend_tracker=tracker,
        )
        assert result.scored is False
        assert result.drop_reason != ""

    def test_triage_dropped_listing_records_usage_and_returns_normally(self):
        """A triage drop is not a cap event: usage recorded, no exception."""
        tracker = SpendTracker(cap_usd=Decimal("10.00"))

        result = score_listing(
            _RecordingClient([{"keep": False, "reason": "Not a match"}]),
            _cap_listing(),
            _cap_criteria(),
            criteria_version=1,
            spend_tracker=tracker,
        )
        assert result.scored is False
        assert "triage: Not a match" in result.drop_reason
        assert tracker.tokens_in == 10
        assert tracker.tokens_out == 20


def _raise():
    raise AssertionError("this fake should never actually be invoked")
