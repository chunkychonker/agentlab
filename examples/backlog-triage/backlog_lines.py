"""Reads and rewrites the BACKLOG.md checkbox marker at the start of a line.

Pure: text in, text out. Lines are split on ``\n`` only and numbered from 1,
matching ``grep -n`` (``str.splitlines`` would also split on form feeds and
Unicode separators and drift from grep's numbering).

The marker contract matches ``.pipeline/backlog.sh``: an item is unclaimed iff
its line begins with the literal ``- [ ] `` at column 0. A closed item gets
``- [done by hand YYYY-MM-DD: stale <kind>] ``, which starts with ``- [done ``
so ``backlog_has_unresolved`` stops treating it as queued (a genuine recurrence
re-files), and carries no ``#N`` so the health check's PR-number test ignores it.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import date

from classify import UNCLAIMED_PREFIX, Historical, Verdict


def parse_unclaimed(text: str) -> list[tuple[int, str]]:
    """``(line_no, line)`` for every line starting ``- [ ] `` at column 0,
    line endings stripped. Never raises."""
    return [
        (number, line.removesuffix("\r"))
        for number, line in enumerate(text.split("\n"), start=1)
        if line.startswith(UNCLAIMED_PREFIX)
    ]


def closing_marker(verdict: Historical, today: date) -> str:
    """The replacement for ``- [ ] `` on a closed line, trailing space included."""
    return f"- [done by hand {today.isoformat()}: stale {verdict.kind.value}] "


def apply(text: str, verdicts: Iterable[Verdict], today: date) -> str:
    """``text`` with every Historical verdict's ``- [ ] `` prefix replaced by
    its closing marker. Actionable verdicts are ignored. Every other byte,
    including line endings, is preserved.

    Raises ValueError, changing nothing, if a Historical verdict's line number
    is out of range or that line no longer starts with ``- [ ] `` (the verdicts
    were computed against different text).
    """
    lines = text.split("\n")
    for verdict in verdicts:
        if not isinstance(verdict, Historical):
            continue
        index = verdict.line_no - 1
        if not 0 <= index < len(lines) or not lines[index].startswith(UNCLAIMED_PREFIX):
            raise ValueError(
                f"line {verdict.line_no} is not an unclaimed item; verdicts do not match this text"
            )
        lines[index] = closing_marker(verdict, today) + lines[index][len(UNCLAIMED_PREFIX):]
    return "\n".join(lines)
