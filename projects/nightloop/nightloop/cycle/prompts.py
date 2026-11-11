"""The prompt each phase is started with.

Pure: no I/O. One named template per phase, adapted from agentlab's
``.pipeline/run.sh`` ``run_cycle``. The agent's own instructions carry the
detail; the prompt says which repo and, for review, which file the
mechanical gate will read.
"""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType
from typing import Mapping

RESEARCH_PROMPT = (
    "Run this cycle's research end to end, following your instructions exactly. "
    "The repo is {repo}."
)
BUILD_PROMPT = (
    "Build this cycle's increment from the newest research note, following your "
    "instructions exactly. Verify it runs. The repo is {repo}."
)
REVIEW_PROMPT = (
    "Independently review this cycle's working-tree diff, run its tests, and write a "
    "PASS/FAIL verdict to {verdict}, following your instructions exactly. The repo is "
    "{repo}. Do not modify the increment."
)

_TEMPLATES: Mapping[str, str] = MappingProxyType(
    {"research": RESEARCH_PROMPT, "build": BUILD_PROMPT, "review": REVIEW_PROMPT}
)


def phase_prompt(name: str, *, repo: Path, verdict_path: Path) -> str:
    """The prompt for phase ``name``. Raises ``KeyError`` for a phase with no template."""
    return _TEMPLATES[name].format(repo=repo, verdict=verdict_path)
