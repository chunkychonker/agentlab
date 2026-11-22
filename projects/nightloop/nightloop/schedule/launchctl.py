"""Runs ``launchctl`` and ``plutil``: the only scheduler module that starts a process.

Implements the ``Launchctl`` port. Programs are called by absolute path, so
a ``launchctl`` earlier on ``PATH`` cannot stand in for the real one.
"""

from __future__ import annotations

import shlex
import subprocess
from pathlib import PurePosixPath

from ..process_tail import last_line
from .errors import ScheduleError
from .launchd_print import parse_print
from .plan import JobState

LAUNCHCTL_PROGRAM = "/bin/launchctl"
PLUTIL_PROGRAM = "/usr/bin/plutil"
# Per command. launchctl answers in well under a second; a hang is a bug.
COMMAND_TIMEOUT_S = 30.0


class SubprocessLaunchctl:
    """``Launchctl`` backed by the real commands.

    Every method raises ``ScheduleError`` quoting the command and its last
    output line when it exits non-zero or runs past ``timeout_s`` (the child
    is killed first), and ``OSError`` if the program cannot be started.
    """

    def __init__(
        self,
        *,
        launchctl: str = LAUNCHCTL_PROGRAM,
        plutil: str = PLUTIL_PROGRAM,
        timeout_s: float = COMMAND_TIMEOUT_S,
    ) -> None:
        self._launchctl = launchctl
        self._plutil = plutil
        self._timeout_s = timeout_s

    def state(self, domain: str, label: str) -> JobState:
        """``launchctl print <domain>/<label>``, parsed by ``parse_print`` (which may raise)."""
        done = self._run(self._launchctl, "print", f"{domain}/{label}")
        return parse_print(done.returncode, done.stdout, done.stderr)

    def enable(self, domain: str, label: str) -> None:
        self._check(self._launchctl, "enable", f"{domain}/{label}")

    def bootstrap(self, domain: str, plist: PurePosixPath) -> None:
        self._check(self._launchctl, "bootstrap", domain, str(plist))

    def bootout(self, domain: str, label: str) -> None:
        self._check(self._launchctl, "bootout", f"{domain}/{label}")

    def lint(self, path: PurePosixPath) -> None:
        self._check(self._plutil, "-lint", str(path))

    def _check(self, *argv: str) -> None:
        done = self._run(*argv)
        if done.returncode != 0:
            tail = last_line(done.stderr, done.stdout, done.returncode)
            raise ScheduleError(f"`{shlex.join(argv)}` exited {done.returncode}: {tail}")

    def _run(self, *argv: str) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                list(argv),
                stdin=subprocess.DEVNULL,
                capture_output=True,
                text=True,
                encoding="utf-8",
                errors="replace",
                timeout=self._timeout_s,
            )
        except subprocess.TimeoutExpired as exc:
            raise ScheduleError(f"`{shlex.join(argv)}` ran past {self._timeout_s:g}s and was killed") from exc
