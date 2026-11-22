"""Builds the wake-schedule advice ``install`` prints but never runs.

Pure: no I/O. launchd cannot wake a sleeping Mac; ``pmset`` can, but needs
sudo and replaces any existing repeating schedule, so it stays a human step.
"""

from __future__ import annotations

WAKE_LEAD_MINUTES = 10
_MINUTES_PER_DAY = 24 * 60
_EVERY_DAY = "MTWRFSU"


def wake_advice(hour: int, minute: int) -> str:
    """The ``sudo pmset repeat wakeorpoweron`` line for ``WAKE_LEAD_MINUTES`` before ``hour:minute``.

    Wraps past midnight (a 00:05 slot gives 23:55). The caller passes a slot
    a ``LaunchdJob`` already accepted. Never raises for such a slot.
    """
    wake = (hour * 60 + minute - WAKE_LEAD_MINUTES) % _MINUTES_PER_DAY
    return f"sudo pmset repeat wakeorpoweron {_EVERY_DAY} {wake // 60:02d}:{wake % 60:02d}:00"
