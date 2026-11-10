"""The seam between the phase logic and whatever actually runs the CLI.

Pure: no I/O. The core declares ``Runner``; ``SubprocessRunner`` (real CLI)
and ``FakeRunner`` (scripted, for tests) implement it.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from .request import PhaseRequest


@dataclass(frozen=True)
class RawRun:
    """What a runner observed: exit code, captured output, and whether it hit the timeout.

    When ``timed_out`` is True the process was killed by the runner, so
    ``returncode`` is whatever the kill produced (negative signal number on
    POSIX) and ``stdout`` may be partial or empty.

    Raises ``TypeError`` if a field has the wrong type (``returncode`` must be
    an ``int``, not a ``bool``).
    """

    returncode: int
    stdout: str
    stderr: str
    timed_out: bool

    def __post_init__(self) -> None:
        if isinstance(self.returncode, bool) or not isinstance(self.returncode, int):
            raise TypeError(f"returncode must be an int, got {type(self.returncode).__name__}")
        for field in ("stdout", "stderr"):
            value = getattr(self, field)
            if not isinstance(value, str):
                raise TypeError(f"{field} must be a str, got {type(value).__name__}")
        if not isinstance(self.timed_out, bool):
            raise TypeError(f"timed_out must be a bool, got {type(self.timed_out).__name__}")


class Runner(Protocol):
    """Runs one phase to completion (or to its timeout) and reports what happened."""

    def run(self, request: PhaseRequest) -> RawRun:
        """Run ``request`` once.

        A non-zero exit or a timeout is reported in the ``RawRun``, not
        raised. Raise only when the phase could not be started at all (for
        example the CLI is not installed); ``run_phase`` lets that propagate.
        """
        ...
