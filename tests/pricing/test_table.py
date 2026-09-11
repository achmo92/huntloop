"""RUN-07 price table tests. Owned by plan 03-02.

The importorskip below lifts the moment huntloop.pricing.table exists; 03-02's
acceptance criteria require `pytest tests/pricing -q -rs` to report 0 skipped.
"""
import pytest

table = pytest.importorskip("huntloop.pricing.table")


def test_price_table_module_exposes_cost_for_usage():
    assert callable(table.cost_for_usage)
