"""Turns a token count into dollars at a given per-MTok rate.

Pure arithmetic. The rates themselves are not here: they are model-specific and
change, so they live as named constants at the entry point (`main.USD_IN_PER_MTOK`
/ `USD_OUT_PER_MTOK`) and are passed in. A pricing table baked into a library
function is a stale pricing table.

See the research note this came from:
    research/2026-08-12-server-side-compaction.md
"""

from __future__ import annotations

# Anthropic prices per million tokens; this is the divisor that word implies.
TOKENS_PER_MTOK = 1_000_000

# Results are rounded to micro-dollars. Not to cents: a compaction iteration on a
# demo transcript costs a few tenths of a cent, and rounding that to 0.00 would
# make the one number the example exists to show read as free.
USD_PRECISION = 6


def estimate_usd(
    input_tokens: int,
    output_tokens: int,
    usd_in_per_mtok: float,
    usd_out_per_mtok: float,
) -> float:
    """Estimated dollars for ``input_tokens`` in and ``output_tokens`` out.

    An *estimate*: it ignores prompt caching (cached reads and cache writes are
    priced differently), batch and priority tiers, and any discount. For the
    compaction iteration those simplifications hold - it is a fresh pass with no
    cache hit - which is the number this example reports.

    Pure. Failure modes: ``ValueError`` if any argument is negative.
    """
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError(
            "token counts must be >= 0, got "
            f"input={input_tokens}, output={output_tokens}"
        )
    if usd_in_per_mtok < 0 or usd_out_per_mtok < 0:
        raise ValueError(
            "prices must be >= 0, got "
            f"in={usd_in_per_mtok}, out={usd_out_per_mtok}"
        )

    dollars = (
        input_tokens * usd_in_per_mtok + output_tokens * usd_out_per_mtok
    ) / TOKENS_PER_MTOK
    return round(dollars, USD_PRECISION)
