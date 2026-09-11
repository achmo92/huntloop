"""RUN-07. USD cost for token usage, computed from a static in-repo table.

Deliberately not a third-party pricing library. OPS-06 lets the user point
HUNTLOOP_OPENAI_BASE_URL at ANY OpenAI-compatible endpoint — a self-hosted
model has no OpenAI price, and a library that assumes one would invent a
number. An unknown model costs Decimal("0") here and reports is_priced() ==
False, so the operator can see "unpriced" rather than trust a fabrication.

Prices are USD per 1,000,000 tokens, captured from OpenAI's public pricing on
2026-09-11. This table is code: change it in a commit, review it like code.
"""

from __future__ import annotations

from decimal import Decimal

COST_QUANTUM = Decimal("0.000001")  # matches runs.cost_usd -> Numeric(12, 6)
TOKENS_PER_PRICE_UNIT = Decimal("1000000")

# model name (lowercased) -> (input USD per 1M tokens, output USD per 1M tokens)
PRICE_TABLE: dict[str, tuple[Decimal, Decimal]] = {
    "gpt-4o": (Decimal("2.50"), Decimal("10.00")),
    "gpt-4o-mini": (Decimal("0.15"), Decimal("0.60")),
    "gpt-4.1": (Decimal("2.00"), Decimal("8.00")),
    "gpt-4.1-mini": (Decimal("0.40"), Decimal("1.60")),
    "gpt-4.1-nano": (Decimal("0.10"), Decimal("0.40")),
    "o4-mini": (Decimal("1.10"), Decimal("4.40")),
}


def is_priced(model: str) -> bool:
    """True when this model name has an entry in PRICE_TABLE."""
    return bool(model) and model.strip().lower() in PRICE_TABLE


def cost_for_usage(model: str, tokens_in: int, tokens_out: int) -> Decimal:
    """USD cost of one model call's token usage, quantized to 6 dp.

    Never raises and never guesses: an unpriced model contributes Decimal("0")
    to the run's cost. Callers that need to surface "we could not price this"
    should ask is_priced() — a zero here is not a claim that the call was free,
    it is a refusal to invent a price for an endpoint we do not know.
    """
    rates = PRICE_TABLE.get((model or "").strip().lower())
    if rates is None:
        return Decimal("0").quantize(COST_QUANTUM)
    in_rate, out_rate = rates
    total = (
        (Decimal(int(tokens_in)) / TOKENS_PER_PRICE_UNIT) * in_rate
        + (Decimal(int(tokens_out)) / TOKENS_PER_PRICE_UNIT) * out_rate
    )
    return total.quantize(COST_QUANTUM)
