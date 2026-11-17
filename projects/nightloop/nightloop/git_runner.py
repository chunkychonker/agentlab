"""Runs one git command in one checkout, with a timeout and no terminal prompts.

The edge shared by ``nightloop.cycle.git_workspace`` (recovery) and
``nightloop.ship`` (the shipper). It only runs the command: deciding what a
non-zero exit means is the caller's job, so each caller keeps its own
failure policy (recovery raises ``RecoveryError``; the shipper returns
``NotShipped``).
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

GIT_PROGRAM = "git"
# Per git command. Generous: a push over a slow link is the long one.
GIT_TIMEOUT_S = 120.0


class GitRunner:
    """Runs ``<program> -C <repo> <args>`` with ``GIT_TERMINAL_PROMPT=0``.

    The environment is read once, here: a push that wants credentials fails
    instead of hanging the night. Raises ``TypeError``/``ValueError`` if
    ``repo`` is not an absolute ``Path`` or ``timeout_s`` is not positive.
    """

    def __init__(self, repo: Path, *, program: str = GIT_PROGRAM, timeout_s: float = GIT_TIMEOUT_S) -> None:
        if not isinstance(repo, Path):
            raise TypeError(f"repo must be a Path, got {type(repo).__name__}")
        if not repo.is_absolute():
            raise ValueError(f"repo must be absolute, got {str(repo)!r}")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not timeout_s > 0:
            raise ValueError(f"timeout_s must be a positive number, got {timeout_s!r}")
        self._repo = repo
        self._program = program
        self._timeout_s = float(timeout_s)
        self._env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    @property
    def repo(self) -> Path:
        """The checkout every command runs in."""
        return self._repo

    @property
    def timeout_s(self) -> float:
        """The per-command timeout, in seconds."""
        return self._timeout_s

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run one git command and return it, whatever its exit code.

        stdin is closed; output is captured as UTF-8 (undecodable bytes are
        replaced). Raises ``OSError`` if git cannot be started and
        ``subprocess.TimeoutExpired`` if it runs past the timeout (the child
        is killed first).
        """
        return subprocess.run(
            [self._program, "-C", str(self._repo), *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self._timeout_s,
            env=self._env,
        )
