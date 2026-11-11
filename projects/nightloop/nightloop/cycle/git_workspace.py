"""The ``Workspace`` port implemented on a real git checkout.

The only module in ``nightloop.cycle`` that runs a process or touches the
filesystem. A port of agentlab's ``snapshot_dirty_main`` plus
``reset_to_clean_main`` (``.pipeline/run.sh``), with the same rule: nothing
here ever discards work. Every git call has a timeout and runs with
``GIT_TERMINAL_PROMPT=0``, so a push that wants credentials fails instead of
hanging the night.
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path
from typing import Callable

from .naming import recovery_branch
from .ports import Clean, Recovery, RecoveryError, Snapshotted
from .run_id import RunId

GIT_PROGRAM = "git"
MAIN_BRANCH = "main"
REMOTE = "origin"
# Per git command. Generous: a push over a slow link is the long one.
GIT_TIMEOUT_S = 120.0
_TAIL_CHARS = 300


class GitWorkspace:
    """A ``Workspace`` over the git checkout whose top level is ``repo``.

    ``now`` is the clock used for the recovery branch's ``HHMMSS`` (local
    time, as bash's ``date +%H%M%S``). Handoff files must be git-ignored
    (agentlab's ``logs/`` is): deleting a tracked one would dirty main every
    cycle.

    Raises ``TypeError``/``ValueError`` if ``repo`` is not an absolute
    ``Path``, and ``ValueError`` if it is not the top level of a git
    checkout (checked once, here, so a bad path fails before any phase runs).
    """

    def __init__(
        self,
        repo: Path,
        *,
        now: Callable[[], float],
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
        self._now = now
        self._main = main
        self._remote = remote
        self._program = git
        self._timeout_s = timeout_s
        self._env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
        try:
            top = self._git("rev-parse", "--show-toplevel")
        except RecoveryError as exc:
            raise ValueError(f"cannot run git in {str(repo)!r}: {exc}") from exc
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != repo.resolve():
            raise ValueError(f"{str(repo)!r} is not the top level of a git checkout")

    def clear_handoff(self, verdict_path: Path) -> None:
        """Delete ``repo / verdict_path`` if it exists.

        Raises ``OSError`` if it exists and cannot be removed (for example
        it is a directory): a verdict that cannot be cleared could be stale.
        """
        (self._repo / verdict_path).unlink(missing_ok=True)

    def recover(self, run_id: RunId, cycle: int) -> Recovery:
        """Snapshot a dirty tree to ``cycle/<date>-unshipped-<HHMMSS>-<cycle>``, then restore main.

        Steps, as bash: if ``git status --porcelain`` is empty, skip to
        restoring main. Else ``switch -c <branch>``, ``add -A`` (ignored
        files stay out), ``commit``, ``push -u <remote> <branch>``. Then
        ``switch <main>``, ``pull --ff-only <remote> <main>``, and confirm
        the tree is clean.

        Failure modes:

        - Push fails or times out: not raised; ``Snapshotted(pushed=False)``
          and the work is safe on the local branch.
        - Branch creation, ``add`` or ``commit`` fails: ``RecoveryError``.
          The changes are carried back to main (``git switch`` never drops
          them), so main is left dirty and nothing is lost; a branch this
          call created but could not commit to is deleted.
        - Main cannot be checked out or fast-forwarded, or is still dirty
          afterwards: ``RecoveryError``.
        - git cannot be started or a command times out: ``RecoveryError``.
        """
        if not self._is_dirty():
            self._restore_main()
            return Clean()
        hhmmss = time.strftime("%H%M%S", time.localtime(self._now()))
        branch = recovery_branch(run_id, cycle, hhmmss)
        created = self._git("switch", "-c", branch)
        if created.returncode != 0:
            back = self._git("switch", self._main)
            raise RecoveryError(
                f"could not create recovery branch {branch} ({_tail(created)}); "
                f"the work is still uncommitted{_where(back, self._main)}"
            )
        failure = self._commit(run_id, cycle)
        if failure is not None:
            back = self._git("switch", self._main)
            if back.returncode == 0:
                # Only this call's own, empty branch: never another cycle's.
                self._git("branch", "-D", branch)
            raise RecoveryError(
                f"snapshot commit on {branch} failed ({failure}); "
                f"the work is still uncommitted{_where(back, self._main)}; resolve it by hand"
            )
        pushed = self._push(branch)
        self._restore_main()
        return Snapshotted(branch=branch, pushed=pushed)

    def _commit(self, run_id: RunId, cycle: int) -> str | None:
        """Stage and commit everything; ``None`` on success, else what failed."""
        added = self._git("add", "-A")
        if added.returncode != 0:
            return f"git add: {_tail(added)}"
        message = f"wip: cycle {cycle} of run {run_id.value} left uncommitted"
        committed = self._git("commit", "-m", message)
        if committed.returncode != 0:
            return f"git commit: {_tail(committed)}"
        return None

    def _push(self, branch: str) -> bool:
        """Push ``branch``; False if the push failed or could not run (non-fatal)."""
        try:
            return self._git("push", "-u", self._remote, branch).returncode == 0
        except RecoveryError:
            return False

    def _restore_main(self) -> None:
        """Switch to main, fast-forward it, and check it is clean; raises ``RecoveryError``."""
        switched = self._git("switch", self._main)
        if switched.returncode != 0:
            raise RecoveryError(f"could not switch to {self._main}: {_tail(switched)}")
        pulled = self._git("pull", "--ff-only", self._remote, self._main)
        if pulled.returncode != 0:
            raise RecoveryError(f"could not fast-forward {self._main} from {self._remote}: {_tail(pulled)}")
        if self._is_dirty():
            raise RecoveryError(f"{self._main} is still dirty after recovery")

    def _is_dirty(self) -> bool:
        """True if ``git status --porcelain`` reports anything; raises ``RecoveryError`` if it fails."""
        status = self._git("status", "--porcelain")
        if status.returncode != 0:
            raise RecoveryError(f"git status failed: {_tail(status)}")
        return status.stdout.strip() != ""

    def _git(self, *args: str) -> subprocess.CompletedProcess[str]:
        """Run one git command in the repo; raises ``RecoveryError`` if it cannot start or times out."""
        try:
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
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RecoveryError(f"git {' '.join(args)} did not run: {exc}") from exc


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    """The last non-blank line git printed (stderr first), shortened, for an error message."""
    for stream in (proc.stderr, proc.stdout):
        lines = [line for line in stream.splitlines() if line.strip()]
        if lines:
            return lines[-1].strip()[:_TAIL_CHARS]
    return f"exit {proc.returncode}"


def _where(back: subprocess.CompletedProcess[str], main: str) -> str:
    """Where the checkout was left, for an error message."""
    if back.returncode == 0:
        return f" on {main}"
    return f", and switching back to {main} failed too ({_tail(back)})"
