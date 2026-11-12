"""The ``BranchPublisher`` port implemented with git on a real checkout.

The only module in ``nightloop.ship`` that runs git. Every command has a
timeout and runs with ``GIT_TERMINAL_PROMPT=0``, so a push that wants
credentials fails instead of hanging the night. It pushes exactly one
branch, by name, never with a force flag and never to main.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..cycle.git_workspace import GIT_PROGRAM, GIT_TIMEOUT_S, MAIN_BRANCH, REMOTE
from .ports import PublishFailed, PublishOutcome, Published

_TAIL_CHARS = 300
_TIMED_OUT_RC = 124


class GitBranchPublisher:
    """A ``BranchPublisher`` over the git checkout whose top level is ``repo``.

    Raises ``TypeError``/``ValueError`` if ``repo`` is not an absolute
    ``Path``. (``GitWorkspace`` on the same repo already checks that it is a
    checkout's top level.)
    """

    def __init__(
        self,
        repo: Path,
        *,
        main: str = MAIN_BRANCH,
        remote: str = REMOTE,
        git: str = GIT_PROGRAM,
        timeout_s: float = GIT_TIMEOUT_S,
    ) -> None:
        if not isinstance(repo, Path):
            raise TypeError(f"repo must be a Path, got {type(repo).__name__}")
        if not repo.is_absolute():
            raise ValueError(f"repo must be absolute, got {str(repo)!r}")
        self._repo = repo
        self._main = main
        self._remote = remote
        self._program = git
        self._timeout_s = timeout_s
        self._env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}

    def publish(self, branch: str, message: str) -> PublishOutcome:
        """``switch -c <branch>``, ``add -A``, ``commit -m <message>``, ``push -u <remote> <branch>``, ``switch <main>``.

        Failure modes (each a ``PublishFailed`` whose reason says where the work is):

        - ``switch -c`` fails (e.g. the branch exists): nothing moved, the
          work is still uncommitted on main.
        - ``add`` or ``commit`` fails (a hook, or nothing to commit): the
          changes are carried back to main and the empty branch this call
          made is deleted.
        - ``push`` fails or times out: the commit is on the local branch
          only; main is checked out and clean.
        - Switching back to main fails: the reason says HEAD was left on
          ``branch``; recovery switches back again.

        Raises ``ValueError`` if ``branch`` is empty, starts with ``-`` or is
        main, and ``OSError`` if git cannot be started.
        """
        if not isinstance(branch, str) or branch == "" or branch.startswith("-") or branch == self._main:
            raise ValueError(f"refusing to publish to branch {branch!r}")
        created = self._git("switch", "-c", branch)
        if created.returncode != 0:
            return PublishFailed(
                f"could not create branch {branch} ({_tail(created)}); the work is still uncommitted on {self._main}"
            )
        added = self._git("add", "-A")
        committed = self._git("commit", "-m", message) if added.returncode == 0 else added
        if committed.returncode != 0:
            back = self._git("switch", self._main)
            if back.returncode == 0:
                # Only this call's own branch, which has no commit of its own.
                self._git("branch", "-D", branch)
            return PublishFailed(
                f"commit on {branch} failed ({_tail(committed)}); the work is still uncommitted"
                f"{self._where(back)}"
            )
        pushed = self._git("push", "-u", self._remote, branch)
        back = self._git("switch", self._main)
        if pushed.returncode != 0:
            return PublishFailed(
                f"push failed for {branch} ({_tail(pushed)}); the commit is on the local branch only"
                f"{'' if back.returncode == 0 else f', and HEAD is still on {branch} ({_tail(back)})'}"
            )
        if back.returncode != 0:
            return PublishFailed(f"pushed {branch} but could not switch back to {self._main} ({_tail(back)})")
        return Published()

    def _where(self, back: subprocess.CompletedProcess[str]) -> str:
        if back.returncode == 0:
            return f" on {self._main}"
        return f" on the new branch, and switching back to {self._main} failed ({_tail(back)})"

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run one git command; a timeout comes back as exit 124. Raises ``OSError`` if git cannot start."""
        argv = [self._program, "-C", str(self._repo), *args]
        try:
            return subprocess.run(
                argv,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_s,
                env=self._env,
            )
        except subprocess.TimeoutExpired:
            return subprocess.CompletedProcess(argv, _TIMED_OUT_RC, "", f"git {args[0]} timed out after {self._timeout_s}s")


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    """The last non-blank line git printed (stderr first), shortened."""
    for stream in (proc.stderr, proc.stdout):
        lines = [line for line in (stream or "").splitlines() if line.strip()]
        if lines:
            return lines[-1].strip()[:_TAIL_CHARS]
    return f"exit {proc.returncode}"
