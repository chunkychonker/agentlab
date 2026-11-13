"""The ``Shipper`` port implemented with git and the ``gh`` CLI.

Runs processes: git and ``gh`` in the checkout. Turns a passed cycle's
uncommitted work into a pushed ``-ship`` branch and an open PR whose body
carries the review record, then runs the mergeable check. It merges nothing:
the open PR is the human gate.

Every refused or failed step is ``NotShipped(reason)``. Only an environment
error (a program that cannot be started, ``OSError``) is raised. Every call
has a timeout and runs with ``GIT_TERMINAL_PROMPT=0`` and
``GH_PROMPT_DISABLED=1``, so nothing waits for a human at night.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .gh_parse import parse_pr_number, parse_pr_view
from .mergeable import MAX_ATTEMPTS, RETRY_DELAY_S, MergeCheck, decide
from .naming import ship_branch
from .result import NotShipped, Shipped, ShipOutcome
from .review_record import PrBody, RecordRefused, compose_pr_body, has_marker
from .run_id import RunId

GIT_PROGRAM = "git"
GH_PROGRAM = "gh"
MAIN_BRANCH = "main"
REMOTE = "origin"
# Per command. Generous: a push over a slow link is the long one.
TIMEOUT_S = 120.0
_TAIL_CHARS = 300
_TOKEN_CHARS = 40


@dataclass(frozen=True)
class _Ran:
    """One finished command; ``returncode`` is ``None`` when it timed out."""

    returncode: int | None
    stdout: str
    stderr: str
    timeout_s: float

    @property
    def ok(self) -> bool:
        return self.returncode == 0

    def tail(self) -> str:
        """The last non-blank line it printed (stderr first), shortened, for a reason."""
        if self.returncode is None:
            return f"timed out after {self.timeout_s:g}s"
        for stream in (self.stderr, self.stdout):
            lines = [line for line in stream.splitlines() if line.strip()]
            if lines:
                return lines[-1].strip()[:_TAIL_CHARS]
        return f"exit {self.returncode}"


class GhShipper:
    """A ``Shipper`` over the git checkout whose top level is ``repo``.

    ``verdict_path`` is the review verdict file (relative to ``repo``, or
    absolute), the same path the review gate reads. ``title`` gives the
    commit subject and PR title. ``now`` is the clock for the branch's
    ``HHMMSS`` (local time). ``sleep`` waits between mergeable reads.

    Raises ``TypeError``/``ValueError`` if ``repo`` is not an absolute
    ``Path``, ``ValueError`` if it is not a checkout's top level or ``gh``
    is not an executable on PATH (checked here, so a bad setup fails before
    any phase runs).
    """

    def __init__(
        self,
        repo: Path,
        *,
        verdict_path: Path,
        title: Callable[[], str],
        now: Callable[[], float],
        sleep: Callable[[float], None],
        main: str = MAIN_BRANCH,
        remote: str = REMOTE,
        git: str = GIT_PROGRAM,
        gh: str = GH_PROGRAM,
        timeout_s: float = TIMEOUT_S,
    ) -> None:
        if not isinstance(repo, Path):
            raise TypeError(f"repo must be a Path, got {type(repo).__name__}")
        if not repo.is_absolute():
            raise ValueError(f"repo must be absolute, got {str(repo)!r}")
        if shutil.which(gh) is None:
            raise ValueError(f"gh program {gh!r} is not an executable on PATH")
        self._repo = repo
        self._verdict_path = verdict_path
        self._title = title
        self._now = now
        self._sleep = sleep
        self._main = main
        self._remote = remote
        self._git_program = git
        self._gh_program = gh
        self._timeout_s = timeout_s
        self._env = {
            **os.environ,
            "GIT_TERMINAL_PROMPT": "0",
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
        }
        try:
            top = self._git("rev-parse", "--show-toplevel")
        except OSError as exc:
            raise ValueError(f"cannot run git in {str(repo)!r}: {exc}") from exc
        if not top.ok or Path(top.stdout.strip()).resolve() != repo.resolve():
            raise ValueError(f"{str(repo)!r} is not the top level of a git checkout")

    def ship(self, run_id: RunId, cycle: int) -> ShipOutcome:
        """Commit the work to ``cycle/<date>-<HHMMSS>-<cycle>-ship``, push it, open the PR, check it.

        Steps: compose the body from the verdict file (before any git, so
        a refused record creates nothing); check HEAD is main, fetch, and
        refuse if main is ahead of ``<remote>/<main>`` or the tree is clean;
        ``switch -c``, ``add -A``, ``commit``, ``switch <main>``; ``push -u``
        the ship branch only (never main, never forced); ``gh pr create``
        with the body on stdin; then up to ``MAX_ATTEMPTS`` reads of
        ``gh pr view --json body,mergeable``, each checking the marker.

        Returns ``Shipped(pr)`` only when the PR's body carries the record
        and ``mergeable`` is ``MERGEABLE``. Every other result is
        ``NotShipped``, and the checkout is on main: before the commit the
        work is still uncommitted (recovery snapshots it); after it, the
        work is on the ship branch, pushed unless the push failed. A
        branch-name collision is ``NotShipped`` and the existing branch is
        not touched.

        Raises ``OSError`` if git or gh cannot be started (the checkout may
        then be on the ship branch; recovery returns it to main), and
        ``ValueError`` for a ``cycle`` that is not a positive int.
        """
        hhmmss = time.strftime("%H%M%S", time.localtime(self._now()))
        branch = ship_branch(run_id, cycle, hhmmss)
        title = self._title()
        if not isinstance(title, str) or title.strip() == "" or "\n" in title or "\r" in title:
            return NotShipped(f"the PR title is not one non-empty line: {title!r}"[:_TAIL_CHARS])
        body = self._compose(run_id, cycle, branch)
        if isinstance(body, NotShipped):
            return body
        refused = self._check_main()
        if refused is not None:
            return refused
        refused = self._commit_to(branch, title)
        if refused is not None:
            return refused
        return self._publish(branch, title, body)

    # --- steps -----------------------------------------------------------------

    def _compose(self, run_id: RunId, cycle: int, branch: str) -> PrBody | NotShipped:
        """The PR body, or ``NotShipped`` if the verdict cannot be read or the record is refused."""
        path = self._repo / self._verdict_path
        try:
            verdict = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            return NotShipped(f"cannot read the verdict file {path}: {type(exc).__name__}")
        summary = (
            f"Cycle {cycle} of nightloop run {run_id.value}, branch `{branch}`.\n\n"
            "Opened by nightloop after the review gate parsed PASS. "
            "Nothing merges this PR automatically."
        )
        composed = compose_pr_body(summary, verdict)
        if isinstance(composed, RecordRefused):
            return NotShipped(f"review record refused: {composed.reason}")
        return composed

    def _check_main(self) -> NotShipped | None:
        """``None`` if HEAD is main, main is not ahead of the remote's, and there is work; else why not."""
        head = self._git("symbolic-ref", "--short", "-q", "HEAD")
        if not head.ok:
            return NotShipped(f"HEAD is not on a branch ({head.tail()}); expected {self._main}")
        if head.stdout.strip() != self._main:
            return NotShipped(f"the checkout is on {head.stdout.strip()[:_TAIL_CHARS]}, not {self._main}")
        fetched = self._git("fetch", "--quiet", self._remote, self._main)
        if not fetched.ok:
            return NotShipped(f"could not fetch {self._remote}/{self._main}: {fetched.tail()}")
        counted = self._git("rev-list", "--count", f"{self._remote}/{self._main}..{self._main}")
        ahead = counted.stdout.strip()
        if not counted.ok or not ahead.isdigit():
            return NotShipped(f"could not count commits on {self._main} ahead of {self._remote}: {counted.tail()}")
        if int(ahead) > 0:
            return NotShipped(
                f"local {self._main} is {int(ahead)} commit(s) ahead of {self._remote}/{self._main}; "
                "nothing was pushed (recovery moves them to a branch)"
            )
        status = self._git("status", "--porcelain")
        if not status.ok:
            return NotShipped(f"git status failed: {status.tail()}")
        if status.stdout.strip() == "":
            return NotShipped("nothing to ship: the tree is clean")
        return None

    def _commit_to(self, branch: str, title: str) -> NotShipped | None:
        """Commit everything to a new ``branch`` and return to main; ``None`` on success."""
        created = self._git("switch", "-c", branch)
        if not created.ok:
            return NotShipped(f"could not create {branch} ({created.tail()}); the work is still uncommitted")
        added = self._git("add", "-A")
        committed = self._git("commit", "--quiet", f"--message={title}") if added.ok else added
        if not committed.ok:
            back = self._git("switch", self._main)
            if back.ok:
                # Only this call's own, empty branch: never another one.
                self._git("branch", "-D", branch)
                where = f"still uncommitted on {self._main}"
            else:
                where = f"uncommitted, and switching back to {self._main} failed ({back.tail()})"
            return NotShipped(f"commit on {branch} failed ({committed.tail()}); the work is {where}")
        back = self._git("switch", self._main)
        if not back.ok:
            return NotShipped(f"committed to {branch} but could not switch back to {self._main}: {back.tail()}")
        return None

    def _publish(self, branch: str, title: str, body: PrBody) -> ShipOutcome:
        """Push ``branch``, open its PR, read it back and run the mergeable check."""
        pushed = self._git("push", "--quiet", "-u", self._remote, branch)
        if not pushed.ok:
            return NotShipped(f"push failed for {branch} ({pushed.tail()}); the work is on the local branch")
        created = self._gh(
            "pr", "create", "--head", branch, "--base", self._main, f"--title={title}", "--body-file", "-",
            stdin_text=body.text,
        )
        if not created.ok:
            return NotShipped(f"gh pr create failed for pushed branch {branch}: {created.tail()}")
        pr = parse_pr_number(created.stdout)
        if pr is None:
            return NotShipped(f"gh pr create printed no PR URL for pushed branch {branch}; check it by hand")
        for attempt in range(1, MAX_ATTEMPTS + 1):
            viewed = self._gh("pr", "view", str(pr), "--json", "body,mergeable")
            if not viewed.ok:
                return NotShipped(f"PR #{pr} left open: gh pr view failed ({viewed.tail()})")
            view = parse_pr_view(viewed.stdout)
            if view is None:
                return NotShipped(f"PR #{pr} left open: unreadable gh output from gh pr view")
            if not has_marker(view.body):
                return NotShipped(f"PR #{pr} left open: its body read back without the review record")
            check = decide(view.mergeable, attempt, MAX_ATTEMPTS)
            if check is MergeCheck.MERGEABLE:
                return Shipped(pr)
            if check is MergeCheck.REFUSED:
                return NotShipped(f"PR #{pr} left open: mergeable={_token(view.mergeable)}")
            self._sleep(RETRY_DELAY_S)
        raise AssertionError("decide never retries on the last attempt")

    # --- processes -------------------------------------------------------------

    def _git(self, *args: str) -> _Ran:
        return self._run([self._git_program, "-C", str(self._repo), *args])

    def _gh(self, *args: str, stdin_text: str | None = None) -> _Ran:
        return self._run([self._gh_program, *args], stdin_text=stdin_text)

    def _run(self, argv: list[str], *, stdin_text: str | None = None) -> _Ran:
        """Run one command in the repo; a timeout is a ``_Ran`` with no returncode. Raises ``OSError``."""
        try:
            done = subprocess.run(
                argv,
                cwd=self._repo,
                input=stdin_text,
                stdin=subprocess.DEVNULL if stdin_text is None else None,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_s,
                env=self._env,
            )
        except subprocess.TimeoutExpired:
            return _Ran(None, "", "", self._timeout_s)
        return _Ran(done.returncode, done.stdout, done.stderr, self._timeout_s)


def _token(value: str) -> str:
    """``value`` as-is if it looks like a GitHub enum word, else quoted, so a reason stays one line."""
    if value != "" and len(value) <= _TOKEN_CHARS and value.replace("_", "").isalnum() and value.isascii():
        return value
    return repr(value)[:_TAIL_CHARS]
