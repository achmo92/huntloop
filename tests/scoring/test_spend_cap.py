"""RUN-08 spend-cap tests. Owned by plan 03-02.

Owning test names (03-VALIDATION.md): test_cap_reached_before_triage_stops_listing,
test_cap_reached_before_scoring_stops_listing, test_spend_tracker_thread_safe.
"""
import pytest

spend_cap = pytest.importorskip("huntloop.scoring.spend_cap")


def test_spend_cap_module_exposes_tracker_and_exception():
    assert hasattr(spend_cap, "SpendTracker")
    assert issubclass(spend_cap.SpendCapReached, Exception)
