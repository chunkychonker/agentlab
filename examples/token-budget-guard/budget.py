"""Pure budget decision for the guarded tool loop: may this request be sent?

No I/O, no SDK import. The shell (agent.py) measures the pending request with
the token-counting endpoint and asks this module whether to send it.

See research/2026-10-16-token-budget-guard.md.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BudgetExceeded:
    """Why the loop stopped: the request for ``turn`` would have exceeded ``limit``.

    ``counted_tokens`` is the count_tokens estimate of the pending request's
    input. It excludes the output reserve, so the stop condition was
    ``counted_tokens + reserve > limit``.
    """

    turn: int
    counted_tokens: int
    limit: int


def check_budget(
    counted_tokens: int, limit: int, turn: int, reserve: int = 0
) -> BudgetExceeded | None:
    """Return ``BudgetExceeded`` iff ``counted_tokens + reserve > limit``, else None.

    The boundary is inclusive: ``counted_tokens + reserve == limit`` is allowed.
    ``reserve`` is headroom for the reply (the shell passes ``max_tokens``) so
    the fence covers worst-case input plus output.

    Raises:
        ValueError: if ``counted_tokens`` or ``reserve`` is negative, ``limit``
            is not positive, or ``turn`` is less than 1. Nothing is returned
            in that case; a bad argument is a caller bug, not a budget verdict.
    """
    if counted_tokens < 0:
        raise ValueError(f"counted_tokens must be >= 0, got {counted_tokens}")
    if reserve < 0:
        raise ValueError(f"reserve must be >= 0, got {reserve}")
    if limit <= 0:
        raise ValueError(f"limit must be > 0, got {limit}")
    if turn < 1:
        raise ValueError(f"turn must be >= 1, got {turn}")
    if counted_tokens + reserve > limit:
        return BudgetExceeded(turn=turn, counted_tokens=counted_tokens, limit=limit)
    return None
