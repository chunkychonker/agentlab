"""Decides, from one ``mergeable`` value, whether a PR is clean, still being computed, or refused.

Pure: no I/O. The same rule as bash's auto-merge gate: only ``MERGEABLE``
passes; ``UNKNOWN`` (GitHub is still computing) is retried a bounded number
of times; every other value, including one this code has never seen, is a
refusal and the PR is left open for a human.
"""

from __future__ import annotations

from enum import Enum
from typing import Final

MERGEABLE_VALUE: Final[str] = "MERGEABLE"
UNKNOWN_VALUE: Final[str] = "UNKNOWN"
# As bash: up to 5 reads, 2s apart.
MAX_ATTEMPTS: Final[int] = 5
RETRY_DELAY_S: Final[float] = 2.0


class MergeCheck(Enum):
    MERGEABLE = "MERGEABLE"
    RETRY = "RETRY"
    REFUSED = "REFUSED"


def decide(value: str, attempt: int, max_attempts: int) -> MergeCheck:
    """What to do after read number ``attempt`` (1-based) of ``max_attempts`` returned ``value``.

    ``RETRY`` only for ``UNKNOWN`` before the last attempt. Raises
    ``ValueError`` unless ``1 <= attempt <= max_attempts``.
    """
    if isinstance(attempt, bool) or isinstance(max_attempts, bool) or not 1 <= attempt <= max_attempts:
        raise ValueError(f"attempt must be in 1..{max_attempts}, got {attempt!r}")
    if value == MERGEABLE_VALUE:
        return MergeCheck.MERGEABLE
    if value == UNKNOWN_VALUE and attempt < max_attempts:
        return MergeCheck.RETRY
    return MergeCheck.REFUSED
