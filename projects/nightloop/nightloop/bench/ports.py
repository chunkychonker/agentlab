"""The seam between the benchmark loop and the throwaway repos its trials run in.

Pure: no I/O. ``nightloop.bench.workspace.TempGitWorkspace`` implements it.
"""

from __future__ import annotations

from pathlib import Path
from typing import ContextManager, Protocol

from .fixture import Fixture


class TrialWorkspace(Protocol):
    """Makes one fresh repo per trial, and reads the verdict a trial left there."""

    def materialize(self, fixture: Fixture) -> ContextManager[Path]:
        """A context that yields the absolute path of a new repo holding ``fixture``, and removes it on exit.

        The repo is never the live checkout. It has the fixture's note
        committed under ``research/``, the reviewer template committed under
        ``.claude/agents/``, the increment's files untracked, and no remote.
        Raises ``OSError`` if the repo cannot be made (``git`` cannot start
        or a git step fails); nothing is left behind.
        """
        ...

    def read_verdict(self, repo: Path, verdict_path: Path) -> str:
        """The verdict file's text (``verdict_path`` relative to ``repo``), bytes that are not UTF-8 replaced.

        Called only after the gate found a PASS or FAIL there. Raises
        ``OSError`` if it cannot be read.
        """
        ...
