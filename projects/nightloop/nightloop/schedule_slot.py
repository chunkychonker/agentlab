"""The run slot every scheduler shares: when a scheduled night fires, inside the config's window.

Pure: no I/O, and no ``plistlib``. It lives outside ``nightloop.schedule``
(launchd) so the GitHub Actions generator can use it without importing that
package, whose ``__init__`` loads ``plistlib`` (which cannot load on some
interpreters; see the 2026-11-22 decisions entry). ``nightloop.schedule.job``
re-exports ``slot_for`` and ``FORBIDDEN_ARG`` from here.
"""

from __future__ import annotations

from .model import Window

# The flag that would let a scheduled run start outside the window. No
# scheduler may ever pass it: the run's own window check is what stops a
# late launch (launchd after a wake, a delayed GitHub cron).
FORBIDDEN_ARG = "--ignore-window"
_HOUR_MAX, _MINUTE_MAX = 23, 59


class SlotError(ValueError):
    """A slot hour or minute is not an int in range, or the hour is outside the window.

    ``str(err)`` names the field first (``hour: ...`` or ``minute: ...``).
    """


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def slot_for(window: Window, hour: int | None = None, minute: int = 0) -> tuple[int, int]:
    """The ``(hour, minute)`` the job fires at: ``hour`` (default ``window.start_hour``) and ``minute``.

    Raises ``SlotError`` if ``hour`` is not an int in
    ``[window.start_hour, window.end_hour)`` or ``minute`` is not an int
    0-59 (``bool`` is not an int here). Every slot it returns is inside the
    window, so a job that fires on time is never refused by ``nightloop run``.
    """
    chosen = window.start_hour if hour is None else hour
    if not _is_int(chosen) or not 0 <= chosen <= _HOUR_MAX:
        raise SlotError(f"hour: must be an integer from 0 to {_HOUR_MAX}, got {chosen!r}")
    if not _is_int(minute) or not 0 <= minute <= _MINUTE_MAX:
        raise SlotError(f"minute: must be an integer from 0 to {_MINUTE_MAX}, got {minute!r}")
    if not window.start_hour <= chosen < window.end_hour:
        raise SlotError(
            f"hour: {chosen} is outside the run window "
            f"[{window.start_hour:02d}:00, {window.end_hour:02d}:00) (end exclusive)"
        )
    return chosen, minute
