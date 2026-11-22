"""The single exception type the scheduler raises for a bad job or a failed launchd step."""

from __future__ import annotations


class ScheduleError(Exception):
    """A launchd job value is invalid, or a launchd/plist step failed.

    ``str(err)`` is one line meant for a person: it names the field for a
    bad value, and for a failed step it names the command, quotes its last
    output line and says what state the job was left in.
    """
