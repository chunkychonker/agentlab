"""A scripted ``Shipper`` for tests: never pushes, never calls ``gh``.

Pure apart from what a scripted step chooses to do (a step may run git in a
temp repo to stand in for the real shipper's commit and push).
"""

from __future__ import annotations

from typing import Callable, Sequence, Union

from .result import SHIP_OUTCOMES, ShipOutcome
from .run_id import RunId

# A step is the outcome to return, or a function that does the shipping work
# and then returns one.
ShipStep = Union[ShipOutcome, Callable[[RunId, int], ShipOutcome]]


class FakeShipperExhausted(RuntimeError):
    """``FakeShipper.ship`` was called more times than it had steps."""


class FakeShipper:
    """Returns its scripted steps in order and records every ``(run_id, cycle)`` call.

    Raises ``FakeShipperExhausted`` from ``ship`` once the steps are used up,
    and ``TypeError`` if a step gives something other than ``Shipped`` or
    ``NotShipped``.
    """

    def __init__(self, steps: Sequence[ShipStep]) -> None:
        self._steps = list(steps)
        self.calls: list[tuple[RunId, int]] = []

    def ship(self, run_id: RunId, cycle: int) -> ShipOutcome:
        if len(self.calls) >= len(self._steps):
            raise FakeShipperExhausted(
                f"no step left for ship call {len(self.calls) + 1} (cycle {cycle}); "
                f"scripted {len(self._steps)}"
            )
        step = self._steps[len(self.calls)]
        self.calls.append((run_id, cycle))
        outcome = step if isinstance(step, SHIP_OUTCOMES) else step(run_id, cycle)
        if not isinstance(outcome, SHIP_OUTCOMES):
            raise TypeError(f"a FakeShipper step must give Shipped or NotShipped, got {type(outcome).__name__}")
        return outcome
