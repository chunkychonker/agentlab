"""Runs one phase through an injected runner, decides its status, and gates it.

Pure in itself: every effect (the clock, the CLI, the gate's filesystem
reads, the metrics write) arrives as a parameter, so tests drive it with a
fixed clock, a ``FakeRunner``, the real gate on a temp repo, and a list as
the sink.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from ..gates.freshness import validate_floor
from ..gates.outcome import GateResult
from ..model import Postcondition
from .metrics import PhaseRecord, to_json_line
from .request import PhaseRequest
from .result import CliOutputError, CliResult, parse_cli_json
from .runner import RawRun, Runner
from .status import GATED, PhaseStatus

# The freshness floor is the phase's start second minus this. File mtimes
# can lag time.time() by a few ms on coarse-clock filesystems, so a floor
# taken right at a second boundary could call a file written in that same
# instant STALE. Cost: an artifact written up to 1s before the phase started
# counts as fresh (decisions log, 2026-11-10).
FLOOR_SLACK_S = 1

# The gate's signature: (postcondition, repo, phase_started, verdict_path).
# ``nightloop.gates.evaluate`` is the production gate.
Gate = Callable[[Postcondition, Path, int, Path], GateResult]


@dataclass(frozen=True)
class PhaseOutcome:
    """The result of ``run_phase``: the status, the gate result if it ran, and the record.

    ``gate`` is set exactly for ``GATED_PASS`` / ``GATE_FAILED``, and the
    status is ``GATED_PASS`` exactly when ``gate.advance``. ``floor`` is the
    freshness floor the gate was given (or would have been).

    Raises ``ValueError`` if those fields disagree with each other or with
    ``record``.
    """

    status: PhaseStatus
    gate: GateResult | None
    record: PhaseRecord
    floor: int

    def __post_init__(self) -> None:
        if self.record.outcome is not self.status:
            raise ValueError(f"record.outcome {self.record.outcome} != status {self.status}")
        if (self.gate is not None) != (self.status in GATED):
            raise ValueError(f"gate must be set exactly when the gate ran; status {self.status}")
        if self.gate is not None:
            if self.gate.advance != (self.status is PhaseStatus.GATED_PASS):
                raise ValueError(f"status {self.status} disagrees with gate.advance")
            if self.record.postcondition_state != self.gate.outcome.name:
                raise ValueError("record.postcondition_state disagrees with the gate outcome")


def run_phase(
    req: PhaseRequest,
    runner: Runner,
    *,
    now: Callable[[], float],
    gate: Gate,
    repo: Path,
    verdict_path: Path,
    metrics_sink: Callable[[str], None],
) -> PhaseOutcome:
    """Run ``req`` once, gate it if the CLI succeeded, and emit one metrics line.

    Order: read ``now()`` once and take the floor ``int(now()) -
    FLOOR_SLACK_S``; run the phase; decide the status; call ``gate`` only if
    the run did not time out, exited 0, printed one CLI result object, and
    did not report ``is_error: true``; pass one line to ``metrics_sink``;
    return. Status precedence: TIMED_OUT, then CLI_ERROR (non-zero exit),
    then UNREADABLE, then CLI_ERROR (``is_error``), then the gate.

    Failure modes:

    - ``ValueError`` before anything runs if the floor is invalid (``now()``
      before epoch second ``FLOOR_SLACK_S``); ``TypeError`` if ``now()`` does
      not return a number. Nothing is run and no line is emitted.
    - Anything ``runner.run`` raises propagates, with no line emitted: the
      phase never started, so there is no run to record.
    - Unparseable stdout is not raised: it is the UNREADABLE status and the
      record's ``parse_error: true``.
    - Anything ``gate`` raises propagates with no line emitted (the stock
      gate raises only for a programming error).
    - Anything ``metrics_sink`` raises propagates. The status was already
      decided, but the caller does not receive it.
    """
    floor = _floor(now())
    raw = runner.run(req)
    cli = _parse(raw.stdout)
    status, gate_result = _decide(raw, cli, req, gate, repo, floor, verdict_path)
    record = PhaseRecord(
        phase=req.name,
        model=req.phase.model,
        rc=raw.returncode,
        outcome=status,
        postcondition_state=None if gate_result is None else gate_result.outcome.name,
        cli=cli,
    )
    outcome = PhaseOutcome(status=status, gate=gate_result, record=record, floor=floor)
    metrics_sink(to_json_line(record))
    return outcome


def _floor(started: float) -> int:
    """The freshness floor for a phase that starts at ``started``; raises as ``run_phase`` says."""
    if isinstance(started, bool) or not isinstance(started, (int, float)):
        raise TypeError(f"now() must return a number, got {type(started).__name__}")
    floor = int(started) - FLOOR_SLACK_S
    validate_floor(floor)
    return floor


def _parse(stdout: str) -> CliResult | None:
    """The parsed CLI result, or ``None`` when stdout is not one (recorded as parse_error)."""
    try:
        return parse_cli_json(stdout)
    except CliOutputError:
        return None


def _decide(
    raw: RawRun,
    cli: CliResult | None,
    req: PhaseRequest,
    gate: Gate,
    repo: Path,
    floor: int,
    verdict_path: Path,
) -> tuple[PhaseStatus, GateResult | None]:
    if raw.timed_out:
        return PhaseStatus.TIMED_OUT, None
    if raw.returncode != 0:
        return PhaseStatus.CLI_ERROR, None
    if cli is None:
        return PhaseStatus.UNREADABLE, None
    if cli.is_error is True:
        return PhaseStatus.CLI_ERROR, None
    result = gate(req.phase.postcondition, repo, floor, verdict_path)
    if not isinstance(result, GateResult):
        raise TypeError(f"gate must return a GateResult, got {type(result).__name__}")
    status = PhaseStatus.GATED_PASS if result.advance else PhaseStatus.GATE_FAILED
    return status, result
