"""Runs a night of gated cycles and keeps every cycle's leftovers off main.

Public API::

    from nightloop.cycle import (
        run_night, NightResult, CycleReport, RunId,
        Shipped, NotShipped, PhaseFailed, ReviewRefused, UsageLimited,
        Workspace, Shipper, Clean, Snapshotted, RecoveryError,
        GitWorkspace, FakeShipper, usage_limit_reset,
    )

``run_night`` takes every effect as a parameter. Production wiring is
``SubprocessRunner()``, ``nightloop.gates.evaluate``,
``GitWorkspace(repo, now=time.time)``, a shipper, ``time.time`` and a sink
that appends each line to a file. The real shipper is
``nightloop.ship.GitHubShipper`` (milestone 5).
"""

from __future__ import annotations

from .fake_shipper import FakeShipper, FakeShipperExhausted
from .git_workspace import GitWorkspace
from .night import run_night
from .ports import Clean, Recovery, RecoveryError, Shipper, Snapshotted, Workspace
from .report import CycleReport, NightResult
from .result import CycleResult, NotShipped, PhaseFailed, ReviewRefused, Shipped, ShipOutcome, UsageLimited
from .run_id import RunId
from .usage_limit import UNKNOWN_RESET, usage_limit_reset

__all__ = [
    "Clean",
    "CycleReport",
    "CycleResult",
    "FakeShipper",
    "FakeShipperExhausted",
    "GitWorkspace",
    "NightResult",
    "NotShipped",
    "PhaseFailed",
    "Recovery",
    "RecoveryError",
    "ReviewRefused",
    "RunId",
    "ShipOutcome",
    "Shipped",
    "Shipper",
    "Snapshotted",
    "UNKNOWN_RESET",
    "UsageLimited",
    "Workspace",
    "run_night",
    "usage_limit_reset",
]
