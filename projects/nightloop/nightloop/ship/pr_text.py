"""The fixed words the shipper writes: commit message, PR title, and the body text above the review.

Pure: no I/O. Generic on purpose: titles drawn from the research note need
the spec path passed through the ``Shipper`` port, which is a later change.
"""

from __future__ import annotations

from ..cycle.run_id import RunId


def commit_message(run_id: RunId, cycle: int) -> str:
    """The message for the commit that holds the cycle's uncommitted work."""
    return f"nightloop: cycle {cycle} of run {run_id.value}"


def pr_title(run_id: RunId, cycle: int) -> str:
    """The PR title."""
    return f"nightloop: cycle {cycle} of run {run_id.value}"


def pr_intro(run_id: RunId, cycle: int, branch: str) -> str:
    """The body text above the review section."""
    return (
        f"Opened by nightloop for cycle {cycle} of run `{run_id.value}` from branch `{branch}`.\n\n"
        "The review passed its fail-closed gate before this PR was opened; "
        "the reviewer's record follows."
    )
