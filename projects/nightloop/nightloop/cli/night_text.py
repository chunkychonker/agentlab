"""One line per cycle saying how a night ended, for ``nightloop run``'s output.

Pure: no I/O. The metrics file is the record; this is only what a person
at a terminal (or a scheduler's log) sees.
"""

from __future__ import annotations

from ..cycle.ports import Clean, Recovery
from ..cycle.report import NightResult
from ..cycle.result import CycleResult, NotShipped, PhaseFailed, ReviewRefused, Shipped, UsageLimited


def render_night(result: NightResult) -> str:
    """``cycle N: <how it ended>; <what recovery did>`` per cycle, oldest first.

    Raises ``AssertionError`` only for a ``CycleResult`` case this module
    does not know, which would mean the sum type grew without it.
    """
    return "\n".join(
        f"cycle {r.cycle}: {_result_text(r.result)}; {_recovery_text(r.recovery)}"
        for r in result.reports
    )


def _result_text(result: CycleResult) -> str:
    if isinstance(result, Shipped):
        return f"shipped PR #{result.pr}"
    if isinstance(result, NotShipped):
        return f"review passed but not shipped: {result.reason}"
    if isinstance(result, PhaseFailed):
        return f"{result.phase} failed ({result.status.value})"
    if isinstance(result, ReviewRefused):
        return f"review refused ({result.verdict.value})"
    if isinstance(result, UsageLimited):
        return f"usage limit in {result.phase}, resets {result.reset}; night stopped"
    raise AssertionError(f"unhandled cycle result {result!r}")


def _recovery_text(recovery: Recovery) -> str:
    if isinstance(recovery, Clean):
        return "tree clean"
    pushed = "pushed" if recovery.pushed else "NOT pushed, local only"
    return f"leftovers saved to {recovery.branch} ({pushed})"
