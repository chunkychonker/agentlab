"""Classifies a set of artifact mtimes against a phase-start floor.

Pure: no I/O, no clock. A port of the decision half of ``artifact_freshness``
in agentlab's ``.pipeline/postcondition.sh``.

The floor is the moment the *phase* started, never the run: with two cycles a
run-start floor would let cycle 1's artifact satisfy cycle 2's gate. STALE is
distinct from MISSING because the 2026-09-01 failure was a ``research/`` full
of earlier nights' notes, not an empty one.
"""

from __future__ import annotations

from typing import Sequence

from .outcome import Freshness


def validate_floor(floor: object) -> None:
    """Raise ``ValueError`` unless ``floor`` is a non-negative ``int`` epoch second.

    ``bool`` is rejected even though it subclasses ``int``. A bad floor is the
    caller's bug, not data about the artifact, so it is never mapped to an
    outcome (decisions log, 2026-11-09).
    """
    if isinstance(floor, bool) or not isinstance(floor, int):
        raise ValueError(f"floor must be an int epoch second, got {type(floor).__name__}")
    if floor < 0:
        raise ValueError(f"floor must be >= 0, got {floor}")


def classify_freshness(mtimes: Sequence[float | None], floor: int) -> Freshness:
    """Classify the matching files' mtimes against ``floor`` (inclusive).

    ``mtimes`` has one entry per matching file; ``None`` marks a file that was
    found but whose mtime could not be read, which counts as found but never
    as fresh. Empty is ``MISSING``; any mtime ``>= floor`` is ``FRESH``;
    otherwise ``STALE``. The float mtime is compared as is, never truncated.

    Raises ``ValueError`` if ``floor`` is invalid (see ``validate_floor``).
    """
    validate_floor(floor)
    if not mtimes:
        return Freshness.MISSING
    if any(m is not None and m >= floor for m in mtimes):
        return Freshness.FRESH
    return Freshness.STALE
