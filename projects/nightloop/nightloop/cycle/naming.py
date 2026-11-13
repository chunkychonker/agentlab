"""Names the branches a cycle's work is committed to.

Pure: no I/O. The recovery branch has the same shape as bash's
``snapshot_dirty_main``: ``cycle/<run date>-unshipped-<HHMMSS>-<seq>``.
Here ``seq`` is the cycle number, which is unique within a night by
construction, so two cycles that fail in the same second still get different
names without a mutable counter. The ship branch,
``cycle/<run date>-<HHMMSS>-<cycle>-ship``, ends in ``-ship`` and has no
``-unshipped-`` part, so the two can never collide.
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
    _check(cycle, hhmmss)
    return f"{RECOVERY_PREFIX}{run_id.date}-unshipped-{hhmmss}-{cycle}"


def ship_branch(run_id: RunId, cycle: int, hhmmss: str) -> str:
    """The branch ``GhShipper`` commits ``cycle`` of ``run_id`` to, at local time ``hhmmss``.

    Raises ``ValueError`` if ``hhmmss`` is not six digits of a valid time or
    ``cycle`` is not a positive int.
    """
    _check(cycle, hhmmss)
    return f"{RECOVERY_PREFIX}{run_id.date}-{hhmmss}-{cycle}-ship"


def _check(cycle: int, hhmmss: str) -> None:
    if isinstance(cycle, bool) or not isinstance(cycle, int) or cycle < 1:
        raise ValueError(f"cycle must be a positive int, got {cycle!r}")
    if not isinstance(hhmmss, str) or not _HHMMSS.fullmatch(hhmmss):
        raise ValueError(f"hhmmss must be HHMMSS, got {hhmmss!r}")
