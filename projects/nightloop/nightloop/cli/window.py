"""Decides whether the hour on the clock lets a night start.

Pure: no I/O. A port of ``.pipeline/schedule.sh``'s ``schedule_disposition``
plus its override. The caller reads the clock and the override; this module
only judges the hour it is handed. launchd runs a missed job at the next
wake, so the clock has to be checked when the night starts, not trusted from
the schedule.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Union

from ..model import Window

_HOUR_MIN, _HOUR_MAX = 0, 23


@dataclass(frozen=True)
class Run:
    """The night may start; ``reason`` says why, for the dry-run plan."""

    reason: str


@dataclass(frozen=True)
class Refuse:
    """The night must not start; ``reason`` is one line naming the hour and the window."""

    reason: str


Disposition = Union[Run, Refuse]


def window_disposition(hour: object, window: Window, *, ignore_window: bool) -> Disposition:
    """``Run`` iff ``hour`` is in ``[window.start_hour, window.end_hour)`` or ``ignore_window``.

    ``hour`` is the local clock's hour. It is typed ``object`` because it is
    judged here, not trusted: anything that is not an ``int`` from 0 to 23
    (a ``bool``, a ``str``, a ``float``, 24, -1) is ``Refuse``, even when
    ``ignore_window`` is true. ``ignore_window`` overrides the window, not a
    clock that cannot be read. As in bash, a clock that cannot be read is
    not taken as proof that it is night. Never raises.
    """
    span = f"[{window.start_hour:02d}:00, {window.end_hour:02d}:00)"
    if isinstance(hour, bool) or not isinstance(hour, int) or not _HOUR_MIN <= hour <= _HOUR_MAX:
        return Refuse(
            f"the clock gave hour {hour!r}, which is not an hour from 0 to 23; "
            f"refusing to start (run window {span}, end exclusive)"
        )
    if window.start_hour <= hour < window.end_hour:
        return Run(f"hour {hour:02d} is inside the run window {span}")
    if ignore_window:
        return Run(f"hour {hour:02d} is outside the run window {span}, but --ignore-window was given")
    return Refuse(
        f"hour {hour:02d} is outside the run window {span} (end exclusive); "
        f"pass --ignore-window to run anyway"
    )
