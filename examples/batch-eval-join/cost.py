"""Pure batch cost estimate from token counts and a dated price table.

No I/O. Prices are a source-dated claim, not a live lookup.
"""

from __future__ import annotations

from dataclasses import dataclass

# Batch processing guide, pricing table, fetched 2026-10-19: a flat 50% off
# the standard per-MTok price. Re-check before trusting these numbers.
PRICES_FETCHED = "2026-10-19"
BATCH_DISCOUNT = 0.5


@dataclass(frozen=True)
class Price:
    """Standard (non-batch) USD per million tokens."""

    input_per_mtok: float
    output_per_mtok: float


STANDARD_PRICES = {
    "claude-haiku-4-5": Price(input_per_mtok=1.0, output_per_mtok=5.0),
    "claude-sonnet-5": Price(input_per_mtok=2.0, output_per_mtok=10.0),
}


def batch_cost_usd(model: str, input_tokens: int, output_tokens: int) -> float:
    """Batch-priced USD cost of the given token totals for ``model``.

    Raises:
        KeyError: ``model`` has no entry in STANDARD_PRICES (no guessed price).
        ValueError: a negative token count.
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError(
            f"token counts must be >= 0, got {input_tokens=} {output_tokens=}"
        )
    if model not in STANDARD_PRICES:
        raise KeyError(f"no price recorded for model {model!r}")
    price = STANDARD_PRICES[model]
    standard = (
        input_tokens * price.input_per_mtok + output_tokens * price.output_per_mtok
    ) / 1_000_000
    return standard * BATCH_DISCOUNT
