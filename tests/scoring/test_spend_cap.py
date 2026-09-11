"""RUN-08 spend-cap tests. Owned by plan 03-02.

Owning test names (03-VALIDATION.md): test_cap_reached_before_triage_stops_listing,
test_cap_reached_before_scoring_stops_listing, test_spend_tracker_thread_safe.
"""
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal

import pytest

from huntloop.llm.client import LlmUsage
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
