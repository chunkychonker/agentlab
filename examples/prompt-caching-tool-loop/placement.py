"""Inserts `cache_control` breakpoints into a tool-loop message list that grows
every turn.

Pure: no `anthropic` import, no I/O, no env reads, no clock. The only thing this
module knows about the network is the *shape* of one JSON key.

Layer 1 (intent). A hand-written tool loop appends an assistant message (with
`tool_use`) and a user message (with `tool_result`) on every iteration, then
calls the API again. The prefix that stays identical across those calls is the
part worth caching, so the marker has to move forward with the frozen tail.
Out of scope: the tools and system breakpoints (static, set once at the entry
point), automatic caching, and the 1-hour TTL.

Layer 2 (spec), enforced below and asserted in `test_placement.py`:

  - `budget` is how many of the four total breakpoints are free for the
    `messages` array. It is clamped to at most `MAX_BREAKPOINTS`.
  - Rolling marker on the last content block of `messages[-1]` whenever
    `budget >= 1` and there is at least one message. That block is frozen from
    the next turn onward, so this request writes it and the next one reads it.
  - Anchor marker on the last content block of `messages[0]`, only when
    `budget >= 2`, there are at least two messages, and the total block count
    exceeds `LOOKBACK_BLOCKS`. Below that the rolling marker still chains
    turn-to-turn on its own and a second breakpoint buys nothing.
  - The result is a deep copy; the input is never mutated.
  - This module owns `cache_control` within `messages`: every marker already on
    the input is stripped from the copy before placement. A marker a caller put
    there themselves - including the out-of-scope `ttl: "1h"` form - does not
    survive.
  - Idempotent: re-running yields exactly the markers this policy placed and no
    others, over the same list or over one that has grown between calls. The
    second case is the one that matters, and it is why the strip exists: the
    tail moves each turn, so without it the previous tail's marker orphans and
    an N-turn loop accumulates N breakpoints against a cap of four.

See the research note this came from:
    research/2026-08-29-prompt-caching-tool-loop.md
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
# content-block positions backward for a usable cache entry, counting the
# breakpoint itself as the first. Past that, a single tail breakpoint can no
# longer see the head of the conversation.
LOOKBACK_BLOCKS = 20

# Key names the API owns. Named once so a typo is a diff, not a silent no-op:
# an unrecognised key on a content block is simply not a breakpoint.
CACHE_CONTROL_KEY = "cache_control"
TEXT_BLOCK_TYPE = "text"

_ROLE_KEY = "role"
_CONTENT_KEY = "content"
_TYPE_KEY = "type"


@dataclasses.dataclass(frozen=True)
class Placement:
    """A message list with breakpoints in it, and how many were placed.

    `messages` is a deep copy of the input, safe to hand straight to
    `messages.create`. `marker_count` counts the blocks this policy marked in
    the `messages` array - it does not count the caller's static tools and
    system breakpoints, which is exactly why `budget` is a parameter.
    """

    messages: list[dict]
    marker_count: int


def place_breakpoints(
    messages: Sequence[Mapping[str, object]],
    *,
    budget: int = MAX_BREAKPOINTS,
) -> Placement:
    """Return `messages` deep-copied with up to `budget` breakpoints inserted.

    Pure. `budget` is the number of the four total breakpoints left for the
    message array: a caller that marked its last tool and its last system block
    passes `MAX_BREAKPOINTS - 2`. Those static tools and system markers live
    outside `messages` and are untouched; any `cache_control` *inside* the
    message list is this function's to place, so pre-existing ones are cleared
    from the copy first (see `_clear_markers`).

    Failure modes, all raised before anything is copied:
      - `ValueError` if `budget` is negative.
      - `TypeError` if `budget` is not an `int`, if a message is not a mapping,
        if a message lacks `role` or `content`, if `content` is neither `str`
        nor `list`, or if a content-list element is not a mapping.
      - `ValueError` if a content list is empty (there is no last block to mark,
        and the API rejects an empty content array), or if a content block has
        no `"type"` key.
    """
    _validate_budget(budget)
    copied = [_validated_copy(message, index) for index, message in enumerate(messages)]

    # Idempotence over a list that GROWS between calls: the tail moves each turn,
    # so a marker left on the previous tail would survive and accumulate. Cleared
    # before the budget check, so `marker_count == 0` is never a lie about what
    # the returned list actually carries.
    _clear_markers(copied)

    available = min(budget, MAX_BREAKPOINTS)
    if available <= 0 or not copied:
        return Placement(messages=copied, marker_count=0)

    # Rolling marker: the tail that is frozen from the next turn onward.
    targets = [len(copied) - 1]

    # Anchor marker: only once the history is longer than one breakpoint can see.
    if available >= 2 and len(copied) >= 2 and _count_blocks(copied) > LOOKBACK_BLOCKS:
        targets.append(0)

    for index in targets:
        blocks = _normalize_content(copied[index][_CONTENT_KEY])
        _mark_last_block(blocks)
        copied[index][_CONTENT_KEY] = blocks

    return Placement(messages=copied, marker_count=len(targets))


def _validate_budget(budget: int) -> None:
    """Reject a budget that is not a non-negative `int`.

    `bool` is rejected too: `place_breakpoints(m, budget=True)` reads like a
    flag and means "one breakpoint", which is a coincidence, not an intent.
    """
    if isinstance(budget, bool) or not isinstance(budget, int):
        raise TypeError(f"budget must be an int, got {type(budget).__name__}")
    if budget < 0:
        raise ValueError(f"budget must be >= 0, got {budget}")


def _validated_copy(message: object, index: int) -> dict:
    """Validate one message at the boundary and return a deep copy of it.

    Everything downstream assumes a message that got through here: a plain dict
    with a `role`, and a `content` that is either a `str` or a non-empty list of
    typed blocks. Failure modes are those listed on `place_breakpoints`.
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

    return copy.deepcopy(dict(message))


def _clear_markers(messages: list[dict]) -> None:
    """Remove every `cache_control` marker already present on `messages`, in place.

    Runs on the deep copy, never on the caller's list. This is what makes
    `place_breakpoints` idempotent over a list that grows between calls: without
    it, the marker on each previous tail survives and a five-turn loop sends five
    breakpoints where the API allows four.

    Assumes validated input (`_validated_copy`); no failure modes of its own.
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
    object it arrived with.

    Assumes validated input (`_validated_copy`); no failure modes of its own.
    """
    if isinstance(content, str):
        return [{_TYPE_KEY: TEXT_BLOCK_TYPE, "text": content}]
    return [dict(block) for block in content]  # type: ignore[union-attr]


def _mark_last_block(blocks: list[dict]) -> None:
    """Attach a fresh `EPHEMERAL` dict to the last block, in place.

    A copy, not `EPHEMERAL` itself, so no returned message can alias the module
    constant. Assumes a non-empty list (guaranteed by `_validated_copy`).
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
