"""Pure text parsers for the docs drift guard (``test_docs.py``); not a test module.

Every function here takes Markdown text and returns plain values. None
reads a file, runs a command or imports nightloop: the test methods do the
reading and compare what these return with what the code says. The parsers
handle the Markdown that nightloop's docs actually use (ATX headings,
backtick fences, inline code spans, inline ``[text](target)`` links), not
the whole CommonMark grammar.
"""

from __future__ import annotations

import re
import shlex
from dataclasses import dataclass

# A fence opens with three or more backticks at column 0 (optionally an info
# string) and closes with a backtick run at least as long.
_FENCE = re.compile(r"^(`{3,})(.*)$")
_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_CODE_SPAN = re.compile(r"(`+)(.+?)(?<!`)\1(?!`)", re.DOTALL)
_LINK = re.compile(r"!?\[[^\]\n]*\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
_EXTERNAL = re.compile(r"^[a-z][a-z0-9+.-]*:", re.IGNORECASE)  # http:, https:, mailto:, ...
# Where a nightloop invocation may start inside a logical command line: the
# line's start, a shell separator, `python3 -m` (any 3.x), or a `$ ` prompt.
_INVOCATION = re.compile(
    r"(?:^|[;&|(]\s*|\bpython3?(?:\.\d+)?\s+-m\s+|\$\s+)nightloop\s+(.*)$"
)
_WORD = re.compile(r"[a-z][a-z0-9-]*")
_FLAG = re.compile(r"(?<![\w-])(--[a-z][a-z0-9-]*)")
_EXIT_ROW = re.compile(r"^\|\s*(\d+)\s*\|\s*`([A-Z][A-Z0-9_]*)`\s*\|")
_DOCS_CHECK = re.compile(r"^<!--\s*docs-check:\s*([a-z0-9-]+)\s*-->\s*$")
_FORBIDDEN = re.compile(r"\b(TODO|TBD|FIXME|XXX)\b|lorem ipsum", re.IGNORECASE)

GATE_PREFIX = "Gate: "
NO_INCIDENT = "no recorded incident"


@dataclass(frozen=True)
class Fence:
    """One fenced code block: its info string and its body lines (without the fence lines)."""

    info: str
    lines: tuple[str, ...]


@dataclass(frozen=True)
class Invocation:
    """One ``nightloop <words> [--flags]`` command named in a doc.

    ``words`` are the bare words right after ``nightloop`` (the subcommand,
    then at most one action word), ``flags`` every ``--long-flag`` in the
    rest of that command, and ``text`` the command as written, for messages.
    """

    words: tuple[str, ...]
    flags: tuple[str, ...]
    text: str


@dataclass(frozen=True)
class GateSection:
    """One ``## Gate: <name>`` section of architecture.md, as its labelled fields.

    ``fields`` maps a label (``Module``, ``Tests``, ``Incident``, ``Source``)
    to every value given for it, in order, so a repeated label is visible.
    """

    name: str
    fields: dict[str, tuple[str, ...]]


def split_fences(md: str) -> tuple[str, list[Fence]]:
    """Return ``(prose, fences)``: the text with every fenced block blanked, and the blocks.

    Each fenced block's lines are replaced by empty lines in ``prose``, so
    line structure and paragraph breaks survive. Raises ``ValueError`` on a
    fence that is never closed: a doc in that state renders wrongly, and
    every later check would read code as prose.
    """
    prose: list[str] = []
    fences: list[Fence] = []
    lines = md.split("\n")
    i = 0
    while i < len(lines):
        opened = _FENCE.match(lines[i])
        if opened is None:
            prose.append(lines[i])
            i += 1
            continue
        ticks, info = opened.group(1), opened.group(2).strip()
        body: list[str] = []
        i += 1
        while i < len(lines) and not (lines[i].startswith(ticks) and lines[i].strip("`") == ""):
            body.append(lines[i])
            i += 1
        if i == len(lines):
            raise ValueError(f"unclosed code fence opened with {ticks}{info}")
        fences.append(Fence(info, tuple(body)))
        prose.extend([""] * (len(body) + 2))
        i += 1
    return "\n".join(prose), fences


def code_spans(prose: str) -> list[str]:
    """Inline code spans in fence-free ``prose``, with internal line breaks collapsed to spaces."""
    return [" ".join(m.group(2).split()) for m in _CODE_SPAN.finditer(prose)]


def links(md: str) -> list[str]:
    """Every inline link or image target in ``md`` that is outside code, in order.

    Targets inside fenced blocks or code spans are not links and are skipped.
    """
    prose, _ = split_fences(md)
    without_code = _CODE_SPAN.sub(lambda m: " " * len(m.group(0)), prose)
    return [m.group(1) for m in _LINK.finditer(without_code)]


def is_external(target: str) -> bool:
    """True for a target with a URL scheme (``https:``, ``mailto:``): not checked offline."""
    return _EXTERNAL.match(target) is not None


def split_target(target: str) -> tuple[str, str | None]:
    """``"a/b.md#x"`` -> ``("a/b.md", "x")``; ``"#x"`` -> ``("", "x")``; no ``#`` -> anchor ``None``."""
    path, hash_, anchor = target.partition("#")
    return path, (anchor if hash_ else None)


def heading_anchors(md: str) -> set[str]:
    """The anchors GitHub generates for ``md``'s ATX headings (outside fences).

    GitHub's rule: lower-case, drop every character that is not a letter,
    digit, space, hyphen or underscore, turn spaces into hyphens, and suffix
    ``-1``, ``-2``... on repeats.
    """
    prose, _ = split_fences(md)
    seen: dict[str, int] = {}
    anchors: set[str] = set()
    for line in prose.split("\n"):
        match = _HEADING.match(line)
        if match is None:
            continue
        slug = re.sub(r"[^\w\- ]", "", match.group(2).lower()).replace(" ", "-")
        count = seen.get(slug, 0)
        seen[slug] = count + 1
        anchors.add(slug if count == 0 else f"{slug}-{count}")
    return anchors


def command_lines(md: str) -> list[str]:
    """Logical command lines: each fenced line (backslash continuations joined) and each code span."""
    prose, fences = split_fences(md)
    out: list[str] = []
    for fence in fences:
        pending = ""
        for line in fence.lines:
            if line.rstrip().endswith("\\"):
                pending += line.rstrip()[:-1] + " "
                continue
            out.append(" ".join((pending + line).split()))
            pending = ""
        if pending:
            out.append(" ".join(pending.split()))
    out.extend(code_spans(prose))
    return out


def invocations(md: str) -> list[Invocation]:
    """Every ``nightloop <subcommand> ...`` command in ``md``'s code (fences and spans).

    Prose mentions are not commands and are skipped. A command ends at a
    shell separator (``&&``, ``||``, ``;``, ``|``) or a `` #`` comment. The
    first bare word after ``nightloop`` is the subcommand; a second bare word
    right after it is kept as a possible action (the test decides whether
    the subcommand takes one).
    """
    found: list[Invocation] = []
    for line in command_lines(md):
        match = _INVOCATION.search(line)
        if match is None:
            continue
        rest = re.split(r"\s(?:&&|\|\||;|\||#)", " " + match.group(1))[0].strip()
        try:
            tokens = shlex.split(rest)
        except ValueError:  # an unbalanced quote: fall back to whitespace
            tokens = rest.split()
        words: list[str] = []
        for token in tokens[:2]:
            if _WORD.fullmatch(token) is None:
                break
            words.append(token)
        if not words:
            continue
        found.append(Invocation(tuple(words), tuple(_FLAG.findall(rest)), "nightloop " + rest))
    return found


def exit_code_table(md: str, heading: str = "Exit codes") -> dict[str, int]:
    """The ``| <code> | `<NAME>` | ... |`` rows under the ``## <heading>`` section, as name -> code.

    Raises ``ValueError`` if the section is missing, if a name or a code is
    listed twice, or if the section has no such row.
    """
    section = _section(md, heading)
    table: dict[str, int] = {}
    codes: set[int] = set()
    for line in section.split("\n"):
        match = _EXIT_ROW.match(line)
        if match is None:
            continue
        code, name = int(match.group(1)), match.group(2)
        if name in table or code in codes:
            raise ValueError(f"exit code row listed twice: {line.strip()}")
        table[name] = code
        codes.add(code)
    if not table:
        raise ValueError(f"no exit-code rows under '## {heading}'")
    return table


def gate_sections(md: str) -> list[GateSection]:
    """Every ``## Gate: <name>`` section, with its ``- **Label:** value`` bullet lines.

    A value continues onto following indented lines (a wrapped bullet).
    Only the four labels in ``GATE_LABELS`` are collected.
    """
    prose, _ = split_fences(md)
    sections: list[GateSection] = []
    current: dict[str, list[str]] | None = None
    name = ""
    label: str | None = None
    for line in prose.split("\n"):
        heading = _HEADING.match(line)
        if heading is not None:
            if current is not None:
                sections.append(_freeze(name, current))
            current = None
            label = None
            if len(heading.group(1)) == 2 and heading.group(2).startswith(GATE_PREFIX):
                name = heading.group(2)[len(GATE_PREFIX):].strip()
                current = {}
            continue
        if current is None:
            continue
        field = re.match(r"^- \*\*(Module|Tests|Incident|Source):\*\*\s*(.*)$", line)
        if field is not None:
            label = field.group(1)
            current.setdefault(label, []).append(field.group(2).strip())
        elif label is not None and line.startswith("  ") and line.strip():
            values = current[label]
            values[-1] = f"{values[-1]} {line.strip()}".strip()
        else:
            label = None
    if current is not None:
        sections.append(_freeze(name, current))
    return sections


def backticked_paths(value: str) -> list[str]:
    """The code spans in a field value, e.g. the module paths of a ``Module`` line."""
    return code_spans(value)


def marked_fence(md: str, marker: str) -> Fence:
    """The fenced block right after a ``<!-- docs-check: <marker> -->`` line (blank lines allowed between).

    Raises ``ValueError`` if the marker is absent, given twice, or not
    followed by a fence.
    """
    lines = md.split("\n")
    hits = [i for i, line in enumerate(lines) if (m := _DOCS_CHECK.match(line)) and m.group(1) == marker]
    if len(hits) != 1:
        raise ValueError(f"expected one 'docs-check: {marker}' marker, found {len(hits)}")
    rest = lines[hits[0] + 1:]
    while rest and not rest[0].strip():
        rest = rest[1:]
    if not rest or _FENCE.match(rest[0]) is None:
        raise ValueError(f"'docs-check: {marker}' is not followed by a code fence")
    _, fences = split_fences("\n".join(rest))
    return fences[0]


def forbidden_words(md: str) -> list[str]:
    """Placeholder markers left in ``md`` (``TODO``, ``TBD``, ``FIXME``, ``XXX``, lorem ipsum)."""
    return [m.group(0) for m in _FORBIDDEN.finditer(md)]


def _section(md: str, heading: str) -> str:
    """The text of the ``## <heading>`` section, up to the next heading of level 2 or above."""
    prose, _ = split_fences(md)
    lines = prose.split("\n")
    start = None
    for i, line in enumerate(lines):
        match = _HEADING.match(line)
        if match is None:
            continue
        if start is not None and len(match.group(1)) <= 2:
            return "\n".join(lines[start:i])
        if len(match.group(1)) == 2 and match.group(2) == heading:
            start = i + 1
    if start is None:
        raise ValueError(f"no '## {heading}' section")
    return "\n".join(lines[start:])


def _freeze(name: str, fields: dict[str, list[str]]) -> GateSection:
    return GateSection(name, {k: tuple(v) for k, v in fields.items()})
