"""Turns two turns' cache counters into the dollars a cache hit saved.

Pure arithmetic and one renderer: no `anthropic` import, no I/O, no env reads,
no clock. The base input rate is *not* here - it is model-specific and changes,
so it lives in one constant at the entry point (`main.BASE_USD_PER_MTOK`) and is
passed in. A pricing table baked into a library function is a stale pricing
table (same reasoning as `server-side-compaction/cost.py`).

Layer 2 (spec), asserted in `test_report.py`:

  - turn 1 writes the prefix, turn 2 reads it back. So `written` comes from
    turn 1's `cache_creation_input_tokens` and `read` from turn 2's
    `cache_read_input_tokens`; crossing those two wires is the mistake this
    module exists to make unspellable.
  - A cached read costs `CACHE_READ_MULTIPLIER` of the base rate; the 5-minute
    write costs `CACHE_WRITE_5M_MULTIPLIER`, i.e. a 25% premium paid once.
  - The net saving is what the read saved minus that premium.

See the research note this came from:
    research/2026-08-29-prompt-caching-tool-loop.md
"""

from __future__ import annotations

import dataclasses

# Multipliers on the model's base *input* rate (prompt-caching docs, 2026-08-29).
# The 1-hour TTL's 2x write multiplier is out of scope - see the README.
CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10

# Anthropic prices per million tokens; this is the divisor that word implies.
TOKENS_PER_MTOK = 1_000_000

# Micro-dollars. Not cents: a two-turn demo saves a few thousandths of a dollar
# - the measured run saves $0.004767 - and rounding that to 0.00 would make the
# one number this example exists to show read as zero.
USD_PRECISION = 6

# The read/write ratio is a diagnostic, not money; three places is plenty.
FRACTION_PRECISION = 3


@dataclasses.dataclass(frozen=True)
class TurnUsage:
    """The three input counters one `messages.create` response reports.

    They partition the prompt - `input_tokens` is only the remainder *after* the
    last cache breakpoint, not the whole thing:

        total prompt = input_tokens + cache_creation_input_tokens
                                    + cache_read_input_tokens

    A `TurnUsage` that exists is a usable one: every field is a non-negative
    `int`, so nothing downstream re-checks.

    Failure modes: `TypeError` if a field is not an `int` (the SDK types these
    as optional, and `None` reaching the arithmetic would read as a free run);
    `ValueError` if a field is negative.
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
class Saving:
    """What one two-turn run's cache hit was worth, in dollars.

    Only three fields are stored - the two token counts and the rate - and every
    dollar figure is derived from them. The arithmetic relations between the
    figures (`net = saved - premium`, `saved = uncached - cached`) are then true
    by construction rather than by a constructor remembering to keep eight
    fields consistent.

    Failure modes: `ValueError` if a token count or the rate is negative;
    `TypeError` if a token count is not an `int`.
    """

    written: int
    read: int
    base_usd_per_mtok: float

    def __post_init__(self) -> None:
        for name in ("written", "read"):
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
    def read_fraction(self) -> float:
        """How much of turn 1's write turn 2 actually read back.

        `max(written, 1)` rather than a guard: a run that wrote nothing has a
        fraction of 0.0, which is a true statement about a cache that never
        formed, not a `ZeroDivisionError`.
        """
        return round(self.read / max(self.written, 1), FRACTION_PRECISION)

    @property
    def read_cost_usd(self) -> float:
        """What the cached read cost: 0.10x the base rate."""
        return self._usd(self.read * CACHE_READ_MULTIPLIER)

    @property
    def read_cost_if_uncached_usd(self) -> float:
        """What those same tokens would have cost at the full base rate."""
        return self._usd(self.read)

    @property
    def saved_on_read_usd(self) -> float:
        """The difference the two lines above make."""
        return round(self.read_cost_if_uncached_usd - self.read_cost_usd, USD_PRECISION)

    @property
    def write_premium_usd(self) -> float:
        """The 25% surcharge on the write, paid once whether or not it is read."""
        return self._usd(self.written * (CACHE_WRITE_5M_MULTIPLIER - 1))

    @property
    def net_saving_usd(self) -> float:
        """Saving minus premium. Negative when the write was never read enough."""
        return round(self.saved_on_read_usd - self.write_premium_usd, USD_PRECISION)

    def _usd(self, tokens: float) -> float:
        return round(tokens * self.base_usd_per_mtok / TOKENS_PER_MTOK, USD_PRECISION)


def summarize(turn1: TurnUsage, turn2: TurnUsage, *, base_usd_per_mtok: float) -> Saving:
    """Price the cache hit between a first turn and a second one.

    This is the whole policy, and it is one line: the number that matters is
    what turn 1 *wrote* against what turn 2 *read*. Turn 2's own creation count
    is the delta it wrote for turn 3 and is deliberately not netted off here -
    a two-turn run never gets to read it back.

    Pure. Failure modes: `ValueError` if `base_usd_per_mtok` is negative
    (raised by `Saving`).
    """
    return Saving(
        written=turn1.cache_creation_input_tokens,
        read=turn2.cache_read_input_tokens,
        base_usd_per_mtok=base_usd_per_mtok,
    )


def render(saving: Saving) -> str:
    """Render a `Saving` for a terminal. Pure; no trailing newline; cannot fail."""
    read_multiplier = CACHE_READ_MULTIPLIER
    write_premium_multiplier = round(CACHE_WRITE_5M_MULTIPLIER - 1, 2)
    return "\n".join(
        [
            "Prompt caching across a two-turn tool loop",
            f"  base input rate      ${saving.base_usd_per_mtok:.2f}/MTok",
            "",
            f"  turn 1 wrote         {saving.written} tokens "
            "(cache_creation_input_tokens)",
            f"  turn 2 read          {saving.read} tokens "
            "(cache_read_input_tokens)",
            f"  read / written       {saving.read_fraction}",
            "",
            f"  that read cost       ${saving.read_cost_usd:.{USD_PRECISION}f} "
            f"({read_multiplier}x base)",
            f"  uncached it would    ${saving.read_cost_if_uncached_usd:.{USD_PRECISION}f} "
            "(1.0x base)",
            f"  saved on the read    ${saving.saved_on_read_usd:.{USD_PRECISION}f}",
            f"  write premium        ${saving.write_premium_usd:.{USD_PRECISION}f} "
            f"({write_premium_multiplier}x base, paid once)",
            f"  net saving           ${saving.net_saving_usd:.{USD_PRECISION}f}",
        ]
    )
