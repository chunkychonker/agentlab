"""The identifier of one night's run, in agentlab's ``TS`` format.

Pure: no I/O. The same string is the metrics lines' ``run_ts`` and the date
prefix of every recovery branch, so it must be a safe git ref component and
must sort by time, as bash's ``date +%Y-%m-%d_%H%M%S`` does.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# YYYY-MM-DD_HHMMSS with each field in range (day 31 is allowed in every
# month: this is a label, not a calendar).
_RUN_ID = re.compile(
    r"[0-9]{4}-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])_([01][0-9]|2[0-3])[0-5][0-9][0-5][0-9]"
)


@dataclass(frozen=True)
class RunId:
    """One night's id, e.g. ``RunId("2026-11-11_020001")``.

    Raises ``TypeError`` if ``value`` is not a str and ``ValueError`` if it
    is not ``YYYY-MM-DD_HHMMSS`` with in-range fields.
    """

    value: str

    def __post_init__(self) -> None:
        if not isinstance(self.value, str):
            raise TypeError(f"run id must be a str, got {type(self.value).__name__}")
        if not _RUN_ID.fullmatch(self.value):
            raise ValueError(f"run id must look like YYYY-MM-DD_HHMMSS, got {self.value!r}")

    @property
    def date(self) -> str:
        """The ``YYYY-MM-DD`` part."""
        return self.value.split("_", 1)[0]
