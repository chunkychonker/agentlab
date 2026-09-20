"""Turns one clearing turn's cache counters into the number of later turns it
takes to pay back the cache write the clear forced.

Pure arithmetic and one renderer: no `anthropic` import, no I/O, no env reads,
no clock. The base input rate is *not* here - it is model-specific and changes,
so it lives in one constant at the entry point (`main.BASE_USD_PER_MTOK`) and is
passed in. A pricing table baked into a library function is a stale pricing
table (same reasoning as `prompt-caching-tool-loop/report.py`).

Layer 2 (spec), asserted in `test_payback.py`:

  - `removed` is what the clear deleted from the message list; `rewritten` is
    `cache_creation_input_tokens` on the turn the clear fired, i.e. the prefix
    the invalidation forced the API to write again.
  - The one-time cost of the invalidation is the *premium* of a cold write over
    what re-reading those same tokens would have cost:
    `rewritten x (1.25 - 0.10) x base`.
  - Each later turn saves `removed x 0.10 x base`, because those tokens are no
    longer in the prefix being re-read at the cached rate.
  - Payback is the first over the second. The base rate cancels, so payback is
    a pure token ratio: `11.5 x rewritten / removed` turns. A clear that removed
    nothing never pays back - `math.inf`, not a `ZeroDivisionError`.

Two assumptions this model makes, both stated in the README: the cleared region
was below a cache breakpoint and was being re-read every turn (true for deep
history, not for the live tail), and future turns are otherwise the same size
(a transcript that keeps growing would also inflate future writes, which this
ignores - so the number is conservative).

See the research note this came from:
    research/2026-09-07-context-editing-cache-tradeoff.md
"""

from __future__ import annotations

import dataclasses
import math

# Multipliers on the model's base *input* rate (prompt-caching docs, re-read
# 2026-09-07). The 1-hour TTL's 2x write multiplier is out of scope.
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

# Anthropic prices per million tokens; this is the divisor that word implies.
TOKENS_PER_MTOK = 1_000_000

# Micro-dollars. Not cents: one clearing turn on a demo transcript moves a few
# thousandths of a dollar, and rounding that to 0.00 would print the two numbers
# this example exists to compare as an identical zero.
USD_PRECISION = 6

# Turns are counted, so three decimals is already more resolution than the thing
# being measured has. It is enough to tell 1.4 turns from 9.6.
TURNS_PRECISION = 3


@dataclasses.dataclass(frozen=True)
class TurnUsage:
    """The three input counters one `messages.create` response reports.

    They partition the prompt - `input_tokens` is only the remainder *after* the
    last cache breakpoint, not the whole thing:

        total prompt = input_tokens + cache_creation_input_tokens
                                    + cache_read_input_tokens

    A `TurnUsage` that exists is a usable one: every field is a non-negative
    `int`, so nothing downstream re-checks.

    Failure modes: `TypeError` if a field is not an `int` (the SDK types the two
    cache counters as optional, and `None` reaching the arithmetic would read as
    a run that cached nothing); `ValueError` if a field is negative.
    """

    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    input_tokens: int

    def __post_init__(self) -> None:
        for name in (
            "cache_creation_input_tokens",
            "cache_read_input_tokens",
            "input_tokens",
        ):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}")
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")

    @property
    def total_input_tokens(self) -> int:
        """The whole prompt: the identity above, which `input_tokens` alone is not."""
        return (
            self.input_tokens
            + self.cache_creation_input_tokens
            + self.cache_read_input_tokens
        )


@dataclasses.dataclass(frozen=True)
class Tradeoff:
    """What one clearing turn cost, what it saves per turn, and where they cross.

    Only three fields are stored - two token counts and the rate - and every
    figure below is derived from them, so no constructor has to remember to keep
    six numbers consistent with each other.

    Failure modes: `TypeError` if `removed` or `rewritten` is not an `int`;
    `ValueError` if either is negative, or if `base_usd_per_mtok` is negative.
    """

    removed: int
    rewritten: int
    base_usd_per_mtok: float

    def __post_init__(self) -> None:
        for name in ("removed", "rewritten"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, int):
                raise TypeError(f"{name} must be an int, got {type(value).__name__}")
            if value < 0:
                raise ValueError(f"{name} must be >= 0, got {value}")
        if self.base_usd_per_mtok < 0:
            raise ValueError(
                f"base_usd_per_mtok must be >= 0, got {self.base_usd_per_mtok}"
            )

    @property
    def invalidation_cost_usd(self) -> float:
        """The one-time price of the cache write the clear forced.

        Not the whole write - the *premium* over what re-reading those same
        tokens would have cost, `(1.25 - 0.10)x base`. Charging the full 1.25x
        would double-count: without the clear those tokens would still have been
        paid for, at the 0.10x read rate.
        """
        return round(self._invalidation_cost_raw(), USD_PRECISION)

    @property
    def saving_per_future_turn_usd(self) -> float:
        """What every turn after the clear saves by no longer re-reading the
        removed tokens at the 0.10x cached-read rate."""
        return round(self._saving_per_future_turn_raw(), USD_PRECISION)

    @property
    def payback_turns(self) -> float:
        """Turns after the clear before the saving covers the write premium.

        The base rate appears in both halves and cancels, so this is a pure
        token ratio - `(1.25 - 0.10) / 0.10 x rewritten / removed`, i.e.
        `11.5 x rewritten / removed`. Payback does not depend on the model's
        price, which is why `clear_at_least` (a token knob) is the only lever on
        it.

        `math.inf` when nothing was removed: a clear that freed no tokens saves
        nothing per turn and never pays back. That is a true statement about a
        wasted invalidation, not a `ZeroDivisionError`.
        """
        saving = self._saving_per_future_turn_raw()
        if saving <= 0:
            return math.inf
        return round(self._invalidation_cost_raw() / saving, TURNS_PRECISION)

    def _invalidation_cost_raw(self) -> float:
        premium = CACHE_WRITE_5M_MULTIPLIER - CACHE_READ_MULTIPLIER
        return self.rewritten * premium * self.base_usd_per_mtok / TOKENS_PER_MTOK

    def _saving_per_future_turn_raw(self) -> float:
        return (
            self.removed
            * CACHE_READ_MULTIPLIER
            * self.base_usd_per_mtok
            / TOKENS_PER_MTOK
        )


def summarize(
    clearing_turn: TurnUsage, *, removed: int, base_usd_per_mtok: float
) -> Tradeoff:
    """Price one clearing turn against the tokens that clear removed.

    This is the whole policy, and it is one line: `rewritten` is the clearing
    turn's `cache_creation_input_tokens` - the prefix the invalidation forced
    the API to write again - and `removed` comes from the edit itself
    (`applied_edits[0].cleared_input_tokens`, or the free `count_tokens`
    subtraction). Crossing those two wires is the mistake this function exists
    to make unspellable.

    The clearing turn's own `cache_read_input_tokens` is deliberately not netted
    off: those are the `tools` and `system` prefixes, which the clear never
    touched and which cost the same with or without it.

    Pure. Failure modes: `ValueError` if `removed` is negative or
    `base_usd_per_mtok` is negative; `TypeError` if `removed` is not an `int`
    (both raised by `Tradeoff`).
    """
    return Tradeoff(
        removed=removed,
        rewritten=clearing_turn.cache_creation_input_tokens,
        base_usd_per_mtok=base_usd_per_mtok,
    )


def render(tradeoff: Tradeoff) -> str:
    """Render a `Tradeoff` for a terminal. Pure; no trailing newline; cannot fail."""
    premium_multiplier = round(
        CACHE_WRITE_5M_MULTIPLIER - CACHE_READ_MULTIPLIER, 2
    )
    turns = tradeoff.payback_turns
    turns_text = "never (the clear removed nothing)" if math.isinf(turns) else (
        f"{turns} turns"
    )
    return "\n".join(
        [
            "The clearing / caching trade",
            f"  base input rate       ${tradeoff.base_usd_per_mtok:.2f}/MTok",
            "",
            f"  removed               {tradeoff.removed} tokens "
            "(the clear deleted these from the message list)",
            f"  rewritten             {tradeoff.rewritten} tokens "
            "(cache_creation_input_tokens on the clearing turn)",
            "",
            f"  invalidation cost     "
            f"${tradeoff.invalidation_cost_usd:.{USD_PRECISION}f} "
            f"(once: {premium_multiplier}x base over re-reading them)",
            f"  saving per later turn "
            f"${tradeoff.saving_per_future_turn_usd:.{USD_PRECISION}f} "
            f"({CACHE_READ_MULTIPLIER}x base, every turn from here)",
            f"  payback               {turns_text}",
        ]
    )
