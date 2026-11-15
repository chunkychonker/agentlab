"""The ``Workspace`` port implemented on a real git checkout.

The only module in ``nightloop.cycle`` that runs a process or touches the
filesystem. A port of agentlab's ``snapshot_dirty_main`` plus
``reset_to_clean_main`` (``.pipeline/run.sh``), with the same rule: nothing
here ever discards work. Unlike bash it also treats commits left on local
main ahead of the remote as leftover work (milestone 5). Every git call goes
through ``nightloop.git_runner.GitRunner``, so it has a timeout and runs
with ``GIT_TERMINAL_PROMPT=0``: a push that wants credentials fails instead
of hanging the night.
"""

from __future__ import annotations

import subprocess
import time
from pathlib import Path
from typing import Callable

from ..git_runner import GIT_PROGRAM, GIT_TIMEOUT_S, GitRunner
from ..process_tail import last_line
from .naming import recovery_branch
from .ports import Clean, Recovery, RecoveryError, Snapshotted
from .run_id import RunId

MAIN_BRANCH = "main"
REMOTE = "origin"


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
        self._runner = GitRunner(repo, program=git, timeout_s=timeout_s)
        self._repo = repo
        self._now = now
        self._main = main
        self._remote = remote
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
        """Snapshot leftover work to ``cycle/<date>-unshipped-<HHMMSS>-<cycle>``, then restore main.

        Leftover work is either or both of: uncommitted changes (``git status
        --porcelain`` is not empty), and commits on local main that
        ``<remote>/<main>`` does not have (``git rev-list --count
        <remote>/<main>..<main>`` > 0, read from the tracking ref the last
        pull left, with no fetch). If there is neither, skip to restoring
        main and return ``Clean()``. Otherwise make one branch from HEAD
        (``switch -c``), commit the uncommitted changes to it if there are
        any (``add -A``, so ignored files stay out), and ``push -u <remote>
        <branch>``. If main was ahead, then move main back to
        ``<remote>/<main>`` with ``git branch -f`` while the branch is still
        checked out; this discards nothing, because the pushed branch holds
        those commits. Finally ``switch <main>``, ``pull --ff-only <remote>
        <main>``, and confirm the tree is clean.

        Failure modes:

        - The tracking ref ``<remote>/<main>`` is missing, or ``rev-list``
          fails: ``RecoveryError`` before anything changes. "Not ahead" is
          never assumed.
        - Push fails or times out with only uncommitted changes to save: not
          raised; ``Snapshotted(pushed=False)`` and the work is safe on the
          local branch.
        - Push fails or times out while main is ahead: ``RecoveryError``.
          Main is not moved, so its commits stay on main (and on the local
          branch); the night stops instead of carrying them into the next
          cycle.
        - Main is ahead but HEAD does not contain main's commits (a phase
          left HEAD on another branch): ``RecoveryError`` after the branch
          is pushed; main is not moved.
        - Branch creation, ``add`` or ``commit`` fails: ``RecoveryError``.
          The changes are carried back to main (``git switch`` never drops
          them), so main is left dirty and nothing is lost; a branch this
          call created but could not commit to is deleted.
        - Main cannot be moved, checked out or fast-forwarded, or is still
          dirty afterwards: ``RecoveryError``.
        - git cannot be started or a command times out: ``RecoveryError``.
        """
        dirty = self._is_dirty()
        ahead = self._commits_ahead()
        if not dirty and ahead == 0:
            self._restore_main()
            return Clean()
        hhmmss = time.strftime("%H%M%S", time.localtime(self._now()))
        branch = recovery_branch(run_id, cycle, hhmmss)
        created = self._git("switch", "-c", branch)
        if created.returncode != 0:
            back = self._git("switch", self._main)
            raise RecoveryError(
                f"could not create recovery branch {branch} ({_tail(created)}); "
                f"the work is still {_left(dirty, ahead)}{_where(back, self._main)}"
            )
        failure = self._commit(run_id, cycle) if dirty else None
        if failure is not None:
            back = self._git("switch", self._main)
            if back.returncode == 0:
                # Only this call's own branch, which gained no commit: never another cycle's.
                self._git("branch", "-D", branch)
            raise RecoveryError(
                f"snapshot commit on {branch} failed ({failure}); "
                f"the work is still {_left(dirty, ahead)}{_where(back, self._main)}; resolve it by hand"
            )
        pushed = self._push(branch)
        if ahead > 0:
            self._rewind_main(branch, pushed, ahead)
        self._restore_main()
        return Snapshotted(branch=branch, pushed=pushed)

    def _commits_ahead(self) -> int:
        """How many commits local main has that ``<remote>/<main>`` lacks; raises ``RecoveryError``."""
        tracking = f"refs/remotes/{self._remote}/{self._main}"
        counted = self._git("rev-list", "--count", f"{tracking}..refs/heads/{self._main}")
        out = counted.stdout.strip()
        if counted.returncode != 0 or not out.isdigit():
            raise RecoveryError(
                f"cannot tell whether {self._main} is ahead of {self._remote}/{self._main} "
                f"({_tail(counted)}); nothing was changed"
            )
        return int(out)

    def _rewind_main(self, branch: str, pushed: bool, ahead: int) -> None:
        """Point main back at ``<remote>/<main>`` once ``branch`` safely holds its commits.

        Runs while ``branch`` is checked out. Raises ``RecoveryError``, with
        main left where it was (and checked out again if possible), if main
        has a commit that neither ``branch`` nor the remote's main holds, if
        the push failed, or if ``git branch -f`` fails.
        """
        tracking = f"refs/remotes/{self._remote}/{self._main}"
        # Commits on main that neither the branch nor the remote's main holds:
        # moving main would orphan them.
        missing = self._git("rev-list", "--count", f"refs/heads/{self._main}", "^HEAD", f"^{tracking}")
        if missing.returncode != 0 or missing.stdout.strip() != "0":
            back = self._git("switch", self._main)
            found = missing.stdout.strip() if missing.returncode == 0 else f"unknown: {_tail(missing)}"
            raise RecoveryError(
                f"{self._main} has commit(s) ({found}) that recovery branch {branch} does not contain, "
                f"because HEAD was not on {self._main}; {self._main} was not moved{_where(back, self._main)}"
            )
        if not pushed:
            back = self._git("switch", self._main)
            raise RecoveryError(
                f"could not push recovery branch {branch}; {self._main} still has its {ahead} unpushed "
                f"commit(s), also on local branch {branch}{_where(back, self._main)}; "
                "nothing was lost, push it by hand"
            )
        moved = self._git("branch", "-f", self._main, tracking)
        if moved.returncode != 0:
            raise RecoveryError(
                f"could not move {self._main} back to {self._remote}/{self._main} ({_tail(moved)}); "
                f"its commits are safe on pushed branch {branch}"
            )

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
            return self._runner.run(*args)
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise RecoveryError(f"git {' '.join(args)} did not run: {exc}") from exc


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    """The last non-blank line git printed (stderr first), shortened, for an error message."""
    return last_line(proc.stderr, proc.stdout, proc.returncode)


def _left(dirty: bool, ahead: int) -> str:
    """What recovery could not move, for an error message."""
    parts = (["uncommitted"] if dirty else []) + ([f"in {ahead} unpushed commit(s)"] if ahead else [])
    return " and ".join(parts)


def _where(back: subprocess.CompletedProcess[str], main: str) -> str:
    """Where the checkout was left, for an error message."""
    if back.returncode == 0:
        return f" on {main}"
    return f", and switching back to {main} failed too ({_tail(back)})"
