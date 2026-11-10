"""Shared builders for the milestone-3 phase-runner tests (not a test module)."""

from __future__ import annotations

from pathlib import Path

from nightloop.config import Config, load_config
from nightloop.phase import PhaseRequest

EXAMPLE_CONFIG = Path(__file__).resolve().parent.parent / "examples" / "agentlab.nightloop.toml"
PHASE_NAMES = ("research", "build", "review")


def example_config() -> Config:
    return load_config(EXAMPLE_CONFIG)


def request(name: str, cwd: Path, *, prompt: str = "Do tonight's work.", timeout_s: int = 60) -> PhaseRequest:
    return PhaseRequest(
        name=name,
        phase=getattr(example_config(), name),
        prompt=prompt,
        cwd=cwd,
        timeout_s=timeout_s,
    )
