"""Publishes the backlog to main with git: the only claims module that commits or pushes.

Implements ``BacklogPublisher``. ``ready`` checks that the checkout can take
a backlog commit (backlog present, HEAD on main, main equal to the remote's
main after a fetch, backlog unmodified). ``publish`` commits the backlog
path alone and pushes ``main`` to ``main``. It never force-pushes, never
resets, and touches no ref but main and the fetch's tracking refs. Every
git call goes through ``GitRunner``, so it has a timeout and never prompts.
"""

from __future__ import annotations

import subprocess
from dataclasses import dataclass

from ..git_runner import GIT_PROGRAM, GIT_TIMEOUT_S, GitRunner
from ..process_tail import last_line
from .backlog_file import BacklogFile
from .publish import PublishFailed, Skipped

# Pathspecs are the backlog's path, never a pattern: a name holding "*" or
# ":" must match only itself.
_LITERAL = "--literal-pathspecs"


@dataclass(frozen=True)
class _GitFailed:
    """One git call failed or timed out; ``detail`` is one line naming it."""

    detail: str


class GitBacklogPublisher:
    """Publishes ``backlog.relative`` in the checkout ``backlog.repo`` to ``<remote>``'s ``<main>``.

    ``git`` is a program name (a stand-in in tests). Raises
    ``TypeError``/``ValueError`` for a non-``BacklogFile``, a blank or
    spaced ``main``/``remote``, or a non-positive ``timeout_s``. Runs no git
    at construction.
    """

    def __init__(
        self,
        backlog: BacklogFile,
        *,
        main: str = "main",
        remote: str = "origin",
        git: str = GIT_PROGRAM,
        timeout_s: float = GIT_TIMEOUT_S,
    ) -> None:
        if not isinstance(backlog, BacklogFile):
            raise TypeError(f"backlog must be a BacklogFile, got {type(backlog).__name__}")
        for field, value in (("main", main), ("remote", remote)):
            if not isinstance(value, str) or value == "" or any(ch.isspace() for ch in value):
                raise ValueError(f"{field} must be a non-empty name with no whitespace, got {value!r}")
        self._backlog = backlog
        self._main = main
        self._remote = remote
        self._git = GitRunner(backlog.repo, program=git, timeout_s=timeout_s)

    def ready(self) -> Skipped | None:
        """``None`` if a backlog commit can be made and pushed now, else ``Skipped(reason)``.

        In order, each failing check is ``Skipped`` and stops the rest:
        the backlog file exists (no git runs before this); ``HEAD`` is the
        branch ``main``; ``git fetch <remote>`` succeeds; ``main`` is neither
        ahead of nor behind ``<remote>/<main>``; the backlog has no
        uncommitted change (staged, unstaged or untracked). A git command
        that fails or times out is ``Skipped`` naming it. Writes no file and
        moves no ref but the fetch's remote-tracking refs. Raises
        ``OSError`` if git cannot start.
        """
        rel = self._backlog.relative
        if not self._backlog.path.is_file():
            return Skipped(f"no backlog file {rel} in {self._backlog.repo}")
        head = self._step("symbolic-ref", "--quiet", "--short", "HEAD")
        if isinstance(head, _GitFailed):
            return Skipped(f"HEAD is not on a branch (detached), not on {self._main}")
        if head.strip() != self._main:
            return Skipped(f"the checkout is on {head.strip()}, not {self._main}")
        fetched = self._step("fetch", "--quiet", self._remote)
        if isinstance(fetched, _GitFailed):
            return Skipped(fetched.detail)
        tracking = f"refs/remotes/{self._remote}/{self._main}"
        counted = self._step("rev-list", "--left-right", "--count", f"refs/heads/{self._main}...{tracking}")
        if isinstance(counted, _GitFailed):
            return Skipped(counted.detail)
        fields = counted.split()
        if len(fields) != 2 or not all(f.isdigit() for f in fields):
            return Skipped(f"cannot compare {self._main} with {self._remote}/{self._main}: got {counted.strip()!r}")
        ahead, behind = int(fields[0]), int(fields[1])
        if ahead:
            return Skipped(f"{self._main} is {ahead} commit(s) ahead of {self._remote}/{self._main} "
                           "(unpublished work; recovery saves it to a branch)")
        if behind:
            return Skipped(f"{self._main} is {behind} commit(s) behind {self._remote}/{self._main}")
        status = self._step(_LITERAL, "status", "--porcelain", "--untracked-files=all", "--", rel)
        if isinstance(status, _GitFailed):
            return Skipped(status.detail)
        if status.strip():
            return Skipped(f"{rel} has uncommitted changes")
        return None

    def publish(self, message: str) -> PublishFailed | None:
        """``git add -- <rel>``, ``git commit -m <message> -- <rel>``, ``git push <remote> main:main``.

        ``None`` once pushed. The commit holds the backlog path only: other
        staged, modified or untracked files are neither committed nor
        touched. Failure modes, each ``PublishFailed`` naming the step:
        ``add`` or ``commit`` fails or times out (the backlog edit stays in
        the working tree); ``push`` is rejected, fails or times out (the
        commit stays on local main, is not reset, and the next recovery
        snapshots it to a branch). Raises ``TypeError``/``ValueError`` for a
        message that is not one non-blank line, and ``OSError`` if git
        cannot start.
        """
        if not isinstance(message, str):
            raise TypeError(f"message must be a str, got {type(message).__name__}")
        if message.strip() == "" or "\n" in message or "\r" in message:
            raise ValueError(f"message must be one non-blank line, got {message!r}")
        rel = self._backlog.relative
        added = self._step(_LITERAL, "add", "--", rel)
        if isinstance(added, _GitFailed):
            return PublishFailed(f"{added.detail} (adding {rel})")
        committed = self._step(_LITERAL, "commit", "--quiet", "-m", message, "--", rel)
        if isinstance(committed, _GitFailed):
            return PublishFailed(f"{committed.detail}; the edit to {rel} is left uncommitted")
        refspec = f"refs/heads/{self._main}:refs/heads/{self._main}"
        pushed = self._step("push", "--quiet", self._remote, refspec)
        if isinstance(pushed, _GitFailed):
            return PublishFailed(f"{pushed.detail}; the commit stays on local {self._main} "
                                 "for recovery to save")
        return None

    def _step(self, *args: str) -> str | _GitFailed:
        """stdout of one git call, or ``_GitFailed("git <cmd> failed: <last line>")``. ``OSError`` propagates."""
        name = next(a for a in args if not a.startswith("-"))
        try:
            done = self._git.run(*args)
        except subprocess.TimeoutExpired:
            return _GitFailed(f"git {name} timed out after {self._git.timeout_s:g}s")
        if done.returncode != 0:
            return _GitFailed(f"git {name} failed: {last_line(done.stderr, done.stdout, done.returncode)}")
        return done.stdout
