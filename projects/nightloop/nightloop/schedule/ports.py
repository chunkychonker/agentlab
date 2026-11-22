"""The ``Launchctl`` port: the launchd operations the scheduler needs, defined by the core.

Pure: no I/O. ``SubprocessLaunchctl`` (``launchctl.py``) implements it for
real; tests use an in-memory fake.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import Protocol

from .plan import JobState


class Launchctl(Protocol):
    """launchd, as seen by ``apply``. Every method raises ``ScheduleError`` when its command fails.

    ``OSError`` (the program cannot be started) propagates unchanged.
    """

    def state(self, domain: str, label: str) -> JobState:
        """Whether ``<domain>/<label>`` is loaded, and running. Reads only."""
        ...

    def enable(self, domain: str, label: str) -> None:
        """Clear a "disabled" override, which would make ``bootstrap`` fail."""
        ...

    def bootstrap(self, domain: str, plist: PurePosixPath) -> None:
        """Load the job described by ``plist`` into ``domain``."""
        ...

    def bootout(self, domain: str, label: str) -> None:
        """Unload ``<domain>/<label>``."""
        ...

    def lint(self, path: PurePosixPath) -> None:
        """Check that ``path`` is a well-formed plist. Reads only."""
        ...


# The methods that change launchd. Tests use this to assert none was called.
MUTATING_METHODS = frozenset({"enable", "bootstrap", "bootout"})
