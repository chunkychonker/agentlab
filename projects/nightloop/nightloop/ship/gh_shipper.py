"""The real ``Shipper``: commits a passed cycle to its own branch, pushes it, and opens the PR.

Runs ``git`` through ``GitRunner`` and ``gh`` through ``Gh``; the decisions
(what the review section says, which PR number, whether it is mergeable)
are the pure modules beside it. Invariants: it never pushes main, never
force-pushes, and never discards work. Main is moved back to the remote's
main only after the branch holding its commits is pushed, and only if no
commit on main would be left behind.
"""

from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Callable

from ..cycle.result import NotShipped, Shipped, ShipOutcome
from ..cycle.run_id import RunId
from ..git_runner import GIT_PROGRAM, GIT_TIMEOUT_S, GitRunner
from ..process_tail import last_line
from .gh import GH_PROGRAM, Gh, GhError
from .mergeable import Mergeable, poll
from .naming import ship_branch
from .pr_text import commit_message, pr_intro, pr_title
from .pr_url import pr_number
from .review_section import ReviewRecordError, compose_body, has_marker, review_section

MAIN_BRANCH = "main"
REMOTE = "origin"


class _Stop(Exception):
    """Ends ``ship`` early with ``NotShipped(reason)``. Private: never escapes ``ship``."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class GhShipper:
    """Ships a cycle whose review passed, from the git checkout whose top level is ``repo``.

    ``verdict_path`` is the review verdict the gate read (relative to
    ``repo``, or absolute). ``sleep`` is the wait between mergeable reads
    (``time.sleep`` in production). ``timeout_s`` applies to each git and gh
    command.

    Raises ``TypeError``/``ValueError`` if ``repo`` is not an absolute
    ``Path``, ``verdict_path`` is not a ``Path``, ``timeout_s`` is not
    positive, or ``repo`` is not the top level of a git checkout (checked
    once, here, so a bad path fails before any phase runs).
    """

    def __init__(
        self,
        repo: Path,
        verdict_path: Path,
        *,
        sleep: Callable[[float], None],
        main: str = MAIN_BRANCH,
        remote: str = REMOTE,
        git: str = GIT_PROGRAM,
        gh: str = GH_PROGRAM,
        timeout_s: float = GIT_TIMEOUT_S,
    ) -> None:
        if not isinstance(verdict_path, Path):
            raise TypeError(f"verdict_path must be a Path, got {type(verdict_path).__name__}")
        self._git = GitRunner(repo, program=git, timeout_s=timeout_s)
        self._gh = Gh(repo, program=gh, timeout_s=timeout_s)
        self._verdict = repo / verdict_path
        self._sleep = sleep
        self._main = main
        self._remote = remote
        self._tracking = f"refs/remotes/{remote}/{main}"
        try:
            top = self._git.run("rev-parse", "--show-toplevel")
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise ValueError(f"cannot run git in {str(repo)!r}: {exc}") from exc
        if top.returncode != 0 or Path(top.stdout.strip()).resolve() != repo.resolve():
            raise ValueError(f"{str(repo)!r} is not the top level of a git checkout")

    def ship(self, run_id: RunId, cycle: int) -> ShipOutcome:
        """Ship ``cycle`` of ``run_id``: ``Shipped(pr)`` only if the PR exists, shows the record, and is MERGEABLE.

        Steps, stopping at the first failure:

        1. Read the verdict and build the PR body (review section included).
           No ``Findings:`` line, a credential hit, or no room: stop before
           any git or gh call.
        2. Switch to ``cycle/<date>-<HHMMSS>-<cycle>-ship`` (made from HEAD,
           or reused if a retry already made it); commit any uncommitted
           work on it. Stop if the branch holds nothing the remote's main
           lacks, or if main has unpushed commits the branch does not hold.
        3. Push exactly ``refs/heads/<branch>:refs/heads/<branch>`` (never
           main, never forced).
        4. Move main back to the remote's main (``git branch -f``): its
           commits are on the pushed branch now.
        5. Reuse the branch's open PR, else ``gh pr create`` and read the
           number from the printed URL.
        6. Read the body back; it must show the review record's marker.
        7. Poll ``mergeable`` (5 reads, 2s apart while ``UNKNOWN``); only
           ``MERGEABLE`` ships.

        Failure modes:

        - Any step fails, exits non-zero or times out: ``NotShipped`` whose
          reason names the step and quotes only the last output line. After
          a failed step 3 the commit is on the local branch only and main is
          not moved, so recovery still sees any commits main holds. After
          step 3 the work is safe on the pushed branch; a gh timeout means
          the PR may or may not exist, and the reason says so.
        - ``git`` or ``gh`` cannot be started: ``OSError`` propagates (an
          environment error; the orchestrator recovers and re-raises).
        - The verdict file exists but cannot be read: ``OSError`` propagates.
        """
        branch = ship_branch(run_id, cycle)
        try:
            body = self._body(run_id, cycle, branch)
            self._commit_on(branch, run_id, cycle)
            self._push(branch)
            self._rewind_main(branch)
            pr = self._open_pr(branch, pr_title(run_id, cycle), body)
            self._check_record(pr)
            self._check_mergeable(pr)
        except _Stop as stop:
            return NotShipped(stop.reason)
        return Shipped(pr=pr)

    # --- step 1 ---------------------------------------------------------------

    def _body(self, run_id: RunId, cycle: int, branch: str) -> str:
        try:
            text = self._verdict.read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise _Stop(f"the review verdict {self._verdict.name} is missing; nothing to post") from None
        try:
            return compose_body(pr_intro(run_id, cycle, branch), review_section(text))
        except ReviewRecordError as exc:
            raise _Stop(f"review record: {exc}") from None

    # --- step 2 ---------------------------------------------------------------

    def _commit_on(self, branch: str, run_id: RunId, cycle: int) -> None:
        exists = self._exit_code("check for branch", "rev-parse", "--verify", "--quiet", f"refs/heads/{branch}")
        if exists not in (0, 1):
            raise _Stop(f"could not check for branch {branch}")
        pending = self._ok("read git status", "status", "--porcelain").strip() != ""
        if exists == 1 and not pending and self._count(f"{self._tracking}..HEAD") == 0:
            raise _Stop("no changes to ship")
        if exists == 0:
            self._ok(f"switch to existing branch {branch}", "switch", branch)
        else:
            self._ok(f"create branch {branch}", "switch", "-c", branch)
        if pending:
            self._ok(f"stage the work on {branch}", "add", "-A")
            self._ok(f"commit the work on {branch} (it is left uncommitted there)",
                     "commit", "-m", commit_message(run_id, cycle))
        if self._count(f"{self._tracking}..HEAD") == 0:
            raise _Stop("no changes to ship")
        left_behind = self._count(f"refs/heads/{self._main}", "^HEAD", f"^{self._tracking}")
        if left_behind:
            raise _Stop(
                f"{self._main} has {left_behind} unpushed commit(s) that {branch} does not hold; "
                "not shipping a partial cycle"
            )

    # --- steps 3 and 4 --------------------------------------------------------

    def _push(self, branch: str) -> None:
        ref = f"refs/heads/{branch}"
        self._ok(f"push {branch} (the commit is on the local branch only)", "push", self._remote, f"{ref}:{ref}")

    def _rewind_main(self, branch: str) -> None:
        self._ok(
            f"move {self._main} back to {self._remote}/{self._main} (the work is safe on pushed {branch})",
            "branch", "-f", self._main, self._tracking,
        )

    # --- steps 5 to 7 ---------------------------------------------------------

    def _open_pr(self, branch: str, title: str, body: str) -> int:
        try:
            existing = self._gh.open_pr_for(branch)
            if existing is not None:
                return existing
            printed = self._gh.create_pr(branch=branch, base=self._main, title=title, body=body)
        except GhError as exc:
            raise _Stop(f"{exc}; {branch} is pushed, a PR for it may or may not exist") from None
        number = pr_number(printed)
        if number is None:
            raise _Stop(f"gh pr create printed no PR URL; {branch} is pushed, a PR for it may or may not exist")
        return number

    def _check_record(self, pr: int) -> None:
        try:
            body = self._gh.body_of(pr)
        except GhError as exc:
            raise _Stop(f"PR #{pr} opened but its body could not be read back: {exc}") from None
        if not has_marker(body):
            raise _Stop(f"PR #{pr} opened but its body does not show the review record")

    def _check_mergeable(self, pr: int) -> None:
        try:
            polled = poll(lambda: self._gh.mergeable_of(pr), self._sleep)
        except GhError as exc:
            raise _Stop(f"PR #{pr} opened but mergeable could not be read: {exc}") from None
        if polled.state is not Mergeable.MERGEABLE:
            raise _Stop(f"PR #{pr} opened but mergeable={polled.shown}")

    # --- git ------------------------------------------------------------------

    def _run(self, what: str, *args: str) -> subprocess.CompletedProcess[str]:
        """One git command; a timeout is ``_Stop``, a failure to start is ``OSError``."""
        try:
            return self._git.run(*args)
        except subprocess.TimeoutExpired:
            raise _Stop(f"git timed out after {self._git.timeout_s:g}s: {what}") from None

    def _ok(self, what: str, *args: str) -> str:
        """stdout of a git command that must exit 0, else ``_Stop`` naming ``what``."""
        done = self._run(what, *args)
        if done.returncode != 0:
            raise _Stop(f"could not {what}: {last_line(done.stderr, done.stdout, done.returncode)}")
        return done.stdout

    def _exit_code(self, what: str, *args: str) -> int:
        return self._run(what, *args).returncode

    def _count(self, *revs: str) -> int:
        """``git rev-list --count <revs>``; ``_Stop`` if it fails (e.g. the tracking ref is missing)."""
        out = self._ok(f"count commits in {' '.join(revs)}", "rev-list", "--count", *revs).strip()
        if not out.isdigit():
            raise _Stop(f"git rev-list --count printed {out[:40]!r}")
        return int(out)
