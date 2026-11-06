"""Turn a request's document blocks into the typed sources a citation can point at.

Pure: no I/O, no SDK. Input is the ``messages`` list exactly as it was sent to
``messages.create`` (plain dicts). Output is one ``Source`` per top-level
``document`` block, in request order, which is the order ``document_index``
counts in (0-indexed across all documents in the request, spanning all
messages).

Why typed sources instead of ``str | Sequence[str]``: a ``str`` is itself a
``Sequence[str]``, so the plain union cannot tell a plain-text document from a
custom-content document of one-character blocks. Three distinct types make
that confusion unrepresentable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Sequence

# Wire names, defined once.
_DOCUMENT_BLOCK = "document"
_TOOL_RESULT_BLOCK = "tool_result"
_TEXT_SOURCE = "text"
_CONTENT_SOURCE = "content"
_PDF_LIKE_SOURCES = frozenset({"base64", "url", "file"})
_TEXT_BLOCK = "text"


@dataclass(frozen=True)
class PlainText:
    """A ``{"type": "text", "media_type": "text/plain"}`` source. Cited by ``char_location``."""

    text: str

    def __post_init__(self) -> None:
        if not isinstance(self.text, str):
            raise TypeError(f"PlainText.text must be str, got {type(self.text).__name__}")


@dataclass(frozen=True)
class CustomContent:
    """A ``{"type": "content"}`` source of text blocks. Cited by ``content_block_location``."""

    blocks: tuple[str, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.blocks, tuple) or not all(isinstance(b, str) for b in self.blocks):
            raise TypeError("CustomContent.blocks must be a tuple of str")


@dataclass(frozen=True)
class Opaque:
    """A document whose text is not available offline (PDF, file, URL, unknown source).

    It still occupies a ``document_index`` slot. Every citation into it is
    reported UNVERIFIABLE with ``reason``.
    """

    reason: str


Source = PlainText | CustomContent | Opaque


def sources_from_messages(messages: Sequence[Mapping]) -> list[Source]:
    """Return one ``Source`` per top-level ``document`` block, in ``document_index`` order.

    Messages whose ``content`` is a plain string hold no documents and are skipped.

    Raises:
        ValueError: a message is not a mapping, its ``content`` is neither a str
            nor a list, a block has no ``type``, a document block has no
            ``source`` (or one without a ``type``), a plain-text source's
            ``data`` is not a str, or a ``tool_result`` contains a ``document``
            block. The last one is refused because whether nested documents
            share the ``document_index`` numbering is not documented, and a
            wrong guess would silently shift every later index.
        Nothing is returned on failure; there is no partial list.
    """
    sources: list[Source] = []
    for m_index, message in enumerate(messages):
        if not isinstance(message, Mapping):
            raise ValueError(f"messages[{m_index}] is not a mapping")
        content = message.get("content")
        if isinstance(content, str):
            continue
        if not isinstance(content, list):
            raise ValueError(f"messages[{m_index}].content must be a str or a list")
        for b_index, block in enumerate(content):
            where = f"messages[{m_index}].content[{b_index}]"
            block_type = _required(block, "type", where)
            if block_type == _DOCUMENT_BLOCK:
                sources.append(_source_of(block, where))
            elif block_type == _TOOL_RESULT_BLOCK:
                _refuse_nested_documents(block, where)
    return sources


def _source_of(block: Mapping, where: str) -> Source:
    source = _required(block, "source", where)
    source_type = _required(source, "type", f"{where}.source")
    if source_type == _TEXT_SOURCE:
        data = _required(source, "data", f"{where}.source")
        if not isinstance(data, str):
            raise ValueError(f"{where}.source.data must be a str")
        return PlainText(data)
    if source_type == _CONTENT_SOURCE:
        return _custom_content(_required(source, "content", f"{where}.source"))
    if source_type in _PDF_LIKE_SOURCES:
        return Opaque(f"{source_type!r} source: no offline text (PDF/file)")
    return Opaque(f"unknown source type {source_type!r}")


def _custom_content(content: object) -> Source:
    if isinstance(content, str):
        # The API accepts a bare string here; how it splits that into blocks is
        # not documented, so block indices cannot be re-derived.
        return Opaque("custom content given as a string: block boundaries unknown")
    if not isinstance(content, list):
        raise ValueError("custom content source must be a list of blocks or a str")
    texts: list[str] = []
    for block in content:
        if not isinstance(block, Mapping) or block.get("type") != _TEXT_BLOCK:
            return Opaque("custom content contains non-text blocks")
        text = block.get("text")
        if not isinstance(text, str):
            raise ValueError("custom content text block has no str 'text'")
        texts.append(text)
    return CustomContent(tuple(texts))


def _refuse_nested_documents(block: Mapping, where: str) -> None:
    inner = block.get("content")
    if isinstance(inner, list) and any(
        isinstance(b, Mapping) and b.get("type") == _DOCUMENT_BLOCK for b in inner
    ):
        raise ValueError(
            f"{where}: document inside tool_result; its document_index numbering is undocumented"
        )


def _required(mapping: object, key: str, where: str) -> object:
    if not isinstance(mapping, Mapping):
        raise ValueError(f"{where} is not a mapping")
    if key not in mapping:
        raise ValueError(f"{where} is missing required field {key!r}")
    return mapping[key]
