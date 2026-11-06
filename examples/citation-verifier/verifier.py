"""Check each citation in a response against the source documents it points into.

Pure: no I/O, no SDK. ``content`` is the response's ``content`` as plain dicts
(``[b.model_dump(exclude_none=True) for b in message.content]`` from the SDK,
or the raw JSON). ``sources`` comes from ``sources.sources_from_messages``.

A citation is MATCH only when its pointer, re-applied to the source, yields
exactly ``cited_text``. The API's "guaranteed valid" covers its own parse; this
covers everything after it (a cache, a rewritten document, a reordered list).

Order of checks for one citation, first hit wins:
    1. unknown ``type``                         -> UNVERIFIABLE
    2. missing / non-int / non-str field        -> ValueError (malformed input)
    3. ``document_index`` not in ``sources``    -> BAD_DOCUMENT_INDEX
    4. ``page_location``                        -> UNVERIFIABLE (no PDF parser)
    5. source is ``Opaque``                     -> UNVERIFIABLE
    6. citation kind does not fit source kind   -> BAD_DOCUMENT_INDEX
    7. not ``0 <= start < end <= len(source)``  -> OUT_OF_RANGE
    8. re-sliced text == ``cited_text``         -> MATCH, else MISMATCH
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import Mapping, Sequence

from sources import CustomContent, Opaque, PlainText, Source

# Wire names, defined once.
_TEXT_BLOCK = "text"
_CHAR = "char_location"
_CONTENT_BLOCK = "content_block_location"
_PAGE = "page_location"


class Status(StrEnum):
    MATCH = "MATCH"
    MISMATCH = "MISMATCH"
    OUT_OF_RANGE = "OUT_OF_RANGE"
    BAD_DOCUMENT_INDEX = "BAD_DOCUMENT_INDEX"
    UNVERIFIABLE = "UNVERIFIABLE"


@dataclass(frozen=True)
class Verdict:
    """The outcome for one citation: ``content[block_index]["citations"][citation_index]``."""

    block_index: int
    citation_index: int
    status: Status
    detail: str


# ---- Parsed citations: one type per location kind ------------------------- #


@dataclass(frozen=True)
class _Span:
    """A ``char_location`` or ``content_block_location``: re-sliceable offline."""

    kind: str
    document_index: int
    start: int
    end: int
    cited_text: str


@dataclass(frozen=True)
class _Page:
    document_index: int


@dataclass(frozen=True)
class _Unknown:
    type_name: str


_SPAN_FIELDS = {
    _CHAR: ("start_char_index", "end_char_index"),
    _CONTENT_BLOCK: ("start_block_index", "end_block_index"),
}


# ---- Public API ------------------------------------------------------------ #


def verify_response(sources: Sequence[Source], content: Sequence[Mapping]) -> list[Verdict]:
    """Return one ``Verdict`` per citation, in block order then citation order.

    Non-text blocks are skipped. A text block with no ``citations`` key, or
    ``citations: None``, or ``[]``, contributes no verdicts. An empty result
    therefore means "nothing was cited", not "everything checked out".

    Raises:
        ValueError: a content block is not a mapping or has no ``type``; a text
            block has no str ``text`` or its ``citations`` is not a list; a
            citation is not a mapping, has no ``type``, or (for a known type)
            lacks a required field or carries one of the wrong type. The
            message names the block, the citation and the field. Nothing is
            returned on failure; there is no partial list.
    """
    verdicts: list[Verdict] = []
    for block_index, citations in _cited_text_blocks(content):
        for citation_index, raw in enumerate(citations):
            where = f"content[{block_index}].citations[{citation_index}]"
            status, detail = _judge(sources, _parse(raw, where))
            verdicts.append(Verdict(block_index, citation_index, status, detail))
    return verdicts


def uncited_text_blocks(content: Sequence[Mapping]) -> list[int]:
    """Return the indices of text blocks that carry no citation.

    Connective text such as ``"According to the document, "`` shows up here
    too; the list is a prompt for a human or a policy, not an error.

    Raises:
        ValueError: same block-shape failures as ``verify_response``.
    """
    return [i for i, citations in _cited_text_blocks(content) if not citations]


def count_by_status(verdicts: Sequence[Verdict]) -> dict[Status, int]:
    """Count verdicts per status. Every ``Status`` is present, zero included."""
    counts = {status: 0 for status in Status}
    for verdict in verdicts:
        counts[verdict.status] += 1
    return counts


# ---- Internals ------------------------------------------------------------- #


def _span(seq, start: int, end: int):
    """Slice ``seq`` with an exclusive end, as the API's indices are defined.

    Kept as its own function so the mutation test can swap in an inclusive-end
    slice and prove the suite notices.
    """
    return seq[start:end]


def _cited_text_blocks(content: Sequence[Mapping]) -> list[tuple[int, list]]:
    found: list[tuple[int, list]] = []
    for index, block in enumerate(content):
        where = f"content[{index}]"
        if not isinstance(block, Mapping) or "type" not in block:
            raise ValueError(f"{where} is not a mapping with a 'type'")
        if block["type"] != _TEXT_BLOCK:
            continue
        if not isinstance(block.get("text"), str):
            raise ValueError(f"{where} is a text block without a str 'text'")
        citations = block.get("citations")
        if citations is None:
            citations = []
        if not isinstance(citations, list):
            raise ValueError(f"{where}.citations must be a list or null")
        found.append((index, citations))
    return found


def _parse(raw: object, where: str) -> _Span | _Page | _Unknown:
    if not isinstance(raw, Mapping):
        raise ValueError(f"{where} is not a mapping")
    if "type" not in raw:
        raise ValueError(f"{where} is missing required field 'type'")
    kind = raw["type"]
    if kind in _SPAN_FIELDS:
        start_field, end_field = _SPAN_FIELDS[kind]
        return _Span(
            kind=kind,
            document_index=_int(raw, "document_index", where),
            start=_int(raw, start_field, where),
            end=_int(raw, end_field, where),
            cited_text=_str(raw, "cited_text", where),
        )
    if kind == _PAGE:
        _int(raw, "start_page_number", where)
        _int(raw, "end_page_number", where)
        _str(raw, "cited_text", where)
        return _Page(document_index=_int(raw, "document_index", where))
    return _Unknown(type_name=str(kind))


def _judge(sources: Sequence[Source], citation: _Span | _Page | _Unknown) -> tuple[Status, str]:
    if isinstance(citation, _Unknown):
        return Status.UNVERIFIABLE, f"citation type {citation.type_name!r} is not checked offline"
    n = len(sources)
    if not 0 <= citation.document_index < n:
        return Status.BAD_DOCUMENT_INDEX, f"document_index {citation.document_index} not in [0, {n})"
    if isinstance(citation, _Page):
        return Status.UNVERIFIABLE, "page_location needs a PDF parser"
    source = sources[citation.document_index]
    if isinstance(source, Opaque):
        return Status.UNVERIFIABLE, source.reason
    if citation.kind == _CHAR and isinstance(source, PlainText):
        return _compare(source.text, citation, joined=False)
    if citation.kind == _CONTENT_BLOCK and isinstance(source, CustomContent):
        return _compare(source.blocks, citation, joined=True)
    return (
        Status.BAD_DOCUMENT_INDEX,
        f"{citation.kind} points at document {citation.document_index}, "
        f"which is {type(source).__name__}",
    )


def _compare(seq: str | tuple[str, ...], citation: _Span, *, joined: bool) -> tuple[Status, str]:
    start, end = citation.start, citation.end
    if not 0 <= start < end <= len(seq):
        return Status.OUT_OF_RANGE, f"[{start}, {end}) not a non-empty range within [0, {len(seq)}]"
    piece = _span(seq, start, end)
    expected = "".join(piece) if joined else piece
    if expected == citation.cited_text:
        return Status.MATCH, f"[{start}, {end}) = {_clip(expected)}"
    return Status.MISMATCH, f"[{start}, {end}) = {_clip(expected)}, cited {_clip(citation.cited_text)}"


def _int(raw: Mapping, field: str, where: str) -> int:
    if field not in raw:
        raise ValueError(f"{where} is missing required field {field!r}")
    value = raw[field]
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where}.{field} must be an int, got {type(value).__name__}")
    return value


def _str(raw: Mapping, field: str, where: str) -> str:
    if field not in raw:
        raise ValueError(f"{where} is missing required field {field!r}")
    value = raw[field]
    if not isinstance(value, str):
        raise ValueError(f"{where}.{field} must be a str, got {type(value).__name__}")
    return value


def _clip(text: str, limit: int = 60) -> str:
    """repr() of ``text``, middle elided past ``limit`` chars; both ends survive so trailing whitespace stays visible."""
    if len(text) <= limit:
        return repr(text)
    half = limit // 2
    return f"{text[:half]!r}...{text[-half:]!r}"
