"""Reads the top-level keys of a Markdown file's YAML frontmatter.

Pure: no I/O. This is not a YAML parser. It reads the subset that agent
templates use: ``key: value`` lines at column 0 between a ``---`` first line
and the next ``---`` line. An indented line or a ``- item`` line continues
the key above it (so a nested ``hooks:`` map is still seen as the key
``hooks``); blank lines and ``#`` comments are skipped. That is enough to say
which fields a template sets, which is all the checker needs.
"""

from __future__ import annotations

import re

_FENCE = "---"
_KEY_LINE = re.compile(r"([A-Za-z][A-Za-z0-9_-]*):(.*)")


class FrontmatterError(ValueError):
    """The text has no frontmatter block, or a line in it is not one this reader accepts."""


def parse_frontmatter(text: str) -> dict[str, str]:
    """Return the frontmatter's top-level keys mapped to their first-line values, stripped.

    Raises ``FrontmatterError`` if the first line is not ``---``, if no closing
    ``---`` line follows, if a key appears twice, if a continuation line comes
    before any key, or if a column-0 line is not ``key: value``.
    """
    lines = text.split("\n")
    if not lines or lines[0].rstrip("\r") != _FENCE:
        raise FrontmatterError("the first line is not '---'")
    fields: dict[str, str] = {}
    for number, raw in enumerate(lines[1:], start=2):
        line = raw.rstrip("\r")
        if line == _FENCE:
            return fields
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line[0] in " \t" or line.startswith("- "):
            if not fields:
                raise FrontmatterError(f"line {number} continues a key, but no key came before it")
            continue
        match = _KEY_LINE.fullmatch(line)
        if match is None:
            raise FrontmatterError(f"line {number} is not 'key: value'")
        key = match.group(1)
        if key in fields:
            raise FrontmatterError(f"line {number} repeats the key {key!r}")
        fields[key] = match.group(2).strip()
    raise FrontmatterError("no closing '---' line")
