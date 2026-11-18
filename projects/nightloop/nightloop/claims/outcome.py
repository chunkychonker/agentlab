"""What a reconciler did with one claim: a status per path, plus the reason when unreadable.

Pure: no I/O. The two reconcilers have separate status enums because they
can end in different ways: a stranded claim that is not on main is filed,
never "not found"; a shipped claim that is not on main is "not found",
never filed. Separate enums make the impossible pairing unbuildable.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass

from .values import PrNumber, StrandedBranch


class StrandedStatus(enum.Enum):
    MARKED = "marked"                    # "- [ ] K" on main now reads "- [stranded <ref>] K"
    FILED = "filed"                      # K was never on main; appended under the stranded section
    ALREADY_HANDLED = "already-handled"  # K is on main under another marker; left alone
    NO_CLAIM = "no-claim"                # the branch claims nothing
    UNREADABLE = "unreadable"            # the branch's claim could not be read


class ShippedStatus(enum.Enum):
    MARKED_DONE = "marked-done"          # "- [building] K" now reads "- [done #N] K"
    ALREADY_HANDLED = "already-handled"  # K is on main under another marker; left alone
    NO_CLAIM = "no-claim"                # the merge added no claim
    ITEM_NOT_FOUND = "item-not-found"    # K is not on main (reworded or removed): a human must look
    UNREADABLE = "unreadable"            # the merge's claim could not be read


_CHANGED = {StrandedStatus.MARKED, StrandedStatus.FILED, ShippedStatus.MARKED_DONE}


def _check_reason(status: enum.Enum, unreadable: enum.Enum, reason: object) -> None:
    """``reason`` is one non-blank line exactly when ``status`` is ``unreadable``, else ``None``."""
    if status is unreadable:
        if not isinstance(reason, str) or reason.strip() == "" or "\n" in reason or "\r" in reason:
            raise ValueError(f"an unreadable outcome needs a one-line reason, got {reason!r}")
    elif reason is not None:
        raise ValueError(f"only an unreadable outcome carries a reason, got {reason!r} for {status}")


@dataclass(frozen=True)
class StrandedOutcome:
    """What ``reconcile_stranded`` did for branch ``ref``.

    ``reason`` is set exactly when ``status`` is ``UNREADABLE``. Raises
    ``TypeError``/``ValueError`` otherwise.
    """

    ref: StrandedBranch
    status: StrandedStatus
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.ref, StrandedBranch):
            raise TypeError(f"ref must be a StrandedBranch, got {type(self.ref).__name__}")
        if not isinstance(self.status, StrandedStatus):
            raise TypeError(f"status must be a StrandedStatus, got {type(self.status).__name__}")
        _check_reason(self.status, StrandedStatus.UNREADABLE, self.reason)

    @property
    def changed(self) -> bool:
        """True when this outcome edited the backlog, so the caller has something to commit."""
        return self.status in _CHANGED


@dataclass(frozen=True)
class ShippedOutcome:
    """What ``reconcile_shipped`` did for PR ``pr``.

    ``reason`` is set exactly when ``status`` is ``UNREADABLE``. Raises
    ``TypeError``/``ValueError`` otherwise.
    """

    pr: PrNumber
    status: ShippedStatus
    reason: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.pr, PrNumber):
            raise TypeError(f"pr must be a PrNumber, got {type(self.pr).__name__}")
        if not isinstance(self.status, ShippedStatus):
            raise TypeError(f"status must be a ShippedStatus, got {type(self.status).__name__}")
        _check_reason(self.status, ShippedStatus.UNREADABLE, self.reason)

    @property
    def changed(self) -> bool:
        """True when this outcome edited the backlog, so the caller has something to commit."""
        return self.status in _CHANGED
