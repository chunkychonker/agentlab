"""Names the branch a shipped cycle's work is pushed to.

Pure: no I/O. ``cycle/<run date>-<run HHMMSS>-<cycle>-ship``. Both time
parts come from the run id, not a clock, so the name is a function of
``(run_id, cycle)`` alone: a retried ``ship`` for the same cycle finds the
same branch and the same open PR instead of making a second one. A run id
plus a cycle number is unique by construction. The shape cannot collide
with a recovery branch (``cycle/<date>-unshipped-...``).
"""

from __future__ import annotations

from ..cycle.naming import RECOVERY_PREFIX
from ..cycle.run_id import RunId

SHIP_SUFFIX = "-ship"


def ship_branch(run_id: RunId, cycle: int) -> str:
    """The branch for ``cycle`` of ``run_id``. Raises ``ValueError`` unless ``cycle`` is a positive int."""
    if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 1:
        raise ValueError(f"cycle must be a positive int, got {cycle!r}")
    return f"{RECOVERY_PREFIX}{run_id.date}-{run_id.hhmmss}-{cycle}{SHIP_SUFFIX}"
