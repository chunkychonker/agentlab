"""The ``PullRequests`` port implemented with the GitHub CLI (``gh``).

The only module in ``nightloop.ship`` that runs ``gh``. It runs in the repo
so ``gh`` picks the GitHub repo from the checkout's remote. The PR body goes
in on stdin (``--body-file -``), never in argv. Prompts are disabled and
every call has a timeout. It runs ``pr list``, ``pr create`` and ``pr view``
only.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path

from ..cycle.git_workspace import MAIN_BRANCH
from .ports import GhFailed

GH_PROGRAM = "gh"
GH_TIMEOUT_S = 60.0
_TAIL_CHARS = 300


class GhCli:
    """``PullRequests`` for the GitHub repo behind the checkout at ``repo``; PRs target ``base``.

    Raises ``TypeError``/``ValueError`` if ``repo`` is not an absolute ``Path``.
    """

    def __init__(
        self,
        repo: Path,
        *,
        base: str = MAIN_BRANCH,
        gh: str = GH_PROGRAM,
        timeout_s: float = GH_TIMEOUT_S,
    ) -> None:
        if not isinstance(repo, Path):
            raise TypeError(f"repo must be a Path, got {type(repo).__name__}")
        if not repo.is_absolute():
            raise ValueError(f"repo must be absolute, got {str(repo)!r}")
        self._repo = repo
        self._base = base
        self._program = gh
        self._timeout_s = timeout_s
        self._env = {
            **os.environ,
            "GH_PROMPT_DISABLED": "1",
            "GH_NO_UPDATE_NOTIFIER": "1",
            "GIT_TERMINAL_PROMPT": "0",
            "NO_COLOR": "1",
        }

    def find(self, branch: str) -> list[int]:
        """``gh pr list --head <branch> --state all --json number``.

        Raises ``GhFailed`` on a non-zero exit, a timeout, or output that is
        not a JSON list of ``{"number": <positive int>}``; ``OSError`` if gh
        cannot start.
        """
        out = self._run("pr", "list", "--head", branch, "--state", "all", "--json", "number")
        try:
            rows = json.loads(out)
        except ValueError as exc:
            raise GhFailed(f"gh pr list printed unreadable JSON: {exc}") from exc
        if not isinstance(rows, list):
            raise GhFailed("gh pr list did not print a JSON list")
        numbers: list[int] = []
        for row in rows:
            number = row.get("number") if isinstance(row, dict) else None
            if isinstance(number, bool) or not isinstance(number, int) or number <= 0:
                raise GhFailed(f"gh pr list printed a row without a PR number: {str(row)[:_TAIL_CHARS]}")
            numbers.append(number)
        return numbers

    def create(self, branch: str, title: str, body: str) -> str:
        """``gh pr create --head <branch> --base <base> --title <title> --body-file -`` with ``body`` on stdin.

        Returns gh's stdout (the PR URL on success). Raises ``GhFailed`` on a
        non-zero exit or a timeout (a URL printed alongside a failure is not
        trusted); ``OSError`` if gh cannot start.
        """
        return self._run(
            "pr", "create", "--head", branch, "--base", self._base, "--title", title, "--body-file", "-",
            stdin=body,
        )

    def body_of(self, pr: int) -> str:
        """``gh pr view <pr> --json body -q .body``. Raises ``GhFailed``, ``ValueError`` for a bad ``pr``, or ``OSError``."""
        return self._run("pr", "view", _pr_arg(pr), "--json", "body", "-q", ".body")

    def mergeable_of(self, pr: int) -> str:
        """``gh pr view <pr> --json mergeable -q .mergeable``, stripped. Raises like ``body_of``."""
        return self._run("pr", "view", _pr_arg(pr), "--json", "mergeable", "-q", ".mergeable").strip()

    def _run(self, *args: str, stdin: str = "") -> str:
        """Run gh in the repo and return stdout; raises ``GhFailed`` (exit or timeout) or ``OSError`` (cannot start)."""
        command = f"gh {args[0]} {args[1]}"
        try:
            done = subprocess.run(
                [self._program, *args],
                cwd=self._repo,
                input=stdin,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_s,
                env=self._env,
            )
        except subprocess.TimeoutExpired as exc:
            raise GhFailed(f"{command} timed out after {self._timeout_s}s") from exc
        if done.returncode != 0:
            raise GhFailed(f"{command} exited {done.returncode}: {_tail(done)}")
        return done.stdout


def _pr_arg(pr: int) -> str:
    if isinstance(pr, bool) or not isinstance(pr, int) or pr <= 0:
        raise ValueError(f"pr must be a positive int, got {pr!r}")
    return str(pr)


def _tail(proc: subprocess.CompletedProcess[str]) -> str:
    """The last non-blank line gh printed (stderr first), shortened."""
    for stream in (proc.stderr, proc.stdout):
        lines = [line for line in (stream or "").splitlines() if line.strip()]
        if lines:
            return lines[-1].strip()[:_TAIL_CHARS]
    return "(no output)"
