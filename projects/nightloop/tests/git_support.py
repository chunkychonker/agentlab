"""A throwaway git checkout with a local bare ``origin``, for the milestone-4 tests (not a test module).

No network: ``origin`` is a directory. The user's global and system git
config are kept out (``GIT_CONFIG_GLOBAL`` points at a file written here),
so signing, hooks paths and default branch names on the machine running
the tests cannot change the result.
"""

from __future__ import annotations

import os
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

GITIGNORE = "logs/\nbuild/\n"
GITCONFIG = """\
[user]
\tname = nightloop tests
\temail = tests@nightloop.invalid
[init]
\tdefaultBranch = main
[commit]
\tgpgsign = false
"""


def git(repo: Path, *args: str) -> str:
    """Run git in ``repo`` and return stdout; raises ``CalledProcessError`` on failure."""
    done = subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )
    return done.stdout


class GitRepoCase(unittest.TestCase):
    """Gives each test ``self.repo`` (a clone on main, clean, pushed) and ``self.origin`` (bare)."""

    def setUp(self) -> None:
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        root = Path(tmp.name).resolve()
        config = root / "gitconfig"
        config.write_text(GITCONFIG, encoding="utf-8")
        self.enterContext(
            mock.patch.dict(os.environ, {"GIT_CONFIG_GLOBAL": str(config), "GIT_CONFIG_NOSYSTEM": "1"})
        )
        self.origin = root / "origin.git"
        subprocess.run(["git", "init", "--bare", "-q", "-b", "main", str(self.origin)], check=True)
        self.repo = root / "repo"
        subprocess.run(["git", "clone", "-q", str(self.origin), str(self.repo)], check=True,
                       capture_output=True)
        (self.repo / ".gitignore").write_text(GITIGNORE, encoding="utf-8")
        (self.repo / "README.md").write_text("# fixture\n", encoding="utf-8")
        git(self.repo, "add", "-A")
        git(self.repo, "commit", "-q", "-m", "initial")
        git(self.repo, "push", "-q", "-u", "origin", "main")

    # --- observations -------------------------------------------------------

    def porcelain(self) -> str:
        return git(self.repo, "status", "--porcelain")

    def current_branch(self) -> str:
        return git(self.repo, "rev-parse", "--abbrev-ref", "HEAD").strip()

    def local_branches(self, pattern: str = "*") -> list[str]:
        out = git(self.repo, "branch", "--list", pattern, "--format=%(refname:short)")
        return [line for line in out.splitlines() if line]

    def origin_branches(self) -> list[str]:
        out = git(self.origin, "branch", "--format=%(refname:short)")
        return [line for line in out.splitlines() if line]

    def file_on(self, ref: str, path: str) -> str:
        return git(self.repo, "show", f"{ref}:{path}")

    def assert_clean_main_equal_to_origin(self) -> None:
        self.assertEqual(self.porcelain(), "")
        self.assertEqual(self.current_branch(), "main")
        self.assertEqual(git(self.repo, "rev-parse", "main"), git(self.origin, "rev-parse", "main"))

    def fail_every_commit(self) -> None:
        """Install a pre-commit hook that refuses every commit, so a snapshot cannot be made."""
        hook = self.repo / ".git" / "hooks" / "pre-commit"
        hook.write_text("#!/bin/sh\necho 'refusing for the test' >&2\nexit 1\n", encoding="utf-8")
        hook.chmod(0o755)
