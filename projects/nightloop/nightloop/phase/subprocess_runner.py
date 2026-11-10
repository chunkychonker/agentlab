"""Runs a phase as a real ``claude -p`` child process.

The only module in ``nightloop.phase`` that imports ``subprocess``. POSIX
only (it uses process groups); Windows is untested.

Why it kills a process *group*: the CLI starts children of its own (tool
shells, MCP servers). ``subprocess.run(timeout=...)`` kills only the direct
child and then waits for the pipes to close, so a grandchild that inherited
stdout keeps the runner waiting forever. Each phase gets its own session, and
on timeout the whole group is sent SIGTERM, then SIGKILL after a grace period.
"""

from __future__ import annotations

import os
import signal
import subprocess
from typing import Sequence

from .argv import CLI_PROGRAM, build_argv
from .request import PhaseRequest
from .runner import RawRun

# How long a timed-out phase gets between SIGTERM and SIGKILL.
TERM_GRACE_S = 10.0
_OUTPUT_ENCODING = "utf-8"


class SubprocessRunner:
    """A ``Runner`` that executes ``build_argv(request)`` with ``request.cwd`` as cwd.

    ``program`` replaces ``argv[0]`` (``claude``) and may be several words,
    for example ``(sys.executable, "stand_in.py")`` in tests; it must be
    non-empty strings. The child inherits the environment, gets no stdin,
    and has its stdout and stderr captured and decoded as UTF-8 (invalid
    bytes replaced, never raised).

    Raises ``ValueError`` if ``program`` is empty or holds an empty string,
    or if ``term_grace_s`` is not positive.
    """

    def __init__(
        self,
        program: Sequence[str] = (CLI_PROGRAM,),
        *,
        term_grace_s: float = TERM_GRACE_S,
    ) -> None:
        program = tuple(program)
        if not program or any(not isinstance(p, str) or p == "" for p in program):
            raise ValueError(f"program must be non-empty strings, got {program!r}")
        if isinstance(term_grace_s, bool) or not isinstance(term_grace_s, (int, float)):
            raise ValueError(f"term_grace_s must be a number, got {type(term_grace_s).__name__}")
        if term_grace_s <= 0:
            raise ValueError(f"term_grace_s must be > 0, got {term_grace_s}")
        self._program = program
        self._term_grace_s = float(term_grace_s)

    def run(self, request: PhaseRequest) -> RawRun:
        """Run the phase until it exits or ``request.timeout_s`` elapses.

        A non-zero exit is a ``RawRun`` with that ``returncode``. On timeout
        the process group is stopped and the ``RawRun`` has ``timed_out=True``
        with whatever output arrived. Raises ``OSError`` (e.g.
        ``FileNotFoundError``) if the program or ``request.cwd`` does not
        exist: the phase never started, so there is nothing to report.
        If this call is interrupted (e.g. ``KeyboardInterrupt``), the group
        is killed before the exception propagates.

        Known limit: a descendant that starts its own session leaves the
        group, is not killed, and can hold stdout open past the timeout.
        """
        argv = (*self._program, *build_argv(request)[1:])
        proc = subprocess.Popen(
            argv,
            cwd=request.cwd,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding=_OUTPUT_ENCODING,
            errors="replace",
            start_new_session=True,
        )
        try:
            try:
                stdout, stderr = proc.communicate(timeout=request.timeout_s)
            except subprocess.TimeoutExpired:
                stdout, stderr = self._stop(proc)
                return RawRun(proc.returncode, stdout or "", stderr or "", timed_out=True)
            return RawRun(proc.returncode, stdout or "", stderr or "", timed_out=False)
        finally:
            if proc.poll() is None:
                _signal_group(proc, signal.SIGKILL)
                proc.wait()

    def _stop(self, proc: subprocess.Popen[str]) -> tuple[str, str]:
        """SIGTERM the group, wait the grace period, then SIGKILL; return the output."""
        _signal_group(proc, signal.SIGTERM)
        try:
            return proc.communicate(timeout=self._term_grace_s)
        except subprocess.TimeoutExpired:
            _signal_group(proc, signal.SIGKILL)
            return proc.communicate()


def _signal_group(proc: subprocess.Popen[str], sig: signal.Signals) -> None:
    """Send ``sig`` to the child's process group.

    A group with no members left raises ``ProcessLookupError``; that means
    the work is already over, so it is the one error ignored here.
    """
    try:
        os.killpg(proc.pid, sig)
    except ProcessLookupError:
        pass
