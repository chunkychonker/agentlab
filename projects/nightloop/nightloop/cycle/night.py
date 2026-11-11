"""Runs one night: up to ``cfg.cycles`` cycles, stopping early on a usage limit.

Pure in itself, like ``cycle.py``: every effect arrives as a parameter.
Deciding whether to start at all (the run window) is the CLI's job
(milestone 6), not this module's.
"""

from __future__ import annotations

from pathlib import Path
from typing import Callable

from ..model import Config
from ..phase.execute import Gate
from ..phase.request import PhaseRequest
from ..phase.runner import Runner
from .cycle import PHASE_ORDER, run_cycle
from .ports import Shipper, Workspace
from .prompts import phase_prompt
from .report import CycleReport, NightResult
from .result import UsageLimited
from .run_id import RunId


def run_night(
    cfg: Config,
    *,
    run_id: RunId,
    repo: Path,
    verdict_path: Path,
    timeout_s: int,
    runner: Runner,
    gate: Gate,
    workspace: Workspace,
    shipper: Shipper,
    now: Callable[[], float],
    metrics_sink: Callable[[str], None],
) -> NightResult:
    """Run cycles 1..``cfg.cycles`` and return one ``CycleReport`` per cycle that ran.

    ``repo`` is the absolute checkout path (every phase's cwd).
    ``verdict_path`` is the review verdict file, relative to ``repo`` or
    absolute; it is both what ``clear_handoff`` deletes and what the review
    gate reads. ``timeout_s`` is each phase's wall-clock limit. Production
    wiring: ``SubprocessRunner()``, ``nightloop.gates.evaluate``,
    ``GitWorkspace(repo, now=time.time)``, a real shipper, ``time.time``.

    A cycle that fails (any phase, the review verdict, the shipper) does not
    stop the night; the next cycle starts on the clean main that recovery
    left. The night stops early after a cycle that is ``UsageLimited``.

    Failure modes, all checked before any cycle starts unless noted:

    - ``TypeError`` if ``cfg``, ``run_id`` or ``verdict_path`` has the wrong
      type; ``TypeError``/``ValueError`` from ``PhaseRequest`` if ``repo`` is
      not an absolute ``Path`` or ``timeout_s`` is not a positive int.
    - During a cycle: ``RecoveryError`` ends the night and propagates;
      later cycles do not start. Every other exception from a cycle
      propagates after that cycle's recovery (see ``run_cycle``). The
      metrics lines already emitted are the record of the earlier cycles.
    """
    if not isinstance(cfg, Config):
        raise TypeError(f"cfg must be a Config, got {type(cfg).__name__}")
    if not isinstance(run_id, RunId):
        raise TypeError(f"run_id must be a RunId, got {type(run_id).__name__}")
    if not isinstance(verdict_path, Path):
        raise TypeError(f"verdict_path must be a Path, got {type(verdict_path).__name__}")
    requests = tuple(
        PhaseRequest(
            name=name,
            phase=getattr(cfg, name),
            prompt=phase_prompt(name, repo=repo, verdict_path=verdict_path),
            cwd=repo,
            timeout_s=timeout_s,
        )
        for name in PHASE_ORDER
    )
    reports: list[CycleReport] = []
    for cycle in range(1, cfg.cycles + 1):
        report = run_cycle(
            cycle,
            requests,
            run_id=run_id,
            runner=runner,
            gate=gate,
            workspace=workspace,
            shipper=shipper,
            now=now,
            repo=repo,
            verdict_path=verdict_path,
            metrics_sink=metrics_sink,
        )
        reports.append(report)
        if isinstance(report.result, UsageLimited):
            break
    return NightResult(tuple(reports))
