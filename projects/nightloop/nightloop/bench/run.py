"""Runs the benchmark matrix: every fixture, ``trials`` times, through the real review phase.

Pure in itself, like ``run_phase``: the runner, the workspace, the clock and
the gate arrive as parameters. The request each trial sends is the one
``phase_requests`` builds for a night's review phase, so the benchmark
measures the production invocation (prompt, argv, tools, model).
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable, Sequence

from ..cycle.night import phase_requests
from ..gates.outcome import Verdict
from ..model import Config
from ..phase.execute import Gate, run_phase
from ..phase.request import PhaseRequest
from ..phase.runner import Runner
from .classify import Outcome, classify
from .fixture import Fixture, require_both_kinds
from .ports import TrialWorkspace
from .report import TrialResult

REVIEW_PHASE = "review"


def review_request(cfg: Config, *, repo: Path, verdict_path: Path, timeout_s: int) -> PhaseRequest:
    """The review phase's ``PhaseRequest`` exactly as a night in ``repo`` would build it.

    Raises what ``phase_requests`` raises (a relative ``repo``, a
    non-positive ``timeout_s``).
    """
    requests = phase_requests(cfg, repo=repo, verdict_path=verdict_path, timeout_s=timeout_s)
    (review,) = [r for r in requests if r.name == REVIEW_PHASE]
    return review


def run_bench(
    fixtures: Sequence[Fixture],
    cfg: Config,
    *,
    runner: Runner,
    workspace: TrialWorkspace,
    trials: int,
    timeout_s: int,
    verdict_path: Path,
    now: Callable[[], float],
    gate: Gate,
    on_trial: Callable[[TrialResult], None],
) -> tuple[TrialResult, ...]:
    """Run each fixture ``trials`` times, in the given order, each trial in its own fresh repo.

    A trial whose phase did not reach the gate (CLI error, timeout,
    unreadable output) or whose verdict is MISSING/MALFORMED is a
    ``TrialResult`` with outcome UNGRADED and a ``detail`` saying why; the
    run goes on. ``on_trial`` is called after each trial (progress).

    Failure modes: ``MatrixError`` before anything runs if the fixtures
    lack a seeded or a clean one (or repeat a slug); ``ValueError`` if
    ``trials`` is not an int >= 1. ``OSError`` from the workspace or the
    runner (``git`` or ``claude`` cannot start) propagates after that
    trial's repo is removed; the trials already run are lost to the caller,
    by design, since a partial matrix is not a benchmark. Whatever
    ``run_phase`` or ``on_trial`` raises propagates the same way.
    """
    require_both_kinds(fixtures)
    if isinstance(trials, bool) or not isinstance(trials, int) or trials < 1:
        raise ValueError(f"trials must be an int >= 1, got {trials!r}")
    results: list[TrialResult] = []
    for fixture in fixtures:
        for trial in range(1, trials + 1):
            with workspace.materialize(fixture) as repo:
                result = _one_trial(
                    fixture, trial, repo, cfg,
                    runner=runner, workspace=workspace, timeout_s=timeout_s,
                    verdict_path=verdict_path, now=now, gate=gate,
                )
            results.append(result)
            on_trial(result)
    return tuple(results)


def _one_trial(
    fixture: Fixture,
    trial: int,
    repo: Path,
    cfg: Config,
    *,
    runner: Runner,
    workspace: TrialWorkspace,
    timeout_s: int,
    verdict_path: Path,
    now: Callable[[], float],
    gate: Gate,
) -> TrialResult:
    req = review_request(cfg, repo=repo, verdict_path=verdict_path, timeout_s=timeout_s)
    lines: list[str] = []  # the phase's metrics line; the benchmark keeps no metrics file
    phase = run_phase(req, runner, now=now, gate=gate, repo=repo,
                      verdict_path=verdict_path, metrics_sink=lines.append)
    if phase.gate is None:
        outcome = classify(fixture.kind, None, "", fixture.patterns)
        detail = f"phase {phase.status.name} (rc {phase.record.rc})"
        return TrialResult(fixture.slug, fixture.kind, trial, outcome, detail)
    verdict = phase.gate.outcome
    if not isinstance(verdict, Verdict):
        raise TypeError(f"the review gate must give a Verdict, got {type(verdict).__name__}")
    findings = workspace.read_verdict(repo, verdict_path) if verdict in (Verdict.PASS, Verdict.FAIL) else ""
    outcome = classify(fixture.kind, verdict, findings, fixture.patterns)
    detail = f"verdict {verdict.name}" if outcome is Outcome.UNGRADED else f"{phase.status.name}, verdict {verdict.name}"
    return TrialResult(fixture.slug, fixture.kind, trial, outcome, detail)

