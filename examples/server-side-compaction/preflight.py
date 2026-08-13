"""Decides whether a transcript is big enough for compaction to fire.

Pure: two numbers in, a go/no-go out. No I/O, no `anthropic` import - the
counting itself happens at the edge (`main.count_transcript_tokens`) and hands
the result here.

Why this exists at all: `/v1/messages/count_tokens` "applies existing compaction
blocks but does not trigger new compactions", so there is no $0 preview of what
compaction *would* save. The one thing counting still buys you for free is the
refusal to pay for a `messages.create` call whose trigger cannot possibly fire.

See the research note this came from:
    research/2026-08-12-server-side-compaction.md
"""

from __future__ import annotations

import dataclasses


@dataclasses.dataclass(frozen=True)
class Preflight:
    """A counted transcript measured against a policy's trigger.

    Fields:
      counted_tokens  what the free token-counting endpoint reported for the
                      request as it stands, before any compaction.
      trigger_tokens  the policy's `trigger.value`.

    Failure modes: ``ValueError`` if ``counted_tokens`` is negative or
    ``trigger_tokens`` is below 1. Both are raised at construction; the
    properties cannot fail.
    """

    counted_tokens: int
    trigger_tokens: int

    def __post_init__(self) -> None:
        if self.counted_tokens < 0:
            raise ValueError(f"counted_tokens must be >= 0, got {self.counted_tokens}")
        if self.trigger_tokens < 1:
            raise ValueError(f"trigger_tokens must be >= 1, got {self.trigger_tokens}")

    @property
    def will_fire(self) -> bool:
        """True when the transcript is strictly larger than the trigger.

        Strictly, not `>=`. Neither docs page states what happens at exactly the
        threshold, and this decides whether to spend money, so the ambiguous case
        is the no-go case.
        """
        return self.counted_tokens > self.trigger_tokens

    @property
    def shortfall(self) -> int:
        """How many more input tokens the transcript needs before it will fire.

        0 exactly when `will_fire` is true. At `counted_tokens == trigger_tokens`
        this is 1, not 0 - the transcript is one token short of *exceeding* the
        threshold, and reporting 0 there would read as "ready".
        """
        if self.will_fire:
            return 0
        return self.trigger_tokens - self.counted_tokens + 1
