"""How a claim step ended, and the port that publishes the backlog to main.

Pure: no I/O. ``BacklogPublisher`` is declared here, by the core, and
implemented at the edge by ``GitBacklogPublisher``. A step's result is a
sum type, ``Published | Nothing | Skipped | PublishFailed``, so "pushed" and
"failed" cannot both be true of one step, and a skip or failure always
carries its one-line reason.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Union


def _check_reason(reason: object) -> None:
    """Raise unless ``reason`` is one non-blank line of text."""
    if not isinstance(reason, str):
        raise TypeError(f"reason must be a str, got {type(reason).__name__}")
    if reason.strip() == "" or "\n" in reason or "\r" in reason:
        raise ValueError(f"reason must be one non-blank line, got {reason!r}")


@dataclass(frozen=True)
class Published:
    """``count`` claim edits were committed and pushed to main in one commit.

    Raises ``TypeError`` unless ``count`` is an int (not a bool) and
    ``ValueError`` unless it is at least 1: publishing nothing is ``Nothing``.
    """

    count: int

    def __post_init__(self) -> None:
        if isinstance(self.count, bool) or not isinstance(self.count, int):
            raise TypeError(f"count must be an int, got {type(self.count).__name__}")
        if self.count < 1:
            raise ValueError(f"a publish carries at least one edit, got {self.count}")


@dataclass(frozen=True)
class Nothing:
    """The step was ready and found nothing to change: no write, no commit, no push."""


@dataclass(frozen=True)
class Skipped:
    """The step did not run because the checkout was not ready; nothing was written.

    Raises ``TypeError``/``ValueError`` unless ``reason`` is one non-blank line.
    """

    reason: str

    def __post_init__(self) -> None:
        _check_reason(self.reason)


@dataclass(frozen=True)
class PublishFailed:
    """The backlog was edited but adding, committing or pushing it failed; ``reason`` says which.

    Whatever was committed stays on local main; the next recovery saves it
    to a branch. Raises ``TypeError``/``ValueError`` unless ``reason`` is
    one non-blank line.
    """

    reason: str

    def __post_init__(self) -> None:
        _check_reason(self.reason)


ClaimStepResult = Union[Published, Nothing, Skipped, PublishFailed]
CLAIM_STEP_RESULTS: tuple[type, ...] = (Published, Nothing, Skipped, PublishFailed)


class BacklogPublisher(Protocol):
    """Checks the checkout can take a backlog commit, and makes and pushes that commit."""

    def ready(self) -> Skipped | None:
        """``None`` when the backlog exists, HEAD is main, and main equals the remote's main after a fetch.

        Otherwise ``Skipped(reason)``, having written nothing. Raises
        ``OSError`` if the tool behind it cannot start.
        """
        ...

    def publish(self, message: str) -> PublishFailed | None:
        """Commit the backlog file alone with ``message`` and push main; ``None`` once pushed.

        ``PublishFailed(reason)`` if any step fails; a commit that was made
        is left in place, never reset, and nothing is ever force-pushed.
        Raises ``OSError`` if the tool behind it cannot start.
        """
        ...
