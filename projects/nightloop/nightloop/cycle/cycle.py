"""Runs one cycle: clear the handoff, run the gated phases, ship on PASS, then recover.

Pure in itself: the runner, gate, clock, workspace, shipper and metrics sink
all arrive as parameters (functional core, imperative shell). The order of
effects is fixed here, not left to the caller:

1. ``workspace.clear_handoff`` - a stale PASS can never authorise this cycle.
2. research, build, review through ``run_phase``; the first one that is not
   ``GATED_PASS`` ends the cycle.
3. ``shipper.ship`` only if review is ``GATED_PASS`` (the verdict parsed PASS).
4. ``workspace.recover`` exactly once, whatever happened above, including an
   exception.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..model import Postcondition
from ..phase.execute import Gate, PhaseOutcome, run_phase
from ..phase.request import PhaseRequest
from ..phase.runner import RawRun, Runner
from ..phase.status import PhaseStatus
from .metrics_tag import tagging_sink
from .ports import RecoveryError, Shipper, Workspace
from .recording import RecordingRunner
from .report import CycleReport
from .result import (
    SHIP_OUTCOMES,
    CycleResult,
    PhaseFailed,
    ReviewRefused,
    UsageLimited,
)
from .run_id import RunId
from .usage_limit import usage_limit_reset

# The order a cycle runs its phases in; review is last because its verdict
# is what authorises shipping.
PHASE_ORDER: tuple[str, ...] = ("research", "build", "review")


def run_cycle(
    cycle: int,
    requests: tuple[PhaseRequest, ...],
    *,
    run_id: RunId,
    runner: Runner,
    gate: Gate,
    workspace: Workspace,
    shipper: Shipper,
    now: Callable[[], float],
    repo: Path,
    verdict_path: Path,
    metrics_sink: Callable[[str], None],
) -> CycleReport:
    """Run cycle number ``cycle`` with ``requests`` (one per phase, in ``PHASE_ORDER``).

    Every phase run emits one metrics line to ``metrics_sink``, tagged with
    ``run_ts`` and ``cycle``; the cycle emits none of its own.

    Failure modes:

    - ``RecoveryError`` from ``workspace.recover`` propagates; the caller
      must stop the night.
    - Anything else raised along the way (``clear_handoff``, ``run_phase``,
      the gate, the sink, ``shipper.ship``) propagates too, but only after
      ``workspace.recover`` has run, so a crash does not strand work on
      main. If that recovery also fails, its ``RecoveryError`` is raised
      with the first exception as its ``__cause__``.
    - ``TypeError`` (after recovery) if the shipper returns something that
      is not ``Shipped``/``NotShipped``.
    """
    try:
        workspace.clear_handoff(verdict_path)
        result = _attempt(
            cycle,
            requests,
            run_id=run_id,
            runner=runner,
            gate=gate,
            shipper=shipper,
            now=now,
            repo=repo,
            verdict_path=verdict_path,
            metrics_sink=tagging_sink(metrics_sink, run_id, cycle),
        )
    except BaseException as primary:
        _recover_after(primary, workspace, run_id, cycle)
        raise
    recovery = workspace.recover(run_id, cycle)
    return CycleReport(cycle=cycle, result=result, recovery=recovery)


def _recover_after(primary: BaseException, workspace: Workspace, run_id: RunId, cycle: int) -> None:
    """Recover after ``primary`` was raised; a failed recovery is raised from ``primary``."""
    try:
        workspace.recover(run_id, cycle)
    except RecoveryError as failed:
        raise failed from primary


def _attempt(
    cycle: int,
    requests: tuple[PhaseRequest, ...],
    *,
    run_id: RunId,
    runner: Runner,
    gate: Gate,
    shipper: Shipper,
    now: Callable[[], float],
    repo: Path,
    verdict_path: Path,
    metrics_sink: Callable[[str], None],
) -> CycleResult:
    """The phases and the ship step; how the cycle ended, before recovery."""
    for req in requests:
        recorder = RecordingRunner(runner)
        outcome = run_phase(
            req,
            recorder,
            now=now,
            gate=gate,
            repo=repo,
            verdict_path=verdict_path,
            metrics_sink=metrics_sink,
        )
        if outcome.status is not PhaseStatus.GATED_PASS:
            if recorder.last is None:
                # run_phase returned, so it ran the phase through the recorder.
                raise RuntimeError(f"{req.name}: run_phase returned without running the phase")
            return _stopped(req.name, outcome, recorder.last)
    shipped = shipper.ship(run_id, cycle)
    if not isinstance(shipped, SHIP_OUTCOMES):
        raise TypeError(f"a Shipper must return Shipped or NotShipped, got {type(shipped).__name__}")
    return shipped


def _stopped(name: str, outcome: PhaseOutcome, raw: RawRun) -> CycleResult:
    """How the cycle ends when phase ``name`` did not pass its gate."""
    if outcome.status is PhaseStatus.CLI_ERROR:
        # Only a CLI error is scanned: a passing (or merely gate-failed)
        # phase whose note quotes the limit message must not end the night.
        reset = usage_limit_reset(_limit_text(outcome, raw))
        if reset is not None:
            return UsageLimited(phase=name, reset=reset)
    if outcome.gate is not None and outcome.gate.postcondition is Postcondition.REVIEW_VERDICT:
        return ReviewRefused(verdict=outcome.gate.outcome)
    return PhaseFailed(phase=name, status=outcome.status)


def _limit_text(outcome: PhaseOutcome, raw: RawRun) -> str:
    """The text a usage-limit message could be in: the CLI's result, else raw stdout, then stderr.

    When stdout parsed as the CLI's JSON, its ``result`` is scanned rather
    than the JSON itself, so the reset time is not followed by JSON syntax.
    """
    cli = outcome.record.cli
    out = raw.stdout if cli is None else cli.result
    return f"{out}\n{raw.stderr}"
