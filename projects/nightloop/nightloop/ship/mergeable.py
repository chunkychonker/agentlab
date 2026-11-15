"""Reads GitHub's ``mergeable`` value for a PR, waiting while GitHub is still computing it.

Pure: no I/O; the read and the sleep are passed in. A port of the poll in
agentlab's ``run.sh``. The manual for ``gh pr view --json`` does not list
the field's values; ``MERGEABLE``, ``CONFLICTING`` and ``UNKNOWN`` are the
ones bash relies on. Any other string is ``OTHER``, and only ``MERGEABLE``
ever ships (fail closed).
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Callable, Final

ATTEMPTS: Final[int] = 5
DELAY_S: Final[float] = 2.0
_SHOWN_CHARS = 40


class Mergeable(Enum):
    MERGEABLE = "MERGEABLE"
    CONFLICTING = "CONFLICTING"
    UNKNOWN = "UNKNOWN"
    OTHER = "OTHER"


@dataclass(frozen=True)
class Polled:
    """The last value read: its class, and the raw text shortened to one line for a reason."""

    state: Mergeable
    shown: str


def classify(raw: str) -> Mergeable:
    """The ``Mergeable`` for ``raw`` (surrounding whitespace ignored, case-sensitive); ``OTHER`` if unknown."""
    text = raw.strip()
    for state in (Mergeable.MERGEABLE, Mergeable.CONFLICTING, Mergeable.UNKNOWN):
        if text == state.value:
            return state
    return Mergeable.OTHER


def poll(
    read: Callable[[], str],
    sleep: Callable[[float], None],
    *,
    attempts: int = ATTEMPTS,
    delay_s: float = DELAY_S,
) -> Polled:
    """Call ``read`` until it gives something other than ``UNKNOWN``, at most ``attempts`` times.

    ``sleep(delay_s)`` runs between reads, only after an ``UNKNOWN`` and
    only if another read follows, so ``UNKNOWN`` on every attempt sleeps
    ``attempts - 1`` times. Whatever ``read`` raises propagates (no retry).
    Raises ``ValueError`` if ``attempts`` < 1.
    """
    if isinstance(attempts, bool) or not isinstance(attempts, int) or attempts < 1:
        raise ValueError(f"attempts must be a positive int, got {attempts!r}")
    raw = ""
    for attempt in range(attempts):
        if attempt > 0:
            sleep(delay_s)
        raw = read()
        if classify(raw) is not Mergeable.UNKNOWN:
            break
    return Polled(state=classify(raw), shown=_shown(raw))


def _shown(raw: str) -> str:
    text = " ".join(raw.split())[:_SHOWN_CHARS]
    return text if text else "<empty>"
