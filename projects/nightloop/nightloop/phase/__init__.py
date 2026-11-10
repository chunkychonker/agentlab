"""Runs one nightloop phase as a headless ``claude -p`` call behind an interface.

Public API::

    from nightloop.phase import (
        run_phase, PhaseRequest, PhaseOutcome, PhaseStatus,
        Runner, RawRun, SubprocessRunner, FakeRunner,
    )

``run_phase`` takes the runner, clock, gate and metrics sink as parameters.
Production wiring is ``SubprocessRunner()``, ``time.time``,
``nightloop.gates.evaluate`` and a sink that appends the line to a file.
"""

from __future__ import annotations

from .execute import FLOOR_SLACK_S, PhaseOutcome, run_phase
from .fake_runner import FakeRunner, FakeRunnerExhausted, cli_stdout, cli_success
from .metrics import PhaseRecord
from .request import PhaseRequest
from .runner import RawRun, Runner
from .status import PhaseStatus
from .subprocess_runner import SubprocessRunner

__all__ = [
    "FLOOR_SLACK_S",
    "FakeRunner",
    "FakeRunnerExhausted",
    "PhaseOutcome",
    "PhaseRecord",
    "PhaseRequest",
    "PhaseStatus",
    "RawRun",
    "Runner",
    "SubprocessRunner",
    "cli_stdout",
    "cli_success",
    "run_phase",
]
