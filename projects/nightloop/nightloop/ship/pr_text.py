"""Names the PR branch, writes the PR's title and opening text, and reads the PR number back.

Pure: no I/O. Everything here is a deterministic function of the run id and
cycle, so a retried ship names the same branch and finds the same PR.
"""

from __future__ import annotations

import re

from ..cycle.run_id import RunId

BRANCH_PREFIX = "nightloop/"
# The last non-blank line `gh pr create` prints: https://<host>/<owner>/<repo>/pull/<n>.
_PR_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?/[^/\s]+/[^/\s]+/pull/([1-9][0-9]{0,9})")


def _check_cycle(cycle: object) -> None:
    if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 1:
        raise ValueError(f"cycle must be a positive int, got {cycle!r}")


def branch_name(run_id: RunId, cycle: int) -> str:
    """``nightloop/<run id>-<cycle>``, a valid git ref that is never main.

    Raises ``ValueError`` unless ``cycle`` is a positive int.
    """
    _check_cycle(cycle)
    return f"{BRANCH_PREFIX}{run_id.value}-{cycle}"


def pr_title(run_id: RunId, cycle: int) -> str:
    """One line, used as the PR title and the commit message. Raises ``ValueError`` for a bad ``cycle``."""
    _check_cycle(cycle)
    return f"nightloop: cycle {cycle} of run {run_id.value}"


def pr_base_body(run_id: RunId, cycle: int) -> str:
    """The PR body's opening line, above the review record. Raises ``ValueError`` for a bad ``cycle``."""
    _check_cycle(cycle)
    return (
        f"Opened by nightloop for cycle {cycle} of run {run_id.value}. "
        "The review phase's verdict parsed PASS; its record is below."
    )


def pr_number_from_url(stdout: str) -> int | None:
    """The PR number from ``gh pr create``'s stdout, or ``None`` if its last non-blank line is not a PR URL.

    Strict on purpose: ``gh pr create`` has no ``--json``, and a number
    guessed from other text could point at someone else's PR.
    """
    if not isinstance(stdout, str):
        return None
    lines = [line.strip() for line in stdout.splitlines() if line.strip()]
    if not lines:
        return None
    found = _PR_URL.fullmatch(lines[-1])
    return int(found.group(1)) if found else None
