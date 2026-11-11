"""The closed set of ways one cycle can end, one frozen dataclass per case.

Pure: no I/O. ``CycleResult`` is a sum type rather than ``ok: bool`` plus a
nullable reason, so a result that both shipped and failed cannot be built.
Every case checks its own fields, so an inconsistent one (a refused review
whose verdict is PASS, a phase "failure" whose status is GATED_PASS) raises
at construction.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from ..gates.outcome import Verdict
from ..model import PHASE_POSTCONDITIONS, Postcondition
from ..phase.status import PhaseStatus


def _check_line(field: str, value: object) -> None:
    """Raise unless ``value`` is one non-empty line of text."""
    if not isinstance(value, str):
        raise TypeError(f"{field} must be a str, got {type(value).__name__}")
    if value.strip() == "" or "\n" in value or "\r" in value:
        raise ValueError(f"{field} must be one non-empty line, got {value!r}")


def _check_phase(phase: object) -> None:
    if not isinstance(phase, str):
        raise TypeError(f"phase must be a str, got {type(phase).__name__}")
    if phase not in PHASE_POSTCONDITIONS:
        raise ValueError(f"phase must be one of {', '.join(PHASE_POSTCONDITIONS)}, got {phase!r}")


@dataclass(frozen=True)
class Shipped:
    """Review passed and the shipper opened PR ``pr``.

    Raises ``TypeError`` unless ``pr`` is an int (not a bool) and
    ``ValueError`` unless it is positive.
    """

    pr: int

    def __post_init__(self) -> None:
        if isinstance(self.pr, bool) or not isinstance(self.pr, int):
            raise TypeError(f"pr must be an int, got {type(self.pr).__name__}")
        if self.pr <= 0:
            raise ValueError(f"pr must be > 0, got {self.pr}")


@dataclass(frozen=True)
class NotShipped:
    """Review passed but the shipper declined or failed; ``reason`` says why.

    The cycle's work is not lost: recovery snapshots it to a branch. Raises
    ``TypeError``/``ValueError`` unless ``reason`` is one non-empty line.
    """

    reason: str

    def __post_init__(self) -> None:
        _check_line("reason", self.reason)


@dataclass(frozen=True)
class PhaseFailed:
    """``phase`` ended with ``status`` and the cycle stopped there.

    Raises ``ValueError`` if ``status`` is ``GATED_PASS`` (that is not a
    failure) or if it is a refused review verdict, which is
    ``ReviewRefused``. Raises ``TypeError`` for wrong field types.
    """

    phase: str
    status: PhaseStatus

    def __post_init__(self) -> None:
        _check_phase(self.phase)
        if not isinstance(self.status, PhaseStatus):
            raise TypeError(f"status must be a PhaseStatus, got {type(self.status).__name__}")
        if self.status is PhaseStatus.GATED_PASS:
            raise ValueError(f"{self.phase} passed its gate; GATED_PASS is not a failure")
        verdict_gated = Postcondition.REVIEW_VERDICT in PHASE_POSTCONDITIONS[self.phase]
        if verdict_gated and self.status is PhaseStatus.GATE_FAILED:
            raise ValueError("a refused review verdict is ReviewRefused, not PhaseFailed")


@dataclass(frozen=True)
class ReviewRefused:
    """The review phase ran, and its verdict (FAIL, MISSING or MALFORMED) does not ship.

    Raises ``ValueError`` if ``verdict`` is ``PASS`` and ``TypeError`` if it
    is not a ``Verdict``.
    """

    verdict: Verdict

    def __post_init__(self) -> None:
        if not isinstance(self.verdict, Verdict):
            raise TypeError(f"verdict must be a Verdict, got {type(self.verdict).__name__}")
        if self.verdict is Verdict.PASS:
            raise ValueError("a PASS verdict is not a refusal")


@dataclass(frozen=True)
class UsageLimited:
    """``phase`` stopped on a Claude usage limit; ``reset`` is when it lifts, or ``"unknown"``.

    Ends the night: every later phase would hit the same limit. Raises
    ``TypeError``/``ValueError`` for an unknown phase or a ``reset`` that is
    not one non-empty line.
    """

    phase: str
    reset: str

    def __post_init__(self) -> None:
        _check_phase(self.phase)
        _check_line("reset", self.reset)


# What a Shipper may return.
ShipOutcome = Union[Shipped, NotShipped]
SHIP_OUTCOMES: tuple[type, ...] = (Shipped, NotShipped)

# How a cycle ends: exactly one of these.
CycleResult = Union[Shipped, NotShipped, PhaseFailed, ReviewRefused, UsageLimited]
CYCLE_RESULTS: tuple[type, ...] = (Shipped, NotShipped, PhaseFailed, ReviewRefused, UsageLimited)
