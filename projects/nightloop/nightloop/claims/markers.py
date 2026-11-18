"""The backlog's line format: the claim markers and the stranded section, each named once.

Pure: no I/O. A backlog item is a line at column 0 of the form
``- [<marker>] <text>``; its wrapped continuation lines are indented. The
item's ``<text>`` is its identity, matched as a whole string, never as a
pattern (item text carries backticks, parentheses and brackets).
"""

from __future__ import annotations

ITEM_OPEN = "- ["
ITEM_CLOSE = "] "

UNCLAIMED_MARKER = " "
UNCLAIMED_PREFIX = ITEM_OPEN + UNCLAIMED_MARKER + ITEM_CLOSE  # "- [ ] "

# The only two markers a cycle's own claim carries. One constant, so the diff
# reader and any future caller cannot disagree about the set.
CLAIMED_MARKERS: tuple[str, ...] = ("researching", "building")
BUILDING_MARKER = "building"

STRANDED_MARKER = "stranded"
DONE_MARKER_PREFIX = "done #"

STRANDED_SECTION = "## Stranded work (unshipped branches)"
STRANDED_SECTION_INTRO: tuple[str, ...] = (
    "Appended by nightloop when a failed cycle's claim names an item that is not",
    "on main: the topic and its claim were written in one edit, so main never had",
    "it. The branch holds the work; salvaging it is a human's call.",
)


def item_line(marker: str, text: str) -> str:
    """``- [<marker>] <text>``, with no line ending."""
    return f"{ITEM_OPEN}{marker}{ITEM_CLOSE}{text}"


def split_item_line(line: str) -> tuple[str, str] | None:
    """``(marker, text)`` for a column-0 item line, else ``None``.

    ``line`` carries no line ending. The marker ends at the first ``"] "``,
    so the marker can never contain ``"] "``; the text may contain anything,
    brackets included. Never raises for a str.
    """
    if not line.startswith(ITEM_OPEN):
        return None
    close = line.find(ITEM_CLOSE, len(ITEM_OPEN))
    if close == -1:
        return None
    return line[len(ITEM_OPEN):close], line[close + len(ITEM_CLOSE):]
