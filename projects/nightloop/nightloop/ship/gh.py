"""Talks to GitHub through the ``gh`` CLI: the only module in nightloop that runs ``gh``.

Every call passes every flag it needs (``--title``, ``--body-file``,
``--base``, ``--head``), so ``gh`` has nothing to prompt for: that is the
primary defence against a hang. ``GH_PROMPT_DISABLED=1`` is set as well,
but whether it covers every prompt is unconfirmed. Each call also has a
timeout, stdin is closed unless it carries the body, and only the last line
of ``gh``'s output ever reaches an error message.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from ..process_tail import last_line
from .pr_list import PrListError, open_pr

GH_PROGRAM = "gh"
GH_TIMEOUT_S = 120.0


class GhError(RuntimeError):
    """A ``gh`` command exited non-zero, timed out, or printed something unreadable.

    The message is one line: the command, then ``gh``'s last output line.
    """


class Gh:
    """Runs ``<program> <args>`` in ``repo`` (gh finds the GitHub repo from its remote).

    The environment is read once, here. Raises ``TypeError``/``ValueError``
    if ``repo`` is not an absolute ``Path`` or ``timeout_s`` is not positive.
    """

    def __init__(self, repo: Path, *, program: str = GH_PROGRAM, timeout_s: float = GH_TIMEOUT_S) -> None:
        if not isinstance(repo, Path):
            raise TypeError(f"repo must be a Path, got {type(repo).__name__}")
        if not repo.is_absolute():
            raise ValueError(f"repo must be absolute, got {str(repo)!r}")
        if isinstance(timeout_s, bool) or not isinstance(timeout_s, (int, float)) or not timeout_s > 0:
            raise ValueError(f"timeout_s must be a positive number, got {timeout_s!r}")
        self._repo = repo
        self._program = program
        self._timeout_s = float(timeout_s)
        self._env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GIT_TERMINAL_PROMPT": "0"}

    def open_pr_for(self, branch: str) -> int | None:
        """The number of the one open PR whose head is ``branch``, or ``None``.

        Raises ``GhError`` if the list fails, times out, is unreadable, or
        names more than one PR; ``OSError`` if ``gh`` cannot start.
        """
        out = self._run("pr", "list", "--head", branch, "--state", "open", "--json", "number,url")
        try:
            return open_pr(out)
        except PrListError as exc:
            raise GhError(str(exc)) from exc

    def create_pr(self, *, branch: str, base: str, title: str, body: str) -> str:
        """Open a PR from ``branch`` into ``base`` and return what ``gh`` printed (the URL, on success).

        The body goes in on stdin (``--body-file -``). Raises ``GhError`` on
        a non-zero exit or timeout (after a timeout the PR may or may not
        exist), ``OSError`` if ``gh`` cannot start.
        """
        return self._run(
            "pr", "create", "--title", title, "--body-file", "-", "--base", base, "--head", branch,
            stdin_text=body,
        )

    def body_of(self, pr: int) -> str:
        """PR ``pr``'s body as GitHub holds it. Raises ``GhError``/``OSError`` as ``create_pr``."""
        return self._run("pr", "view", str(pr), "--json", "body", "-q", ".body")

    def mergeable_of(self, pr: int) -> str:
        """PR ``pr``'s raw ``mergeable`` value. Raises ``GhError``/``OSError`` as ``create_pr``."""
        return self._run("pr", "view", str(pr), "--json", "mergeable", "-q", ".mergeable").strip()

    def _run(self, *args: str, stdin_text: str | None = None) -> str:
        """stdout of one ``gh`` call; raises ``GhError`` on non-zero exit or timeout, ``OSError`` if it cannot start."""
        command = " ".join(args[:2])
        try:
            done = subprocess.run(
                [self._program, *args],
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
        except subprocess.TimeoutExpired as exc:
            raise GhError(f"gh {command} timed out after {self._timeout_s:g}s") from exc
        if done.returncode != 0:
            raise GhError(f"gh {command} failed: {last_line(done.stderr, done.stdout, done.returncode)}")
        return done.stdout
