"""Reads the one backlog claim a cycle made out of a unified diff of the backlog file.

Pure: no I/O. A port of ``.pipeline/backlog.sh``'s ``backlog_claimed_line``,
``backlog_claim_key`` and ``backlog_claimed_item`` as one function, because
in Python the key and the item come out of the same pass.
"""

from __future__ import annotations

from .markers import CLAIMED_MARKERS, item_line
from .values import Claim

_ADDED = "+"
_CLAIM_PREFIXES: tuple[str, ...] = tuple(_ADDED + item_line(marker, "") for marker in CLAIMED_MARKERS)
_CONTINUATION_PREFIXES = (_ADDED + " ", _ADDED + "\t")


def claim_from_diff(diff: str) -> Claim | None:
    """The first added ``- [researching] `` / ``- [building] `` line, with its added continuation lines.

    ``key`` is that line's text after the marker. ``item`` is ``key`` plus
    each directly following added line that starts with a space or a tab,
    without the ``+``; the first line that is not one (a context line, a
    removed line, or an added line at column 0) ends the item. One cycle
    claims one item, so a second claim line is ignored rather than guessed
    at. A claim line whose text is blank is not a claim. ``None`` when the
    diff holds no claim (normal: a cycle that failed before claiming).
    A ``\\r`` before each ``\\n`` is dropped. Raises ``TypeError`` for a
    non-str, and ``ValueError`` if the claim's own lines hold a ``\\r``
    mid-line (git output read in text mode never does: it is translated to
    ``\\n``).
    """
    if not isinstance(diff, str):
        raise TypeError(f"diff must be a str, got {type(diff).__name__}")
    key: str | None = None
    lines: list[str] = []
    for raw in diff.split("\n"):
        line = raw[:-1] if raw.endswith("\r") else raw
        if key is None:
            text = _claimed_text(line)
            if text is not None:
                key = text
                lines.append(text)
            continue
        if not line.startswith(_CONTINUATION_PREFIXES):
            break
        lines.append(line[len(_ADDED):])
    if key is None:
        return None
    return Claim(key=key, item="\n".join(lines))


def _claimed_text(line: str) -> str | None:
    for prefix in _CLAIM_PREFIXES:
        if line.startswith(prefix):
            text = line[len(prefix):]
            return text if text.strip() else None
    return None
