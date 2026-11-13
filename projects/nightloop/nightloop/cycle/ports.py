"""The two capabilities the cycle core needs from the outside world, declared by the core.

Pure: no I/O. ``Workspace`` is everything the orchestrator asks of the
checkout (``GitWorkspace`` implements it on real git); ``Shipper`` turns a
passed review into a PR (``GhShipper`` implements it on git and the GitHub
CLI; ``FakeShipper`` is the test double).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol, Union

from .result import ShipOutcome
from .run_id import RunId


class RecoveryError(RuntimeError):
    """A cycle's leftover work could not be moved off main, or main could not be restored.

    Ends the night: carrying on would let this cycle's work leak into the
    next. The message says what state the checkout was left in.
    """


@dataclass(frozen=True)
class Clean:
    """The tree was clean after the cycle, so no recovery branch was made."""


@dataclass(frozen=True)
class Snapshotted:
    """The cycle's leftover work was committed to ``branch``.

    ``pushed`` is False when the push to the remote failed; the work is then
    safe on the local branch only (non-fatal, as in bash). Raises
    ``TypeError``/``ValueError`` for an empty ``branch`` or a non-bool
    ``pushed``.
    """

    branch: str
    pushed: bool

    def __post_init__(self) -> None:
        if not isinstance(self.branch, str) or self.branch == "":
            raise ValueError(f"branch must be a non-empty str, got {self.branch!r}")
        if not isinstance(self.pushed, bool):
            raise TypeError(f"pushed must be a bool, got {type(self.pushed).__name__}")


Recovery = Union[Clean, Snapshotted]
RECOVERIES: tuple[type, ...] = (Clean, Snapshotted)


class Workspace(Protocol):
    """The checkout a night runs in."""

    def clear_handoff(self, verdict_path: Path) -> None:
        """Delete the review verdict file so no earlier verdict can authorise this cycle.

        ``verdict_path`` is the same path the review gate reads (relative to
        the repo, or absolute). Absent is fine. Raises ``OSError`` if it
        exists and cannot be removed.
        """
        ...

    def recover(self, run_id: RunId, cycle: int) -> Recovery:
        """Move any leftover work to one recovery branch, then return to a clean, current main.

        Leftover work is uncommitted changes and commits on local main that
        the remote's main does not have. ``Clean()`` if there was nothing to
        move. Raises ``RecoveryError`` if
        the work could not be committed (main is left dirty, nothing is
        discarded) or main could not be restored.
        """
        ...


class Shipper(Protocol):
    """Opens the PR for a cycle whose review passed."""

    def ship(self, run_id: RunId, cycle: int) -> ShipOutcome:
        """Return ``Shipped(pr)`` or ``NotShipped(reason)``.

        Raise only for an environment error; the orchestrator recovers the
        cycle's work and lets the exception propagate.
        """
        ...
