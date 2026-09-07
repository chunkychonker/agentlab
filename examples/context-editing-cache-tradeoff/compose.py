"""Composes a `clear_tool_uses_20250919` edit with clearing-aware
`cache_control` placement for one growing tool-loop message list.

Pure: no `anthropic` import, no I/O, no env reads, no clock. The only thing this
module knows about the network is the *shape* of two JSON objects.

Layer 1 (intent). `examples/prompt-caching-tool-loop/placement.py` anchors its
second `messages` breakpoint on `messages[0]`, which is correct while the whole
history is byte-stable. Turn on `clear_tool_uses` and it stops being correct:
`messages[0]` survives the clear, but everything between it and the `keep`
boundary is rewritten to placeholder text on every clearing turn, so an anchor
at the head spans a region that changes each time - a guaranteed miss and a
wasted breakpoint. The fix is to anchor on the first message that *survives*
clearing. Out of scope: the tools and system breakpoints (static, set once at
the entry point), `clear_thinking_20251015`, `compact_20260112`, the
`input_tokens` trigger form, `clear_at_least`, `exclude_tools`, and the 1-hour
TTL.

Layer 2 (spec), enforced below and asserted in `test_compose.py`:

  - `clearing_boundary` is the index of the first message that survives a clear
    with this `keep`: the message holding the `keep`-th-from-last `tool_use`
    block. `0` when there are no more tool uses than `keep` (nothing clears, so
    every message survives, starting at the head); `len(messages)` when `keep`
    is 0 and there is at least one tool use (no message survives intact, so
    there is no survivor to anchor on).
  - `place_breakpoints_for_clearing` keeps the parent's rolling marker on the
    last block of `messages[-1]` and moves the anchor from `messages[0]` to
    `messages[clearing_boundary(...)]`. The anchor is skipped when the boundary
    is `len(messages)` and when it collides with the rolling marker; it lands on
    `messages[0]` - the parent's behaviour exactly - when the boundary is 0.
  - The result is a deep copy; the input is never mutated. Every `cache_control`
    already on the input is stripped from the copy first, so re-running over the
    same list, or over one that grew between calls, yields exactly the markers
    this policy placed and no others.
  - `clearing_config` renders the `tool_uses`-triggered edit and nothing else.

See the research note this came from:
    research/2026-09-07-context-editing-cache-tradeoff.md
"""

from __future__ import annotations

import copy
import dataclasses
from collections.abc import Mapping, Sequence

# The only `cache_control` value this example uses. `{"type": "ephemeral",
# "ttl": "1h"}` is the other documented form (2x write instead of 1.25x); it is
# out of scope, so it is not spellable here.
EPHEMERAL: dict[str, str] = {"type": "ephemeral"}

# Documented cap: at most four `cache_control` breakpoints per request, across
# `tools`, `system` and `messages` together.
MAX_BREAKPOINTS = 4

# Documented lookback: from each breakpoint the system checks at most 20
# content-block positions backward for a usable cache entry. Past that, a single
# tail breakpoint can no longer see the head of the conversation, which is when
# a second one starts paying for itself.
LOOKBACK_BLOCKS = 20

# The one context-editing strategy this example covers, and the beta it ships
# behind. Both are literal members of the SDK's parameter unions.
STRATEGY = "clear_tool_uses_20250919"
BETA = "context-management-2025-06-27"

# The API measures both `trigger` (in this example) and `keep` (always) in tool
# uses. The `input_tokens` trigger form defaults to 100k and would never fire on
# a demo transcript, so it is out of scope and not spellable here.
TRIGGER_KIND = "tool_uses"
KEEP_KIND = "tool_uses"

# Key names the API owns. Named once so a typo is a diff, not a silent no-op: an
# unrecognised key on a content block is simply not a breakpoint.
CACHE_CONTROL_KEY = "cache_control"
TEXT_BLOCK_TYPE = "text"
TOOL_USE_BLOCK_TYPE = "tool_use"

_ROLE_KEY = "role"
_CONTENT_KEY = "content"
_TYPE_KEY = "type"


@dataclasses.dataclass(frozen=True)
class Placement:
    """A message list with breakpoints in it, and how many were placed.

    `messages` is a deep copy of the input, safe to hand straight to
    `messages.create`. `marker_count` counts the blocks this policy marked in
    the `messages` array - never the caller's static tools and system
    breakpoints, which is exactly why `budget` is a parameter - and it counts
    *distinct* marked blocks, so it can never overstate what the list carries.
    """

    messages: list[dict]
    marker_count: int


def clearing_boundary(
    messages: Sequence[Mapping[str, object]], *, keep_tool_uses: int
) -> int:
    """Index of the first message that survives a clear with this `keep`.

    `clear_tool_uses_20250919` empties the `tool_result` bodies of every tool
    use except the most recent `keep_tool_uses`, so the first message that comes
    through a clearing turn byte-identical is the one holding the
    `keep_tool_uses`-th-from-last `tool_use` block. Everything above it is
    rewritten to placeholder text each time the edit fires; everything from it
    down is stable, which makes it the only sound place for a cache anchor.

    Two ends of the range are named rather than derived, in this order:

      - No more tool uses than `keep_tool_uses` (including a transcript with no
        tool use at all): nothing would be cleared, so every message survives
        and the boundary is the head, `0`.
      - `keep_tool_uses == 0` with at least one tool use: every result is
        cleared, so no message survives intact. The boundary is `len(messages)`
        - one past the end, the caller's signal that there is nothing to anchor.

    Pure. Failure modes, all raised before anything is scanned: `TypeError` if
    `keep_tool_uses` is not an `int`, if a message is not a mapping, if a
    message lacks `role` or `content`, if `content` is neither `str` nor `list`,
    or if a content-list element is not a mapping; `ValueError` if
    `keep_tool_uses` is negative, if a content list is empty, or if a content
    block has no `"type"` key.
    """
    _validate_keep(keep_tool_uses)
    for index, message in enumerate(messages):
        _validate_message(message, index)

    positions = _tool_use_positions(messages)
    if len(positions) <= keep_tool_uses:
        return 0
    if keep_tool_uses == 0:
        return len(messages)
    return positions[-keep_tool_uses]


def place_breakpoints_for_clearing(
    messages: Sequence[Mapping[str, object]],
    *,
    budget: int,
    keep_tool_uses: int,
) -> Placement:
    """Return `messages` deep-copied with up to `budget` clearing-aware
    breakpoints inserted.

    Pure. `budget` is the number of the four total breakpoints left for the
    message array: a caller that marked its last tool and its last system block
    passes `MAX_BREAKPOINTS - 2`. Those static markers live outside `messages`
    and are untouched; any `cache_control` *inside* the message list is this
    function's to place, so pre-existing ones are cleared from the copy first.

    Two markers, at most:

      - the rolling marker on the last block of `messages[-1]`, whenever
        `budget >= 1` and the list is non-empty - the frozen tail this request
        writes and the next one reads;
      - the anchor on the last block of `messages[clearing_boundary(...)]`,
        whenever `budget >= 2` and the block count exceeds `LOOKBACK_BLOCKS`.
        Below that window the rolling marker still chains turn-to-turn on its
        own and a second breakpoint buys nothing.

    The anchor is skipped in the two cases where it would not be a second
    breakpoint at all: a boundary of `len(messages)` (nothing survives the
    clear), and a boundary that lands on the rolling marker's own message
    (marking one block twice is one breakpoint, and `marker_count` must not say
    two). The second case subsumes the parent's `len(messages) >= 2` guard.

    Failure modes: those of `clearing_boundary`, plus `TypeError` if `budget` is
    not an `int` and `ValueError` if it is negative. All raised before anything
    is copied.
    """
    _validate_budget(budget)
    boundary = clearing_boundary(messages, keep_tool_uses=keep_tool_uses)

    copied = [_validated_copy(message, index) for index, message in enumerate(messages)]

    # Idempotence over a list that GROWS between calls: the tail moves each turn
    # and so does the clearing boundary, so markers left where they were would
    # survive and accumulate against a cap of four. Cleared before the budget
    # check, so `marker_count == 0` is never a lie about what the list carries.
    _clear_markers(copied)

    available = min(budget, MAX_BREAKPOINTS)
    if available <= 0 or not copied:
        return Placement(messages=copied, marker_count=0)

    rolling = len(copied) - 1
    targets = [rolling]

    if available >= 2 and _count_blocks(copied) > LOOKBACK_BLOCKS:
        if boundary < len(copied) and boundary != rolling:
            targets.append(boundary)

    for index in targets:
        blocks = _normalize_content(copied[index][_CONTENT_KEY])
        _mark_last_block(blocks)
        copied[index][_CONTENT_KEY] = blocks

    return Placement(messages=copied, marker_count=len(targets))


def clearing_config(*, keep_tool_uses: int, trigger_tool_uses: int) -> dict:
    """The `context_management` value for a `count_tokens` or `create` call.

    A local minimal serialiser rather than an import of
    `examples/context-editing-preview/policy.py`: each example in this repo is a
    self-contained directory with its own pin, and a cross-example import would
    couple two of them at the filesystem level. This renders the one edit shape
    this demo sends - the `tool_uses` trigger, no `clear_at_least`, no
    `exclude_tools` - and nothing else.

    Pure. Failure modes: `TypeError` if either value is not an `int`;
    `ValueError` if either is `< 1`. `keep_tool_uses == 0` is legal for
    `clearing_boundary`, which has to answer for any list it is handed, but it
    is not a policy this demo sends: clearing every result including the live
    tail is a different experiment.
    """
    _validate_edit_value("keep_tool_uses", keep_tool_uses)
    _validate_edit_value("trigger_tool_uses", trigger_tool_uses)
    return {
        "edits": [
            {
                "type": STRATEGY,
                "trigger": {"type": TRIGGER_KIND, "value": trigger_tool_uses},
                "keep": {"type": KEEP_KIND, "value": keep_tool_uses},
            }
        ]
    }


def _validate_keep(keep_tool_uses: int) -> None:
    """Reject a `keep` that is not a non-negative `int`.

    `bool` is rejected too: `keep_tool_uses=True` reads like a flag and means
    "keep one", which is a coincidence, not an intent.
    """
    if isinstance(keep_tool_uses, bool) or not isinstance(keep_tool_uses, int):
        raise TypeError(
            f"keep_tool_uses must be an int, got {type(keep_tool_uses).__name__}"
        )
    if keep_tool_uses < 0:
        raise ValueError(f"keep_tool_uses must be >= 0, got {keep_tool_uses}")


def _validate_budget(budget: int) -> None:
    """Reject a budget that is not a non-negative `int`. `bool` is rejected too."""
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise TypeError(f"budget must be an int, got {type(budget).__name__}")
    if budget < 0:
        raise ValueError(f"budget must be >= 0, got {budget}")


def _validate_edit_value(name: str, value: int) -> None:
    """Reject an edit field that is not an `int` >= 1."""
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{name} must be an int, got {type(value).__name__}")
    if value < 1:
        raise ValueError(f"{name} must be >= 1, got {value}")


def _validate_message(message: object, index: int) -> None:
    """Validate one message at the boundary.

    Everything downstream assumes a message that got through here: a mapping
    with a `role`, and a `content` that is either a `str` or a non-empty list of
    typed blocks. Failure modes are those listed on `clearing_boundary`.
    """
    if not isinstance(message, Mapping):
        raise TypeError(
            f"messages[{index}] must be a mapping, got {type(message).__name__}"
        )
    for key in (_ROLE_KEY, _CONTENT_KEY):
        if key not in message:
            raise TypeError(f"messages[{index}] has no {key!r} key")

    content = message[_CONTENT_KEY]
    if isinstance(content, list):
        if not content:
            raise ValueError(
                f"messages[{index}][{_CONTENT_KEY!r}] is an empty list: there is "
                "no last block to mark, and the API rejects empty content"
            )
        for position, block in enumerate(content):
            if not isinstance(block, Mapping):
                raise TypeError(
                    f"messages[{index}][{_CONTENT_KEY!r}][{position}] must be a "
                    f"mapping, got {type(block).__name__}"
                )
            if _TYPE_KEY not in block:
                raise ValueError(
                    f"messages[{index}][{_CONTENT_KEY!r}][{position}] has no "
                    f"{_TYPE_KEY!r} key, so it is not a content block"
                )
    elif not isinstance(content, str):
        raise TypeError(
            f"messages[{index}][{_CONTENT_KEY!r}] must be a str or a list, got "
            f"{type(content).__name__}"
        )


def _validated_copy(message: object, index: int) -> dict:
    """Validate one message and return a deep copy of it.

    Failure modes: those of `_validate_message`.
    """
    _validate_message(message, index)
    return copy.deepcopy(dict(message))  # type: ignore[arg-type]


def _tool_use_positions(messages: Sequence[Mapping[str, object]]) -> list[int]:
    """The message index of every `tool_use` block, in order, one entry per block.

    A message holding two `tool_use` blocks contributes its index twice, because
    the API counts tool *uses*, not messages - so a `keep` of 2 against one
    parallel-call message keeps both of that message's uses. Assumes validated
    input; no failure modes of its own.
    """
    positions: list[int] = []
    for index, message in enumerate(messages):
        content = message[_CONTENT_KEY]
        if isinstance(content, str):
            continue  # a bare string cannot hold a tool_use block
        for block in content:  # type: ignore[union-attr]
            if block.get(_TYPE_KEY) == TOOL_USE_BLOCK_TYPE:
                positions.append(index)
    return positions


def _clear_markers(messages: list[dict]) -> None:
    """Remove every `cache_control` marker already present on `messages`, in place.

    Runs on the deep copy, never on the caller's list. Assumes validated input;
    no failure modes of its own.
    """
    for message in messages:
        content = message[_CONTENT_KEY]
        if isinstance(content, str):
            continue  # a bare string has nowhere to carry a marker
        for position, block in enumerate(content):
            if CACHE_CONTROL_KEY in block:
                content[position] = {
                    key: value
                    for key, value in block.items()
                    if key != CACHE_CONTROL_KEY
                }


def _normalize_content(content: object) -> list[dict]:
    """Return `content` as a list of block dicts, ready for a marker.

    A bare `str` becomes one text block, because `cache_control` attaches to a
    block and a string has nowhere to put it. Only messages that are about to be
    marked go through here, so an untouched message keeps the exact `content`
    object it arrived with. Assumes validated input; no failure modes of its own.
    """
    if isinstance(content, str):
        return [{_TYPE_KEY: TEXT_BLOCK_TYPE, "text": content}]
    return [dict(block) for block in content]  # type: ignore[union-attr]


def _mark_last_block(blocks: list[dict]) -> None:
    """Attach a fresh `EPHEMERAL` dict to the last block, in place.

    A copy, not `EPHEMERAL` itself, so no returned message can alias the module
    constant. Assumes a non-empty list (guaranteed by `_validate_message`).
    """
    blocks[-1][CACHE_CONTROL_KEY] = dict(EPHEMERAL)


def _count_blocks(messages: Sequence[Mapping[str, object]]) -> int:
    """Total content blocks across `messages`; a `str` content counts as one.

    This is the number the 20-block lookback window is measured in. Assumes
    validated input; no failure modes of its own.
    """
    total = 0
    for message in messages:
        content = message[_CONTENT_KEY]
        total += 1 if isinstance(content, str) else len(content)  # type: ignore[arg-type]
    return total
