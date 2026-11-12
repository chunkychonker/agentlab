"""The two capabilities ``GitHubShipper`` needs from the outside world, declared by the shipper.

Pure: no I/O. ``BranchPublisher`` is the git side (``GitBranchPublisher``
implements it), ``PullRequests`` the GitHub side (``GhCli`` implements it
with the ``gh`` CLI). Neither has a way to merge a PR or to push to main.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Union


@dataclass(frozen=True)
class Published:
    """The cycle's work is committed on the PR branch, pushed, and the checkout is back on main."""


@dataclass(frozen=True)
class PublishFailed:
    """The work could not be published; ``reason`` says which step failed and where the work is.

    Raises ``TypeError``/``ValueError`` unless ``reason`` is a non-empty str.
    """

    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str) or self.reason.strip() == "":
            raise ValueError(f"reason must be a non-empty str, got {self.reason!r}")


PublishOutcome = Union[Published, PublishFailed]


class BranchPublisher(Protocol):
    """Puts the working tree's changes on a new branch on the remote."""

    def publish(self, branch: str, message: str) -> PublishOutcome:
        """Create ``branch`` from HEAD, commit everything to it with ``message``, push it, return to main.

        Never commits on main and never rewrites a remote branch. On
        ``PublishFailed`` the work is either still uncommitted on main
        (branch, add or commit failed) or committed on the local ``branch``
        only (push failed); the reason says which. Raises ``OSError`` only if
        git cannot be started.
        """
        ...


class GhFailed(RuntimeError):
    """A ``gh`` command ran but failed, timed out, or printed something unreadable.

    The message is one line saying which command and why.
    """


class PullRequests(Protocol):
    """The GitHub pull-request operations the shipper uses, and no others."""

    def find(self, branch: str) -> list[int]:
        """Numbers of every PR (any state) whose head is ``branch``. Raises ``GhFailed``, or ``OSError``."""
        ...

    def create(self, branch: str, title: str, body: str) -> str:
        """Open a PR from ``branch`` and return ``gh``'s stdout. Raises ``GhFailed``, or ``OSError``."""
        ...

    def body_of(self, pr: int) -> str:
        """The body of PR ``pr`` as GitHub now has it. Raises ``GhFailed``, or ``OSError``."""
        ...

    def mergeable_of(self, pr: int) -> str:
        """PR ``pr``'s ``mergeable`` value, e.g. ``MERGEABLE``. Raises ``GhFailed``, or ``OSError``."""
        ...
