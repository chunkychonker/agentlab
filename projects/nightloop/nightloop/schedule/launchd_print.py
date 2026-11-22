"""Reads a job's ``JobState`` from ``launchctl print gui/<uid>/<label>``'s exit code and output.

Pure: no I/O. ``launchctl print`` is not a stable interface, so this parse
is kept small and fails loudly on anything it does not recognise rather
than guessing "idle" (which would let ``install`` boot out a running night).
Fixtures in the tests are real output captured on Darwin 25.2.
"""

from __future__ import annotations

import re

from .errors import ScheduleError
from .plan import JobState

# launchctl print's exit status for "Could not find service ... in domain".
NOT_FOUND_RC = 113
RUNNING_STATE = "running"
# Top-level states with no process: booting the job out kills nothing.
IDLE_STATES = frozenset({"not running", "waiting", "spawn scheduled"})
# The job's own state is the one-tab-indented key. Nested blocks (e.g.
# "resource coalition") carry their own two-tab "state = active", and
# "job state = exited" is a different key.
_TOP_LEVEL_STATE = re.compile(r"^\tstate = (.+)$", re.MULTILINE)


def parse_print(returncode: int, stdout: str, stderr: str = "") -> JobState:
    """``NOT_LOADED`` for exit 113; else the top-level ``state = ...`` line on exit 0.

    Raises ``ScheduleError`` (quoting the output's last line) for any other
    exit code, for exit 0 with no top-level state line or more than one, and
    for a state that is neither ``running`` nor one of ``IDLE_STATES``.
    """
    if returncode == NOT_FOUND_RC:
        return JobState.NOT_LOADED
    if returncode != 0:
        tail = (stderr.strip() or stdout.strip() or "no output").splitlines()[-1]
        raise ScheduleError(f"launchctl print exited {returncode}: {tail}")
    states = [match.strip() for match in _TOP_LEVEL_STATE.findall(stdout)]
    if len(states) != 1:
        raise ScheduleError(
            f"launchctl print: expected one top-level 'state = ...' line, found {len(states)}"
        )
    state = states[0]
    if state == RUNNING_STATE:
        return JobState.RUNNING
    if state in IDLE_STATES:
        return JobState.LOADED_IDLE
    raise ScheduleError(
        f"launchctl print: unknown job state {state!r}; refusing to guess whether it is running"
    )
