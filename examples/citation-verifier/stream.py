"""Rebuild cited text blocks from a Messages API stream's events.

Pure: no I/O, no SDK. Events are wire-shaped dicts (raw SSE JSON, or
``event.model_dump(exclude_none=True)`` from the SDK). Citations stream as a
``citations_delta`` per citation, appended to the open text block's
``citations`` list, interleaved with ``text_delta`` fragments.

Scope: text blocks only. A stream that opens any other block type (tool_use,
thinking, ...) is refused; ``examples/streaming-tool-loop/accumulator.py``
handles those.
"""

from __future__ import annotations

from typing import Iterable, Mapping

# Wire names, defined once.
_MESSAGE_START = "message_start"
_BLOCK_START = "content_block_start"
_BLOCK_DELTA = "content_block_delta"
_BLOCK_STOP = "content_block_stop"
_MESSAGE_DELTA = "message_delta"
_MESSAGE_STOP = "message_stop"
_PING = "ping"
_TEXT_DELTA = "text_delta"
_CITATIONS_DELTA = "citations_delta"
_TEXT_BLOCK = "text"

_IGNORED = frozenset({_MESSAGE_START, _MESSAGE_DELTA, _PING})


def accumulate(events: Iterable[Mapping]) -> list[dict]:
    """Return the finished text blocks, in index order, in non-streaming shape.

    Each block is ``{"type": "text", "text": str}`` plus ``"citations": [...]``
    when at least one citation arrived, the same as
    ``block.model_dump(exclude_none=True)`` of a non-streamed ``TextBlock``.

    Raises:
        ValueError: an event has no ``type`` or an unknown one (including the
            stream's ``error`` event); a block is started twice, or is not a
            text block; a delta or stop names an index that is not open; a
            delta has an unknown type or no payload; any event follows
            ``message_stop``; the stream ends without ``message_stop`` or with
            a block still open; the finished indices are not 0..n-1. There is
            no partial result: a truncated stream raises rather than returning
            what arrived so far.
    """
    open_blocks: dict[int, dict] = {}
    done: dict[int, dict] = {}
    stopped = False
    for position, event in enumerate(events):
        where = f"event {position}"
        if stopped:
            raise ValueError(f"{where}: event after message_stop")
        kind = _field(event, "type", where)
        if kind in _IGNORED:
            continue
        if kind == _MESSAGE_STOP:
            stopped = True
        elif kind == _BLOCK_START:
            index = _index(event, where)
            if index in open_blocks or index in done:
                raise ValueError(f"{where}: block {index} started twice")
            open_blocks[index] = _open(_field(event, "content_block", where), where)
        elif kind == _BLOCK_DELTA:
            _apply(_open_block(open_blocks, event, where), _field(event, "delta", where), where)
        elif kind == _BLOCK_STOP:
            index = _index(event, where)
            done[index] = _finish(_open_block(open_blocks, event, where))
            del open_blocks[index]
        else:
            raise ValueError(f"{where}: unexpected event type {kind!r}: {dict(event)!r}")
    if not stopped:
        raise ValueError("stream ended without message_stop (truncated)")
    if open_blocks:
        raise ValueError(f"stream stopped with blocks still open: {sorted(open_blocks)}")
    if sorted(done) != list(range(len(done))):
        raise ValueError(f"block indices are not contiguous from 0: {sorted(done)}")
    return [done[i] for i in range(len(done))]


def _open(block: object, where: str) -> dict:
    block_type = _field(block, "type", f"{where}.content_block")
    if block_type != _TEXT_BLOCK:
        raise ValueError(f"{where}: block type {block_type!r} is out of scope (text blocks only)")
    text = block.get("text", "")
    citations = block.get("citations") or []
    if not isinstance(text, str) or not isinstance(citations, list):
        raise ValueError(f"{where}: malformed text content_block {dict(block)!r}")
    return {"text": text, "citations": list(citations)}


def _apply(builder: dict, delta: object, where: str) -> None:
    delta_type = _field(delta, "type", f"{where}.delta")
    if delta_type == _TEXT_DELTA:
        text = _field(delta, "text", f"{where}.delta")
        if not isinstance(text, str):
            raise ValueError(f"{where}.delta.text must be a str")
        builder["text"] += text
    elif delta_type == _CITATIONS_DELTA:
        builder["citations"].append(_field(delta, "citation", f"{where}.delta"))
    else:
        raise ValueError(f"{where}: delta type {delta_type!r} is out of scope (text blocks only)")


def _finish(builder: dict) -> dict:
    block = {"type": _TEXT_BLOCK, "text": builder["text"]}
    if builder["citations"]:
        block["citations"] = builder["citations"]
    return block


def _open_block(open_blocks: dict[int, dict], event: Mapping, where: str) -> dict:
    index = _index(event, where)
    if index not in open_blocks:
        raise ValueError(f"{where}: block {index} is not open")
    return open_blocks[index]


def _index(event: Mapping, where: str) -> int:
    index = _field(event, "index", where)
    if isinstance(index, bool) or not isinstance(index, int):
        raise ValueError(f"{where}.index must be an int")
    return index


def _field(mapping: object, key: str, where: str) -> object:
    if not isinstance(mapping, Mapping):
        raise ValueError(f"{where} is not a mapping")
    if key not in mapping:
        raise ValueError(f"{where} is missing required field {key!r}")
    return mapping[key]
