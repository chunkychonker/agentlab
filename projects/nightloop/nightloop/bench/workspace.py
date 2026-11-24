"""Builds one throwaway git repo per benchmark trial, outside the live checkout.

The only bench module that starts a process or writes files. Each repo is a
fresh ``git init`` under the system temp dir (or ``parent``): a base commit
holding the fixture's research note, the reviewer template as a project
agent, and ignore rules; then the increment's files, left untracked. It has
no remote, so a misbehaving reviewer cannot push. The repo is deleted when
the trial's context exits.

Nothing in the repo names the fixture: the dir name, the note's file name
and the commit message are the same for every fixture, because a slug like
``path-prefix-escape`` would tell the reviewer what to look for.

Its own git calls ignore the user's global and system git config (no hooks,
no signing, a fixed identity), so the machine cannot change the base commit.
The reviewer, run later in the repo, sees the user's normal environment.
"""

from __future__ import annotations

import contextlib
import os
import shutil
import subprocess
import tempfile
from pathlib import Path, PurePosixPath
from typing import Iterator

from .fixture import Fixture

GIT_PROGRAM = "git"
GIT_TIMEOUT_S = 60.0
MAIN_BRANCH = "main"
# The only note under research/, the same name for every fixture.
NOTE_PATH = PurePosixPath("research") / "proposal.md"
BASE_COMMIT_MESSAGE = "base"
AGENTS_DIR = PurePosixPath(".claude") / "agents"
GITIGNORE = "logs/\n__pycache__/\n"
_PREFIX = "nightloop-bench-"  # no slug: the path is in the prompt
_IDENTITY = ("-c", "user.name=nightloop bench", "-c", "user.email=bench@nightloop.invalid")
_HERMETIC = ("-c", "core.hooksPath=/dev/null", "-c", "commit.gpgsign=false")


class WorkspaceError(OSError):
    """A git step that builds a trial repo exited non-zero (an environment problem, like git not starting)."""


def agent_path(agent: str) -> PurePosixPath:
    """Where the reviewer template sits, so ``claude --agent <agent>`` resolves it as a project agent."""
    return AGENTS_DIR / f"{agent}.md"


class TempGitWorkspace:
    """A ``TrialWorkspace`` that puts ``template`` at ``.claude/agents/<agent>.md`` in every trial repo.

    ``parent`` is where trial repos are made (default: the system temp dir).
    Raises ``TypeError`` if ``template`` is not bytes, ``ValueError`` if
    ``agent`` is blank or ``parent`` is relative.
    """

    def __init__(
        self,
        template: bytes,
        agent: str,
        *,
        parent: Path | None = None,
        git: str = GIT_PROGRAM,
        timeout_s: float = GIT_TIMEOUT_S,
    ) -> None:
        if not isinstance(template, bytes):
            raise TypeError(f"template must be bytes, got {type(template).__name__}")
        if not isinstance(agent, str) or agent.strip() == "" or "/" in agent:
            raise ValueError(f"agent must be a bare agent name, got {agent!r}")
        if parent is not None and not parent.is_absolute():
            raise ValueError(f"parent must be absolute, got {str(parent)!r}")
        self._template = template
        self._agent = agent
        self._parent = parent
        self._git = git
        self._timeout_s = timeout_s
        self._env = {
            **os.environ,
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_TERMINAL_PROMPT": "0",
        }

    @contextlib.contextmanager
    def materialize(self, fixture: Fixture) -> Iterator[Path]:
        """Yield a new trial repo for ``fixture`` (see ``TrialWorkspace``); delete it on exit.

        Raises ``OSError`` if ``git`` cannot start, ``WorkspaceError`` if a
        git step fails, ``subprocess.TimeoutExpired`` if one hangs. On any of
        these the half-built repo is deleted before the error propagates.
        """
        repo = Path(tempfile.mkdtemp(prefix=_PREFIX, dir=self._parent)).resolve()
        try:
            self._build(repo, fixture)
            yield repo
        finally:
            shutil.rmtree(repo, ignore_errors=False)

    def read_verdict(self, repo: Path, verdict_path: Path) -> str:
        """The verdict file's text; undecodable bytes replaced, as the gate does. Raises ``OSError``."""
        return (repo / verdict_path).read_bytes().decode("utf-8", errors="replace")

    def _build(self, repo: Path, fixture: Fixture) -> None:
        self._run(repo, "init", "-q", "--template=", "-b", MAIN_BRANCH)
        _write(repo, PurePosixPath(".gitignore"), GITIGNORE.encode("utf-8"))
        _write(repo, NOTE_PATH, fixture.note.encode("utf-8"))
        _write(repo, agent_path(self._agent), self._template)
        self._run(repo, "add", "-A")
        self._run(repo, "commit", "-q", "-m", BASE_COMMIT_MESSAGE)
        for rel, content in fixture.files.items():
            _write(repo, rel, content)

    def _run(self, repo: Path, command: str, *args: str) -> None:
        done = subprocess.run(
            [self._git, *_HERMETIC, *_IDENTITY, "-C", str(repo), command, *args],
            stdin=subprocess.DEVNULL,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=self._timeout_s,
            env=self._env,
        )
        if done.returncode != 0:
            raise WorkspaceError(
                f"git {command} failed in {repo} (exit {done.returncode}): {done.stderr.strip()}"
            )


def _write(repo: Path, rel: PurePosixPath, content: bytes) -> None:
    path = repo.joinpath(*rel.parts)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(content)
