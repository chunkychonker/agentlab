"""What one cycle, and one night, produced.

Pure: no I/O.
"""

from __future__ import annotations

from dataclasses import dataclass

from .ports import RECOVERIES, Recovery
from .result import CYCLE_RESULTS, CycleResult, UsageLimited


@dataclass(frozen=True)
class CycleReport:
    """Cycle number ``cycle`` (1-based) ended in ``result``, and recovery did ``recovery``.

    Raises ``TypeError``/``ValueError`` for a non-positive or non-int
    ``cycle``, or a ``result``/``recovery`` outside its sum type.
    """

    cycle: int
    result: CycleResult
    recovery: Recovery

    def __post_init__(self) -> None:
        if isinstance(self.cycle, bool) or not isinstance(self.cycle, int):
            raise TypeError(f"cycle must be an int, got {type(self.cycle).__name__}")
        if self.cycle < 1:
            raise ValueError(f"cycle must be >= 1, got {self.cycle}")
        if not isinstance(self.result, CYCLE_RESULTS):
            raise TypeError(f"result must be a CycleResult, got {type(self.result).__name__}")
        if not isinstance(self.recovery, RECOVERIES):
            raise TypeError(f"recovery must be a Recovery, got {type(self.recovery).__name__}")


@dataclass(frozen=True)
class NightResult:
    """The cycles that ran, in order.

    Raises ``ValueError`` if ``reports`` is empty, is not numbered 1, 2, ...,
    or has a ``UsageLimited`` cycle anywhere but last (a limit ends the
    night). Raises ``TypeError`` if it is not a tuple of ``CycleReport``.
    """

    reports: tuple[CycleReport, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.reports, tuple) or not all(
            isinstance(r, CycleReport) for r in self.reports
        ):
            raise TypeError("reports must be a tuple of CycleReport")
        if not self.reports:
            raise ValueError("a night runs at least one cycle")
        numbers = [r.cycle for r in self.reports]
        if numbers != list(range(1, len(numbers) + 1)):
            raise ValueError(f"cycles must be numbered 1..n in order, got {numbers}")
        if any(isinstance(r.result, UsageLimited) for r in self.reports[:-1]):
            raise ValueError("only the last cycle can be UsageLimited")

    @property
    def usage_limited(self) -> bool:
        """True if the night stopped early on a usage limit."""
        return isinstance(self.reports[-1].result, UsageLimited)
