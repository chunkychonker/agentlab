"""Reads GitHub's ``mergeable`` value as the one decision the shipper acts on.

Pure: no I/O. As bash's auto-merge gate: only ``MERGEABLE`` proceeds, only
``UNKNOWN`` (GitHub still computing) is worth waiting for, and anything else,
including a value this code has never seen, is treated as not mergeable.
"""

from __future__ import annotations

from enum import Enum

READY_VALUE = "MERGEABLE"
WAIT_VALUE = "UNKNOWN"


class Mergeable(Enum):
    """What to do with one ``mergeable`` reading."""

    READY = "ready"
    WAIT = "wait"
    BLOCKED = "blocked"


def decide(value: str) -> Mergeable:
    """``READY`` for exactly ``MERGEABLE``, ``WAIT`` for exactly ``UNKNOWN``, else ``BLOCKED``.

    Matching is exact (case and whitespace included): the edge strips gh's
    trailing newline, and nothing else is guessed. Raises ``TypeError`` if
    ``value`` is not a str.
    """
    if not isinstance(value, str):
        raise TypeError(f"mergeable value must be a str, got {type(value).__name__}")
    if value == READY_VALUE:
        return Mergeable.READY
    if value == WAIT_VALUE:
        return Mergeable.WAIT
    return Mergeable.BLOCKED
