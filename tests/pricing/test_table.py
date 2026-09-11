"""RUN-07 price table tests. Owned by plan 03-02.

One test per behavior bullet in 03-02-PLAN.md Task 1. The module is imported
directly (no importorskip): 03-02's acceptance criteria require
`pytest tests/pricing -q -rs` to report 0 skipped.
"""
from decimal import Decimal

from huntloop.pricing.table import (
    COST_QUANTUM,
    PRICE_TABLE,
    cost_for_usage,
    is_priced,
)


def test_cost_for_usage_known_model():
    # gpt-4o: $2.50 per 1M input tokens, $10.00 per 1M output tokens.
    assert cost_for_usage("gpt-4o", 1_000_000, 0) == Decimal("2.500000")
    assert cost_for_usage("gpt-4o", 0, 1_000_000) == Decimal("10.000000")
    # gpt-4o-mini: $0.15 in / $0.60 out — both rates in one call.
    assert cost_for_usage("gpt-4o-mini", 1_000_000, 1_000_000) == Decimal("0.750000")


def test_cost_for_usage_zero_tokens():
    assert cost_for_usage("gpt-4o", 0, 0) == Decimal("0.000000")


def test_cost_for_usage_unknown_model_falls_back():
    # An unpriced/self-hosted model must not raise and must not invent a price.
    assert cost_for_usage("some-local-llama", 1_000_000, 1_000_000) == Decimal("0.000000")


def test_is_priced():
    assert is_priced("gpt-4o") is True
    assert is_priced("some-local-llama") is False
    assert is_priced("") is False


def test_lookup_is_case_insensitive():
    assert cost_for_usage("GPT-4O", 1_000_000, 0) == Decimal("2.500000")


def test_every_result_has_exactly_six_decimal_places():
    for model in ("gpt-4o", "gpt-4o-mini", "gpt-4.1", "o4-mini"):
        for tokens_in, tokens_out in ((1_000_000, 0), (0, 1_000_000), (123_457, 998)):
            result = cost_for_usage(model, tokens_in, tokens_out)
            assert result == result.quantize(COST_QUANTUM)
            assert -result.as_tuple().exponent == 6


def test_every_price_table_value_is_decimal():
    # A float slipping into this table would reintroduce the binary rounding
    # error the Numeric(12,6) cost_usd column exists to avoid.
    for model, rates in PRICE_TABLE.items():
        assert isinstance(model, str), f"model key {model!r} must be str"
        assert isinstance(rates, tuple) and len(rates) == 2, (
            f"{model!r} must map to a 2-tuple"
        )
        in_rate, out_rate = rates
        assert isinstance(in_rate, Decimal), f"{model!r} input rate must be Decimal"
        assert isinstance(out_rate, Decimal), f"{model!r} output rate must be Decimal"
