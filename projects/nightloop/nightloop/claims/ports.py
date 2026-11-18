"""The two capabilities the claim reconcilers need from outside, declared by the core.

Pure: no I/O. ``ClaimSource`` finds claims in git (``GitClaimSource``
implements it); ``BacklogStore`` reads and writes the backlog file
(``BacklogFile`` implements it). A lookup that cannot be read is a value,
``Unreadable(reason)``, not an exception, so one bad ref or one failed
``gh`` call never stops the others from being reconciled.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence, Union

from .values import Claim, PrNumber, StrandedBranch


class ClaimSourceError(RuntimeError):
    """The source could not even be listed (e.g. ``for-each-ref`` failed or timed out).

    Raised, not returned: without the list there is nothing per-item to
    report, so the caller has to see it.
    """


@dataclass(frozen=True)
class NoClaim:
    """The source was read and holds no claim (a cycle that failed before claiming, or a PR that claimed nothing)."""


@dataclass(frozen=True)
class Unreadable:
    """The claim could not be read; ``reason`` is one line saying why.

    Raises ``TypeError``/``ValueError`` unless ``reason`` is one non-blank line.
    """

    reason: str

    def __post_init__(self) -> None:
        if not isinstance(self.reason, str):
            raise TypeError(f"reason must be a str, got {type(self.reason).__name__}")
        if self.reason.strip() == "" or "\n" in self.reason or "\r" in self.reason:
            raise ValueError(f"reason must be one non-blank line, got {self.reason!r}")


ClaimLookup = Union[Claim, NoClaim, Unreadable]


@dataclass(frozen=True)
class UnshippedBranch:
    """One recovery branch and what reading its claim found."""

    ref: StrandedBranch
    lookup: ClaimLookup

    def __post_init__(self) -> None:
        if not isinstance(self.ref, StrandedBranch):
            raise TypeError(f"ref must be a StrandedBranch, got {type(self.ref).__name__}")
        if not isinstance(self.lookup, (Claim, NoClaim, Unreadable)):
            raise TypeError(f"lookup must be a Claim, NoClaim or Unreadable, got {type(self.lookup).__name__}")


class ClaimSource(Protocol):
    """Where claims are recovered from."""

    def unshipped_claims(self) -> Sequence[UnshippedBranch]:
        """Every recovery branch, each with the claim it carries relative to main.

        Raises ``ClaimSourceError`` if the branches cannot be listed and
        ``OSError`` if the tool behind the source cannot start.
        """
        ...

    def merged_claim(self, pr: PrNumber) -> ClaimLookup:
        """The claim merged PR ``pr`` added to the backlog.

        Raises ``OSError`` if the tool behind the source cannot start; every
        other failure is ``Unreadable``.
        """
        ...


class BacklogStore(Protocol):
    """The backlog file's text."""

    def read(self) -> str:
        """The whole file, byte for byte. Raises ``OSError`` if it cannot be read."""
        ...

    def write(self, text: str) -> None:
        """Replace the whole file with ``text``, atomically. Raises ``OSError`` on failure."""
        ...
