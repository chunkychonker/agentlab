"""Reconstruct a complete message from a raw Anthropic stream event sequence.

Pure: no I/O, no network, no clock, no env. It consumes an iterable of raw
stream events and returns a value, so it is testable with a plain list of
``SimpleNamespace`` fakes (see ``test_agent.py``).

The one thing streaming changes about tool use: a ``tool_use`` block's ``input``
does not arrive as a value. ``content_block_start`` opens it as an *empty dict*,
and each following ``content_block_delta`` carries ``partial_json`` — a raw
string fragment of JSON. Only the concatenation of every fragment for that index,
parsed at that index's ``content_block_stop``, is the real input.

Extended thinking adds two more block types to assemble. A ``thinking`` block
arrives as ``thinking_delta`` string fragments plus a trailing ``signature_delta``
carrying the encrypted signature; a ``redacted_thinking`` block arrives complete
in its ``content_block_start`` with no deltas at all. Both must come back out in
wire shape so the loop can echo them into the next turn unmodified — a tool-use
turn that drops or edits them is a 400.

See the research notes this came from:
    research/2026-08-16-streaming-tool-loop.md          (text / tool_use)
    research/2026-09-02-streaming-thinking-accumulator.md  (thinking blocks)
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Iterable

# ---- Event / delta / block type names, defined once ------------------------ #
# Magic strings are coupling-by-meaning; agent.py imports these rather than
# re-typing the literals.

MESSAGE_START = "message_start"
CONTENT_BLOCK_START = "content_block_start"
CONTENT_BLOCK_DELTA = "content_block_delta"
CONTENT_BLOCK_STOP = "content_block_stop"
MESSAGE_DELTA = "message_delta"
MESSAGE_STOP = "message_stop"
PING = "ping"

TEXT_DELTA = "text_delta"
INPUT_JSON_DELTA = "input_json_delta"
THINKING_DELTA = "thinking_delta"
SIGNATURE_DELTA = "signature_delta"

TEXT_BLOCK = "text"
TOOL_USE_BLOCK = "tool_use"
THINKING_BLOCK = "thinking"
REDACTED_THINKING_BLOCK = "redacted_thinking"

# Events that are recognized but contribute nothing to the reconstructed blocks.
# ``message_start`` carries a Message whose ``content`` is always empty, and
# ``message_stop``/``ping`` carry no payload at all.
_IGNORED_EVENT_TYPES = frozenset({MESSAGE_START, MESSAGE_STOP, PING})


@dataclass
class AccumulatedMessage:
    """A finished message rebuilt from a stream.

    ``content`` is in index order; each block is one of
    ``{"type": "text", "text": str}``,
    ``{"type": "tool_use", "id": str, "name": str, "input": dict}``,
    ``{"type": "thinking", "thinking": str, "signature": str}``, or
    ``{"type": "redacted_thinking", "data": str}`` — the same wire shape the
    Messages API accepts when echoing an assistant turn back.
    """

    stop_reason: str | None
    content: list[dict]


# --------------------------------------------------------------------------- #
# Open-block builders
#
# A block is open (a builder) or finished (a dict) — never both, and never
# neither, so "delta for a block that was never started" is a lookup miss rather
# than a state flag someone has to remember to check.
# --------------------------------------------------------------------------- #

@dataclass
class _OpenText:
    """A text block still receiving ``text_delta`` fragments."""

    text: str

    def apply(self, index: int, delta) -> None:
        """Append one delta's text. Raises ValueError if it is not a text_delta."""
        if getattr(delta, "type", None) != TEXT_DELTA:
            raise ValueError(
                f"content block {index}: text block received "
                f"{getattr(delta, 'type', None)!r} delta, expected {TEXT_DELTA!r}"
            )
        self.text += delta.text

    def finish(self, index: int) -> dict:
        """Return the finished text block. Never fails."""
        return {"type": TEXT_BLOCK, "text": self.text}


@dataclass
class _OpenToolUse:
    """A tool_use block still receiving ``input_json_delta`` fragments.

    Fragments are held as raw strings and parsed only in ``finish`` — partial
    JSON can be invalid JSON at any point before the block closes.
    """

    id: str
    name: str
    fragments: list[str] = field(default_factory=list)

    def apply(self, index: int, delta) -> None:
        """Record one partial_json fragment.

        Raises ValueError if the delta is not an ``input_json_delta``.
        """
        if getattr(delta, "type", None) != INPUT_JSON_DELTA:
            raise ValueError(
                f"content block {index}: tool_use block received "
                f"{getattr(delta, 'type', None)!r} delta, expected {INPUT_JSON_DELTA!r}"
            )
        self.fragments.append(delta.partial_json)

    def finish(self, index: int) -> dict:
        """Parse the concatenated fragments into the tool input block.

        Failure modes:
          - ``json.JSONDecodeError`` if the concatenation is not valid JSON —
            including the empty string, which is what a truncated stream leaves
            behind. It is never swallowed into ``{}``: a tool must not be called
            with silently-wrong input.
          - ``ValueError`` if the JSON parses to something other than an object,
            since ``input`` must be a dict to be splatted into a tool.
        """
        raw = "".join(self.fragments)
        try:
            parsed = json.loads(raw)
        except json.JSONDecodeError as exc:
            # Re-raise the same type (callers catch JSONDecodeError) but name the
            # block, so a truncated stream points at the offending index.
            raise json.JSONDecodeError(
                f"content block {index} (tool_use {self.name!r}): {exc.msg}",
                exc.doc,
                exc.pos,
            ) from exc
        if not isinstance(parsed, dict):
            raise ValueError(
                f"content block {index}: tool_use input parsed to "
                f"{type(parsed).__name__}, expected object"
            )
        return {"type": TOOL_USE_BLOCK, "id": self.id, "name": self.name, "input": parsed}


@dataclass
class _OpenThinking:
    """A thinking block still receiving ``thinking_delta``/``signature_delta`` fragments.

    Both kinds of fragment are held as strings and joined in ``finish``. The
    ``thinking`` and ``signature`` fields on the opening ``content_block_start``
    are always ``""`` (the same "empty, not absent" seed ``tool_use`` uses for
    ``input``), so nothing is read from them — exactly as ``_OpenToolUse``
    ignores the opening ``input={}``.

    Failure modes:
      - ``ValueError`` naming the index if ``apply`` gets a delta whose type is
        neither ``thinking_delta`` nor ``signature_delta``.
    ``finish`` never fails: both fields are plain string joins.
    """

    thinking_parts: list[str] = field(default_factory=list)
    signature_parts: list[str] = field(default_factory=list)

    def apply(self, index: int, delta) -> None:
        """Record one thinking or signature fragment.

        Raises ValueError if the delta is neither a ``thinking_delta`` nor a
        ``signature_delta`` — including a ``text_delta`` or ``input_json_delta``
        misrouted to a thinking index.
        """
        delta_type = getattr(delta, "type", None)
        if delta_type == THINKING_DELTA:
            self.thinking_parts.append(delta.thinking)
            return
        if delta_type == SIGNATURE_DELTA:
            self.signature_parts.append(delta.signature)
            return
        raise ValueError(
            f"content block {index}: thinking block received {delta_type!r} delta, "
            f"expected {THINKING_DELTA!r} or {SIGNATURE_DELTA!r}"
        )

    def finish(self, index: int) -> dict:
        """Return the finished thinking block. Never fails.

        Both joins can legitimately produce ``""``. Zero ``thinking_delta``s is
        the documented ``display:"omitted"`` case, *not* the truncation an empty
        ``tool_use`` input signals. Zero ``signature_delta``s yields
        ``signature: ""`` and is passed through rather than raised on: a pure
        accumulator cannot know the model or display mode, so the loud failure
        for a genuinely missing signature belongs at the API boundary, which
        rejects it. See the README's signature-policy table.
        """
        return {
            "type": THINKING_BLOCK,
            "thinking": "".join(self.thinking_parts),
            "signature": "".join(self.signature_parts),
        }


@dataclass
class _OpenRedactedThinking:
    """A redacted_thinking block: opaque ``data``, captured whole at its start.

    Unlike every other block type this carries no deltas — the stream sends
    ``content_block_start`` with the full ``data`` and then ``content_block_stop``.

    Failure modes:
      - ``ValueError`` naming the index from ``apply`` for *any* delta.
      - ``ValueError`` at construction (in ``_open_block``) if the opening
        ``content_block`` has no string ``data``.
    ``finish`` never fails.
    """

    data: str

    def apply(self, index: int, delta) -> None:
        """Always raises ValueError: a redacted_thinking block has no deltas."""
        raise ValueError(
            f"content block {index}: {REDACTED_THINKING_BLOCK} block received a "
            f"{getattr(delta, 'type', None)!r} delta, but it takes no deltas"
        )

    def finish(self, index: int) -> dict:
        """Return the finished redacted_thinking block. Never fails."""
        return {"type": REDACTED_THINKING_BLOCK, "data": self.data}


# Every block type the accumulator can hold open. Named once so the union does
# not have to be re-typed at each use site.
_OpenBlock = _OpenText | _OpenToolUse | _OpenThinking | _OpenRedactedThinking


def _open_block(index: int, content_block) -> _OpenBlock:
    """Build the right open-block accumulator for a content_block_start.

    Raises ValueError for block types this example does not handle (e.g.
    ``server_tool_use``) rather than dropping them silently, and for a
    ``redacted_thinking`` start with no string ``data`` to round-trip.
    """
    block_type = getattr(content_block, "type", None)
    if block_type == TEXT_BLOCK:
        return _OpenText(text=content_block.text)
    if block_type == TOOL_USE_BLOCK:
        return _OpenToolUse(id=content_block.id, name=content_block.name)
    if block_type == THINKING_BLOCK:
        # The start's own thinking/signature are always ""; the content arrives
        # entirely as deltas.
        return _OpenThinking()
    if block_type == REDACTED_THINKING_BLOCK:
        data = getattr(content_block, "data", None)
        if not isinstance(data, str):
            raise ValueError(
                f"content block {index}: {REDACTED_THINKING_BLOCK} start carries no "
                f"string 'data' field (got {data!r}); it cannot be round-tripped"
            )
        return _OpenRedactedThinking(data=data)
    raise ValueError(f"content block {index}: unsupported block type {block_type!r}")


# --------------------------------------------------------------------------- #
# The accumulator
# --------------------------------------------------------------------------- #

def accumulate(events: Iterable[object]) -> AccumulatedMessage:
    """Consume a raw stream event sequence and return the reconstructed message.

    ``events`` may be a live ``client.messages.stream()`` (iterating it yields
    raw typed events) or any iterable of objects with the same attributes.

    ``ping`` and any event whose ``.type`` is not one of the six known types are
    ignored, per the docs' forward-compatibility note. ``stop_reason`` is taken
    from the last ``message_delta`` that carried one.

    Failure modes (all loud — nothing is silently dropped or defaulted):
      - ``ValueError`` naming the index for a delta or stop on an index that was
        never started, a second start on an index that is still open or already
        finished, a delta whose type does not match its block, an unsupported
        block type, a block left unclosed when the stream ends, or a gap in the
        block indices (which would break index-to-position correspondence).
      - ``ValueError`` naming the index for a ``thinking_delta``/``signature_delta``
        on an index that is not an open ``thinking`` block, any delta on a
        ``redacted_thinking`` index, or a ``redacted_thinking`` start with no
        string ``data``.
      - ``json.JSONDecodeError`` if a tool_use block's accumulated
        ``partial_json`` is not valid JSON at its ``content_block_stop``.

    Deliberately *not* failures: a ``thinking`` block that closes having received
    zero ``thinking_delta``s (the ``display:"omitted"`` case) or zero
    ``signature_delta``s. Both yield ``""`` for that field.
    """
    open_blocks: dict[int, _OpenBlock] = {}
    finished: dict[int, dict] = {}
    stop_reason: str | None = None

    for event in events:
        event_type = getattr(event, "type", None)

        if event_type == CONTENT_BLOCK_START:
            index = event.index
            if index in open_blocks:
                raise ValueError(f"content block {index}: started twice without a stop")
            if index in finished:
                raise ValueError(f"content block {index}: started again after it stopped")
            open_blocks[index] = _open_block(index, event.content_block)

        elif event_type == CONTENT_BLOCK_DELTA:
            index = event.index
            block = open_blocks.get(index)
            if block is None:
                raise ValueError(f"content block {index}: delta for a block that is not open")
            block.apply(index, event.delta)

        elif event_type == CONTENT_BLOCK_STOP:
            index = event.index
            block = open_blocks.pop(index, None)
            if block is None:
                raise ValueError(f"content block {index}: stop for a block that is not open")
            finished[index] = block.finish(index)

        elif event_type == MESSAGE_DELTA:
            # Top-level changes; earlier message_deltas can carry a null
            # stop_reason, so only a real one overwrites what we have.
            reason = getattr(event.delta, "stop_reason", None)
            if reason is not None:
                stop_reason = reason

        elif event_type in _IGNORED_EVENT_TYPES:
            continue

        else:
            # Forward-compat: unknown event types are ignored, not errors.
            continue

    if open_blocks:
        raise ValueError(
            f"stream ended with unclosed content block(s): {sorted(open_blocks)}"
        )
    if sorted(finished) != list(range(len(finished))):
        raise ValueError(
            f"content block indices are not contiguous from 0: {sorted(finished)}"
        )

    return AccumulatedMessage(
        stop_reason=stop_reason,
        content=[finished[i] for i in range(len(finished))],
    )
