"""The dry-run plan: what each phase of each cycle of a night would execute.

Pure: no I/O, no runner. The requests come from ``nightloop.cycle.night``'s
``phase_requests``, the same function ``run_night`` uses, and the argv from
``build_argv``, the same function ``SubprocessRunner`` runs. So the plan is
what a night would execute, not a description of it kept by hand.
"""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from pathlib import Path

from ..cycle.night import phase_requests
from ..model import Config
from ..phase.argv import build_argv
from .window import Disposition, Run


@dataclass(frozen=True)
class PlannedPhase:
    """One phase of cycle ``cycle``: its name, model, gate, timeout, cwd and full argv."""

    cycle: int
    name: str
    model: str
    gate: str
    timeout_s: int
    cwd: Path
    argv: tuple[str, ...]


def plan_night(
    cfg: Config, *, repo: Path, verdict_path: Path, timeout_s: int
) -> tuple[PlannedPhase, ...]:
    """Every phase of cycles 1..``cfg.cycles``, in the order a night would run them.

    A cycle that fails stops early at run time, so a real night runs at most
    this. Raises what ``phase_requests`` raises: ``TypeError``/``ValueError``
    for a non-``Config``, a relative ``repo``, a non-``Path`` ``verdict_path``
    or a non-positive ``timeout_s``.
    """
    requests = phase_requests(cfg, repo=repo, verdict_path=verdict_path, timeout_s=timeout_s)
    return tuple(
        PlannedPhase(
            cycle=cycle,
            name=req.name,
            model=req.phase.model,
            gate=req.phase.postcondition.value,
            timeout_s=req.timeout_s,
            cwd=req.cwd,
            argv=build_argv(req),
        )
        for cycle in range(1, cfg.cycles + 1)
        for req in requests
    )


def render_plan(plan: tuple[PlannedPhase, ...], disposition: Disposition) -> str:
    """The plan as text: one line saying whether a night would start now, then one block per phase.

    The argv line is ``shlex.join``ed, so it can be pasted into a shell.
    Never raises.
    """
    verb = "would run" if isinstance(disposition, Run) else "would refuse"
    lines = [f"dry run: {verb}: {disposition.reason}", "no model was called and no runner was built"]
    for step in plan:
        lines += [
            "",
            f"cycle {step.cycle} / {step.name}",
            f"  model:   {step.model}",
            f"  gate:    {step.gate}",
            f"  timeout: {step.timeout_s}s",
            f"  cwd:     {step.cwd}",
            f"  argv:    {shlex.join(step.argv)}",
        ]
    return "\n".join(lines)
