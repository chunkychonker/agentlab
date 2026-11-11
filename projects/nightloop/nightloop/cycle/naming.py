"""Names the branch a cycle's leftover work is snapshotted to.

Pure: no I/O. Same shape as bash's ``snapshot_dirty_main``:
``cycle/<run date>-unshipped-<HHMMSS>-<seq>``. Here ``seq`` is the cycle
number, which is unique within a night by construction, so two cycles that
fail in the same second still get different names without a mutable counter.
"""

from __future__ import annotations

import re

from .run_id import RunId

RECOVERY_PREFIX = "cycle/"
_HHMMSS = re.compile(r"([01][0-9]|2[0-3])[0-5][0-9][0-5][0-9]")


def recovery_branch(run_id: RunId, cycle: int, hhmmss: str) -> str:
    """The recovery branch for ``cycle`` of ``run_id``, snapshotted at local time ``hhmmss``.

    Raises ``ValueError`` if ``hhmmss`` is not six digits of a valid time or
    ``cycle`` is not a positive int.
    """
    if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 1:
        raise ValueError(f"cycle must be a positive int, got {cycle!r}")
    if not isinstance(hhmmss, str) or not _HHMMSS.fullmatch(hhmmss):
        raise ValueError(f"hhmmss must be HHMMSS, got {hhmmss!r}")
    return f"{RECOVERY_PREFIX}{run_id.date}-unshipped-{hhmmss}-{cycle}"
