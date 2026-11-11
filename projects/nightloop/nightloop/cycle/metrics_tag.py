"""Adds the night's ``run_ts`` and the ``cycle`` number to a phase's metrics line.

Pure: no I/O. ``run_phase`` (milestone 3) writes lines without either key
because it does not know them; bash's ``metrics.sh`` puts them first, so they
go first here too and every other key keeps its value and order.
"""

from __future__ import annotations

import json
from typing import Callable

from .run_id import RunId

RUN_TS_KEY = "run_ts"
CYCLE_KEY = "cycle"


def tag_line(line: str, run_id: RunId, cycle: int) -> str:
    """Return ``line`` with ``run_ts`` and ``cycle`` prepended, as one compact JSON object.

    Raises ``ValueError`` if ``line`` is not one JSON object, or already has
    either key (a second tagger would otherwise overwrite the first silently).
    """
    doc = json.loads(line)
    if not isinstance(doc, dict):
        raise ValueError(f"a metrics line must be a JSON object, got {type(doc).__name__}")
    clash = {RUN_TS_KEY, CYCLE_KEY} & doc.keys()
    if clash:
        raise ValueError(f"metrics line already has {sorted(clash)}")
    tagged = {RUN_TS_KEY: run_id.value, CYCLE_KEY: cycle, **doc}
    return json.dumps(tagged, separators=(",", ":"), allow_nan=False)


def tagging_sink(sink: Callable[[str], None], run_id: RunId, cycle: int) -> Callable[[str], None]:
    """A metrics sink that tags each line (see ``tag_line``) and passes it to ``sink``.

    The returned sink raises whatever ``tag_line`` or ``sink`` raises.
    """

    def emit(line: str) -> None:
        sink(tag_line(line, run_id, cycle))

    return emit
