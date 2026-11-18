"""The backlog edits of the claim lifecycle, as functions from text to an ``Edit``.

Pure: no I/O. A port of ``.pipeline/backlog.sh``'s ``backlog_count_unclaimed``,
``backlog_apply_stranded``, ``backlog_file_stranded`` and ``backlog_mark_done``.
Every function is a reconciler: it decides from the text it is given, and
applied to its own ``Rewritten`` output it returns ``AlreadyHandled``.

An item is matched by its text with ``==`` on the parsed line, never by a
pattern or a prefix. A rewrite changes only the lines it names and keeps
every other byte, including each line's ending (``\\n``, ``\\r\\n``, or
none on a final line). Lines the functions add end in ``\\n``.
"""

from __future__ import annotations

from .edit import AlreadyHandled, Edit, ItemNotFound, Rewritten
from .markers import (
    BUILDING_MARKER,
    DONE_MARKER_PREFIX,
    STRANDED_MARKER,
    STRANDED_SECTION,
    STRANDED_SECTION_INTRO,
    UNCLAIMED_MARKER,
    UNCLAIMED_PREFIX,
    item_line,
    split_item_line,
)
from .values import Claim, PrNumber, StrandedBranch, check_key

_NL = "\n"
_CRLF = "\r\n"

# One line as (text without its ending, its ending). The ending is "\n",
# "\r\n", or "" for a final line with no newline.
_Line = tuple[str, str]


def count_unclaimed(text: str) -> int:
    """How many lines start with ``- [ ] `` at column 0.

    Indented lines and every other marker (``[researching]``, ``[building]``,
    ``[done #N]``, ``[stranded <b>]``) never count. Raises ``TypeError`` for
    a non-str.
    """
    _check_text(text)
    return sum(1 for body, _ in _split(text) if body.startswith(UNCLAIMED_PREFIX))


def apply_stranded(text: str, claim: Claim, branch: StrandedBranch) -> Edit:
    """Rewrite ``- [ ] <claim.key>`` to ``- [stranded <branch>] <claim.key>``.

    ``Rewritten`` when at least one such line was rewritten (continuation
    lines are left as they are). ``AlreadyHandled`` when the key is there
    only under other markers (already stranded, done, or claimed by a cycle
    in flight). ``ItemNotFound`` when no item line has that text; the
    caller may then ``file_stranded``. Raises ``TypeError`` for wrong
    argument types.
    """
    _check_text(text)
    _check_type("claim", claim, Claim)
    _check_type("branch", branch, StrandedBranch)
    return _remark(text, claim.key, UNCLAIMED_MARKER, _stranded_marker(branch))


def file_stranded(text: str, claim: Claim, branch: StrandedBranch) -> Edit:
    """Add ``- [stranded <branch>] <claim.key>`` and its continuation lines under the stranded section.

    For a claim whose item was never on main. The item goes after the last
    non-blank line of the ``STRANDED_SECTION`` (before the next heading).
    The section is created at the end of the text, once, if missing.
    ``AlreadyHandled`` when any item line already has ``claim.key`` under any
    marker, ``[done #N]`` included, so salvaged work is never re-filed.
    Never ``ItemNotFound``. Raises ``TypeError`` for wrong argument types.
    """
    _check_text(text)
    _check_type("claim", claim, Claim)
    _check_type("branch", branch, StrandedBranch)
    lines = _split(text)
    section: int | None = None
    for index, (body, _) in enumerate(lines):
        if _item_text(body) == claim.key:
            return AlreadyHandled()
        if section is None and body == STRANDED_SECTION:
            section = index
    added: list[_Line] = [(item_line(_stranded_marker(branch), claim.key), _NL)]
    added += [(line, _NL) for line in claim.continuation]
    if section is None:
        heading: list[_Line] = [("", _NL)] if lines else []
        heading += [(STRANDED_SECTION, _NL)] + [(line, _NL) for line in STRANDED_SECTION_INTRO]
        return Rewritten(_join(_terminated(lines, len(lines) - 1) + heading + added))
    after = _section_end(lines, section)
    lines = _terminated(lines, after)
    return Rewritten(_join(lines[: after + 1] + added + lines[after + 1:]))


def mark_done(text: str, key: str, pr: PrNumber) -> Edit:
    """Rewrite ``- [building] <key>`` to ``- [done #<pr>] <key>``.

    ``Rewritten`` when at least one such line was rewritten.
    ``AlreadyHandled`` when the key is there only under other markers
    (``[done #M]`` from an earlier pass, keeping its own number;
    ``[stranded <b>]``; ``[researching]``; ``[ ]``). ``ItemNotFound`` when
    no item line has that text. Raises ``TypeError`` for wrong types and
    ``ValueError`` for a blank or multi-line ``key``.
    """
    _check_text(text)
    check_key(key)
    _check_type("pr", pr, PrNumber)
    return _remark(text, key, BUILDING_MARKER, f"{DONE_MARKER_PREFIX}{pr}")


def _remark(text: str, key: str, old_marker: str, new_marker: str) -> Edit:
    """Swap ``old_marker`` for ``new_marker`` on every item line whose text is ``key``."""
    lines = _split(text)
    seen = changed = False
    for index, (body, ending) in enumerate(lines):
        parsed = split_item_line(body)
        if parsed is None or parsed[1] != key:
            continue
        seen = True
        if parsed[0] == old_marker:
            lines[index] = (item_line(new_marker, key), ending)
            changed = True
    if not seen:
        return ItemNotFound()
    if not changed:
        return AlreadyHandled()
    return Rewritten(_join(lines))


def _stranded_marker(branch: StrandedBranch) -> str:
    return f"{STRANDED_MARKER} {branch}"


def _item_text(body: str) -> str | None:
    parsed = split_item_line(body)
    return None if parsed is None else parsed[1]


def _section_end(lines: list[_Line], heading: int) -> int:
    """Index of the section's last non-blank line before the next heading (``heading`` if none)."""
    last = heading
    for index in range(heading + 1, len(lines)):
        body = lines[index][0]
        if body.startswith("#"):
            break
        if body.strip():
            last = index
    return last


def _terminated(lines: list[_Line], index: int) -> list[_Line]:
    """``lines`` with line ``index`` given a ``\\n`` if it has no ending (a no-op for ``index`` -1)."""
    if index < 0 or lines[index][1]:
        return lines
    out = list(lines)
    out[index] = (out[index][0], _NL)
    return out


def _split(text: str) -> list[_Line]:
    """``text`` as lines with their endings; ``_join`` gives ``text`` back byte for byte."""
    pieces = text.split(_NL)
    lines: list[_Line] = []
    for piece in pieces[:-1]:
        lines.append((piece[:-1], _CRLF) if piece.endswith("\r") else (piece, _NL))
    if pieces[-1]:
        lines.append((pieces[-1], ""))
    return lines


def _join(lines: list[_Line]) -> str:
    return "".join(body + ending for body, ending in lines)


def _check_text(text: object) -> None:
    if not isinstance(text, str):
        raise TypeError(f"text must be a str, got {type(text).__name__}")


def _check_type(field: str, value: object, kind: type) -> None:
    if not isinstance(value, kind):
        raise TypeError(f"{field} must be a {kind.__name__}, got {type(value).__name__}")
