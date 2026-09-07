"""Offline self-test for clearing-aware composition. No API key, no network, no
`anthropic` install.

Run:
    python3 test_compose.py

Each test maps to an acceptance criterion from the research note
(research/2026-09-07-context-editing-cache-tradeoff.md):

  C1. clearing_boundary        -> 10 pairs with keep=3 lands on messages[15];
                                  keep >= the tool-use count is 0 (nothing
                                  clears); keep=0 is len(messages) (nothing
                                  survives); parallel tool uses count per block
  C2. the anchor moves         -> past the 20-block window, budget=2 and keep=3
                                  give exactly two markers, and the anchor is on
                                  messages[clearing_boundary], not messages[0]
  C3. the degrade cases        -> boundary 0 falls back to the parent's head
                                  anchor; boundary len(messages) skips it; a
                                  boundary colliding with the rolling marker
                                  skips it
  C4. budget and window        -> below the lookback window, one marker only;
                                  budget 0/1/9 behave like the parent's
  C5. purity                   -> input unmutated, deep copy, idempotent over
                                  the same list and over one that grew
  C6. boundary failures        -> ValueError / TypeError, never a silent skip
  C7. clearing_config          -> dict-equal to the documented edit shape;
                                  keep=0 and trigger=0 rejected
  C8. why the rule exists      -> the region the anchor covers is byte-identical
                                  one round later, and sits inside what the next
                                  clear will have already flattened
  C9. the shell's static half  -> system and tools carry exactly one breakpoint
                                  each, all four fit the cap, and the grown
                                  transcript extends the original
"""

from __future__ import annotations

import copy
import sys
import time

import compose
import main
import transcript

# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #

# Small results: these tests count blocks and positions, never tokens.
_RESULT_CHARS = 40


def _rounds(count: int) -> list[dict]:
    """A tool loop of `count` complete round trips: `1 + 2 * count` messages."""
    return transcript.build_transcript(rounds=count, result_chars=_RESULT_CHARS)


def _marked_positions(messages: list[dict]) -> list[tuple[int, int]]:
    """Every `(message index, block index)` carrying a `cache_control` marker."""
    found = []
    for index, message in enumerate(messages):
        content = message[compose._CONTENT_KEY]
        if isinstance(content, str):
            continue
        for position, block in enumerate(content):
            if compose.CACHE_CONTROL_KEY in block:
                found.append((index, position))
    return found


def _rejects(exception_type, call, label: str) -> None:
    try:
        call()
    except exception_type:
        return
    raise AssertionError(f"{label} was accepted; expected {exception_type.__name__}")


# --------------------------------------------------------------------------- #
# C1: the boundary itself
# --------------------------------------------------------------------------- #


def test_ten_pairs_with_keep_three_survive_from_message_fifteen():
    """C1: the note's worked case - the 3rd-from-last tool use is round 7's."""
    messages = _rounds(10)

    assert len(messages) == 21, len(messages)
    assert compose.clearing_boundary(messages, keep_tool_uses=3) == 15
    # Round 7's assistant turn is messages[15], and it is the one holding the
    # third-from-last tool_use block.
    assert messages[15]["content"][1]["id"] == "toolu_0007"
    print("ok  10 pairs, keep=3 -> the survivor boundary is messages[15]")


def test_keeping_everything_leaves_the_boundary_at_the_head():
    """C1: nothing clears, so every message survives - starting at messages[0]."""
    messages = _rounds(10)

    for keep in (10, 11, 100):
        assert compose.clearing_boundary(messages, keep_tool_uses=keep) == 0, keep
    print("ok  keep >= the tool-use count leaves the boundary at 0")


def test_keeping_nothing_leaves_no_survivor_at_all():
    """C1: every result is cleared, so the boundary is one past the end."""
    messages = _rounds(10)

    assert compose.clearing_boundary(messages, keep_tool_uses=0) == 21
    print("ok  keep=0 puts the boundary at len(messages), the no-survivor signal")


def test_a_transcript_with_no_tool_use_never_clears():
    """C1: no tool uses means nothing to clear, even at keep=0 - not a sentinel."""
    messages = [
        {"role": "user", "content": "hello"},
        {"role": "assistant", "content": [{"type": "text", "text": "hi"}]},
    ]

    assert compose.clearing_boundary(messages, keep_tool_uses=0) == 0
    assert compose.clearing_boundary(messages, keep_tool_uses=3) == 0
    print("ok  a transcript with no tool use has its boundary at 0 for any keep")


def test_the_boundary_walks_back_one_round_per_kept_tool_use():
    """C1: keep=N is the N-th-from-last tool use, not the N-th-from-last message."""
    messages = _rounds(10)

    assert compose.clearing_boundary(messages, keep_tool_uses=1) == 19
    assert compose.clearing_boundary(messages, keep_tool_uses=2) == 17
    assert compose.clearing_boundary(messages, keep_tool_uses=3) == 15
    print("ok  each extra kept tool use moves the boundary back one round trip")


def test_parallel_tool_uses_in_one_message_count_once_each():
    """C1: the API counts tool *uses*, so two in one message consume two of keep."""
    messages = [
        {"role": "user", "content": "go"},
        {
            "role": "assistant",
            "content": [
                {"type": "tool_use", "id": "a", "name": "t", "input": {}},
                {"type": "tool_use", "id": "b", "name": "t", "input": {}},
            ],
        },
        {
            "role": "user",
            "content": [
                {"type": "tool_result", "tool_use_id": "a", "content": "x"},
                {"type": "tool_result", "tool_use_id": "b", "content": "y"},
            ],
        },
        {
            "role": "assistant",
            "content": [{"type": "tool_use", "id": "c", "name": "t", "input": {}}],
        },
        {
            "role": "user",
            "content": [{"type": "tool_result", "tool_use_id": "c", "content": "z"}],
        },
    ]

    # Three uses in total: two in messages[1], one in messages[3].
    assert compose.clearing_boundary(messages, keep_tool_uses=1) == 3
    assert compose.clearing_boundary(messages, keep_tool_uses=2) == 1
    assert compose.clearing_boundary(messages, keep_tool_uses=3) == 0
    print("ok  two tool uses in one message consume two of the keep budget")


# --------------------------------------------------------------------------- #
# C2-C4: placement
# --------------------------------------------------------------------------- #


def test_past_the_lookback_window_the_anchor_lands_on_the_survivor():
    """C2: the whole point - the anchor is the survivor, never the head."""
    messages = _rounds(10)
    boundary = compose.clearing_boundary(messages, keep_tool_uses=3)

    placed = compose.place_breakpoints_for_clearing(
        messages, budget=2, keep_tool_uses=3
    )

    assert compose._count_blocks(messages) > compose.LOOKBACK_BLOCKS
    assert placed.marker_count == 2, placed.marker_count
    marked = _marked_positions(placed.messages)
    assert len(marked) == 2, marked

    last_index = len(messages) - 1
    rolling = (last_index, len(placed.messages[last_index]["content"]) - 1)
    anchor = (boundary, len(placed.messages[boundary]["content"]) - 1)
    assert sorted(marked) == sorted([rolling, anchor]), marked
    assert boundary != 0, "the fixture must not be a degrade case"
    assert (0, 0) not in marked, "the anchor is on the survivor, not the head"
    print("ok  budget=2, keep=3 -> rolling tail plus an anchor on messages[15]")


def test_a_boundary_at_the_head_degrades_to_the_parents_anchor():
    """C3: nothing cleared yet, so the head is still the right anchor."""
    messages = _rounds(10)
    assert compose.clearing_boundary(messages, keep_tool_uses=50) == 0

    placed = compose.place_breakpoints_for_clearing(
        messages, budget=2, keep_tool_uses=50
    )

    marked = _marked_positions(placed.messages)
    assert placed.marker_count == 2, placed.marker_count
    assert (0, 0) in marked, marked
    print("ok  a boundary of 0 falls back to the head anchor, as the parent does")


def test_a_boundary_past_the_end_skips_the_anchor():
    """C3: keep=0 leaves no survivor, so there is nothing stable to anchor."""
    messages = _rounds(10)
    assert compose.clearing_boundary(messages, keep_tool_uses=0) == len(messages)

    placed = compose.place_breakpoints_for_clearing(
        messages, budget=2, keep_tool_uses=0
    )

    assert placed.marker_count == 1, placed.marker_count
    marked = _marked_positions(placed.messages)
    assert marked == [(len(messages) - 1, 0)], marked
    print("ok  a boundary past the end places the rolling marker and no anchor")


def test_a_boundary_on_the_tail_does_not_double_mark_it():
    """C3: one block marked twice is one breakpoint, and the count must say one.

    The fixture ends on an unanswered `tool_use` - not a message list the API
    would accept, but this function is pure and has to answer for any list.
    """
    messages = _rounds(10)[:-1]  # drop the final tool_result
    assert len(messages) == 20
    assert compose.clearing_boundary(messages, keep_tool_uses=1) == 19

    placed = compose.place_breakpoints_for_clearing(
        messages, budget=2, keep_tool_uses=1
    )

    assert placed.marker_count == 1, placed.marker_count
    assert _marked_positions(placed.messages) == [(19, 1)]
    print("ok  a boundary colliding with the rolling marker is not counted twice")


def test_below_the_lookback_window_only_the_rolling_marker_is_placed():
    """C4: a second breakpoint the lookback can already reach buys nothing."""
    messages = _rounds(6)
    assert compose._count_blocks(messages) <= compose.LOOKBACK_BLOCKS

    placed = compose.place_breakpoints_for_clearing(
        messages, budget=2, keep_tool_uses=3
    )

    assert placed.marker_count == 1, placed.marker_count
    assert _marked_positions(placed.messages) == [(len(messages) - 1, 0)]
    print("ok  below the 20-block window only the rolling marker is placed")


def test_a_budget_of_one_is_spent_on_the_tail_not_the_survivor():
    """C4: with one slot, the frozen tail is worth more than the anchor."""
    messages = _rounds(10)

    placed = compose.place_breakpoints_for_clearing(
        messages, budget=1, keep_tool_uses=3
    )

    assert placed.marker_count == 1
    assert _marked_positions(placed.messages) == [(len(messages) - 1, 0)]
    print("ok  budget=1 marks the rolling tail and skips the anchor")


def test_a_budget_of_zero_places_nothing_and_a_big_one_is_clamped():
    """C4: the cap is the API's, not the caller's."""
    messages = _rounds(10)

    none_placed = compose.place_breakpoints_for_clearing(
        messages, budget=0, keep_tool_uses=3
    )
    assert none_placed.marker_count == 0
    assert _marked_positions(none_placed.messages) == []

    clamped = compose.place_breakpoints_for_clearing(
        messages, budget=99, keep_tool_uses=3
    )
    assert clamped.marker_count <= compose.MAX_BREAKPOINTS
    assert clamped.marker_count == 2, clamped.marker_count
    print("ok  budget=0 places nothing and an oversized budget is clamped")


def test_an_empty_message_list_places_nothing():
    """C4: no messages, no markers, no crash."""
    placed = compose.place_breakpoints_for_clearing(
        [], budget=2, keep_tool_uses=3
    )

    assert placed.messages == []
    assert placed.marker_count == 0
    print("ok  an empty message list places nothing")


# --------------------------------------------------------------------------- #
# C5: purity
# --------------------------------------------------------------------------- #


def test_the_input_is_never_mutated():
    """C5: the caller's list is theirs; we hand back a copy."""
    messages = _rounds(10)
    before = copy.deepcopy(messages)

    placed = compose.place_breakpoints_for_clearing(
        messages, budget=2, keep_tool_uses=3
    )
    placed.messages[0]["content"] = "clobbered"

    assert messages == before, "the input was mutated"
    print("ok  the input list is deep-copied, not aliased or mutated")


def test_placing_twice_places_the_same_markers():
    """C5: idempotent over the same list - re-running is not a second breakpoint."""
    messages = _rounds(10)

    once = compose.place_breakpoints_for_clearing(
        messages, budget=2, keep_tool_uses=3
    )
    twice = compose.place_breakpoints_for_clearing(
        once.messages, budget=2, keep_tool_uses=3
    )

    assert twice.messages == once.messages
    assert twice.marker_count == once.marker_count == 2
    print("ok  placing over an already-placed list yields the same two markers")


def test_a_growing_loop_does_not_accumulate_markers():
    """C5: the tail *and* the boundary move each turn; stale markers must not stay.

    This is the case that matters: without the strip, a twelve-turn loop would
    send a dozen breakpoints against a cap of four.
    """
    placed = compose.place_breakpoints_for_clearing(
        _rounds(8), budget=2, keep_tool_uses=3
    )

    for count in range(9, 13):
        grown = [*placed.messages, *_rounds(count)[-2:]]
        placed = compose.place_breakpoints_for_clearing(
            grown, budget=2, keep_tool_uses=3
        )
        assert placed.marker_count == 2, (count, placed.marker_count)
        assert len(_marked_positions(placed.messages)) == 2, count

    print("ok  a growing loop keeps exactly two markers, never accumulating")


def test_the_module_constant_is_never_handed_out():
    """C5: a caller mutating a returned marker must not change the next call."""
    messages = _rounds(10)

    placed = compose.place_breakpoints_for_clearing(
        messages, budget=2, keep_tool_uses=3
    )
    marker = placed.messages[-1]["content"][-1][compose.CACHE_CONTROL_KEY]
    assert marker is not compose.EPHEMERAL
    marker["ttl"] = "1h"

    assert compose.EPHEMERAL == {"type": "ephemeral"}
    print("ok  every marker is a fresh dict, never the module constant")


# --------------------------------------------------------------------------- #
# C6: boundary failures
# --------------------------------------------------------------------------- #


def test_a_bad_keep_or_budget_is_rejected():
    """C6: a silently skipped anchor is worse than a raise."""
    messages = _rounds(4)

    _rejects(
        ValueError,
        lambda: compose.clearing_boundary(messages, keep_tool_uses=-1),
        "a negative keep",
    )
    _rejects(
        TypeError,
        lambda: compose.clearing_boundary(messages, keep_tool_uses=True),
        "a boolean keep",
    )
    _rejects(
        TypeError,
        lambda: compose.clearing_boundary(messages, keep_tool_uses="3"),
        "a string keep",
    )
    _rejects(
        ValueError,
        lambda: compose.place_breakpoints_for_clearing(
            messages, budget=-1, keep_tool_uses=3
        ),
        "a negative budget",
    )
    _rejects(
        TypeError,
        lambda: compose.place_breakpoints_for_clearing(
            messages, budget=1.5, keep_tool_uses=3
        ),
        "a float budget",
    )
    print("ok  a negative or non-int keep or budget raises at the boundary")


def test_a_malformed_message_is_rejected_by_both_entry_points():
    """C6: both public functions validate, so neither can be the loose door."""
    for call, label in (
        (lambda m: compose.clearing_boundary(m, keep_tool_uses=1), "clearing_boundary"),
        (
            lambda m: compose.place_breakpoints_for_clearing(
                m, budget=2, keep_tool_uses=1
            ),
            "place_breakpoints_for_clearing",
        ),
    ):
        _rejects(TypeError, lambda: call(["not a mapping"]), f"{label}: a bare str")
        _rejects(
            TypeError,
            lambda: call([{"role": "user"}]),
            f"{label}: a message with no content",
        )
        _rejects(
            TypeError,
            lambda: call([{"role": "user", "content": 7}]),
            f"{label}: a numeric content",
        )
        _rejects(
            ValueError,
            lambda: call([{"role": "user", "content": []}]),
            f"{label}: an empty content list",
        )
        _rejects(
            ValueError,
            lambda: call([{"role": "user", "content": [{"text": "no type"}]}]),
            f"{label}: a block with no type",
        )
    print("ok  a malformed message raises from either entry point")


def test_validation_happens_before_anything_is_copied():
    """C6: a list that is half valid raises rather than returning half a placement."""
    messages = [*_rounds(3), {"role": "user"}]

    _rejects(
        TypeError,
        lambda: compose.place_breakpoints_for_clearing(
            messages, budget=2, keep_tool_uses=1
        ),
        "a list whose last message is malformed",
    )
    print("ok  one bad message at the end rejects the whole list")


# --------------------------------------------------------------------------- #
# C7: the edit shape
# --------------------------------------------------------------------------- #


def test_the_config_matches_the_documented_edit_shape():
    """C7: dict-equality against the shape from the context-editing docs."""
    config = compose.clearing_config(keep_tool_uses=3, trigger_tool_uses=5)

    assert config == {
        "edits": [
            {
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "tool_uses", "value": 5},
                "keep": {"type": "tool_uses", "value": 3},
            }
        ]
    }, config
    print("ok  clearing_config is dict-equal to the documented edit shape")


def test_the_config_rejects_a_policy_this_demo_does_not_send():
    """C7: keep=0 is answerable by the boundary helper but is not a policy here."""
    _rejects(
        ValueError,
        lambda: compose.clearing_config(keep_tool_uses=0, trigger_tool_uses=5),
        "keep=0",
    )
    _rejects(
        ValueError,
        lambda: compose.clearing_config(keep_tool_uses=3, trigger_tool_uses=0),
        "trigger=0",
    )
    _rejects(
        TypeError,
        lambda: compose.clearing_config(keep_tool_uses=3, trigger_tool_uses="5"),
        "a string trigger",
    )
    print("ok  clearing_config rejects keep=0, trigger=0 and non-int values")


# --------------------------------------------------------------------------- #
# C8: why the rule exists
# --------------------------------------------------------------------------- #


def test_the_anchored_region_survives_the_next_round():
    """C8: the anchor covers a prefix that one more round leaves byte-identical.

    Two client-side facts, both checkable here. First, the transcript through
    the anchor is unchanged when a round is appended - so the *unedited* prefix
    is stable. Second, the boundary moves strictly forward, so everything the
    anchor covers is at or above the next turn's boundary and will therefore
    have been cleared to the same placeholder text on both turns. Together they
    are why the re-cached prefix is readable again on turn B, which is the claim
    the live run then confirms with `cache_read_input_tokens`.
    """
    messages = transcript.build_transcript(
        rounds=main.ROUNDS, result_chars=main.RESULT_CHARS
    )
    grown = main._grown_transcript(messages)
    anchor = compose.clearing_boundary(messages, keep_tool_uses=main.KEEP)
    next_anchor = compose.clearing_boundary(grown, keep_tool_uses=main.KEEP)

    assert grown[: anchor + 1] == messages[: anchor + 1]
    assert next_anchor > anchor, (anchor, next_anchor)
    print("ok  the anchored prefix is byte-identical one round later")


def test_the_head_anchor_would_have_covered_a_region_that_changes():
    """C8: the negative case - `messages[0]` is stable, the span below it is not.

    A head anchor caches from the start of the list to `messages[0]`'s last
    block, and the next breakpoint down has to bridge everything in between -
    which is exactly the span the clear rewrites. The boundary anchor starts the
    stable region *after* that span instead.
    """
    messages = transcript.build_transcript(
        rounds=main.ROUNDS, result_chars=main.RESULT_CHARS
    )
    anchor = compose.clearing_boundary(messages, keep_tool_uses=main.KEEP)

    cleared_between = [
        index
        for index in range(1, anchor)
        if isinstance(messages[index]["content"], list)
        and any(
            block.get("type") == "tool_result" for block in messages[index]["content"]
        )
    ]
    assert cleared_between, "the fixture must have cleared results below the anchor"
    assert anchor > 0
    print(
        f"ok  {len(cleared_between)} tool results sit between the head and the "
        "anchor, all rewritten on every clear"
    )


# --------------------------------------------------------------------------- #
# C9: the shell's static half
# --------------------------------------------------------------------------- #


def test_the_static_prefix_carries_one_breakpoint_each_and_is_byte_stable():
    """C9: two static markers, and nothing variable in what they cache."""
    system = main.build_system()
    tools = main.build_tools()

    assert len(system) == 1
    assert compose.CACHE_CONTROL_KEY in system[0]
    assert system == main.build_system(), "the system prefix is not byte-stable"
    # Sized, not decorative: tools + system has to clear the model's
    # 1,024-token minimum cacheable prefix, or both static breakpoints cache
    # nothing at all and no error is reported anywhere.
    assert len(system[0]["text"]) >= main.MIN_SYSTEM_CHARS, len(system[0]["text"])

    marked_tools = [
        tool for tool in tools if compose.CACHE_CONTROL_KEY in tool
    ]
    assert len(marked_tools) == 1, marked_tools
    assert marked_tools[0] is tools[-1], "the tools marker must be on the last tool"
    assert tools == main.build_tools(), "the tools prefix is not byte-stable"
    print("ok  system and tools each carry exactly one byte-stable breakpoint")


def test_all_four_breakpoints_of_a_live_request_fit_the_cap():
    """C9: the static two plus the message budget never exceed the API's four."""
    messages = transcript.build_transcript(
        rounds=main.ROUNDS, result_chars=main.RESULT_CHARS
    )
    placed = compose.place_breakpoints_for_clearing(
        messages, budget=main.MESSAGES_BUDGET, keep_tool_uses=main.KEEP
    )

    total = main.STATIC_BREAKPOINTS + placed.marker_count
    assert placed.marker_count == 2, placed.marker_count
    assert total == compose.MAX_BREAKPOINTS, total
    print("ok  2 static + 2 message breakpoints is exactly the documented cap")


def test_the_grown_transcript_extends_the_original():
    """C9: turn B re-reads turn A's prefix only if it really is a prefix."""
    messages = transcript.build_transcript(
        rounds=main.ROUNDS, result_chars=main.RESULT_CHARS
    )
    grown = main._grown_transcript(messages)

    assert len(grown) == len(messages) + 2
    assert grown[: len(messages)] == messages
    print("ok  the turn-B transcript extends the turn-A one byte for byte")


# --------------------------------------------------------------------------- #


def main_() -> int:
    tests = [
        test_ten_pairs_with_keep_three_survive_from_message_fifteen,
        test_keeping_everything_leaves_the_boundary_at_the_head,
        test_keeping_nothing_leaves_no_survivor_at_all,
        test_a_transcript_with_no_tool_use_never_clears,
        test_the_boundary_walks_back_one_round_per_kept_tool_use,
        test_parallel_tool_uses_in_one_message_count_once_each,
        test_past_the_lookback_window_the_anchor_lands_on_the_survivor,
        test_a_boundary_at_the_head_degrades_to_the_parents_anchor,
        test_a_boundary_past_the_end_skips_the_anchor,
        test_a_boundary_on_the_tail_does_not_double_mark_it,
        test_below_the_lookback_window_only_the_rolling_marker_is_placed,
        test_a_budget_of_one_is_spent_on_the_tail_not_the_survivor,
        test_a_budget_of_zero_places_nothing_and_a_big_one_is_clamped,
        test_an_empty_message_list_places_nothing,
        test_the_input_is_never_mutated,
        test_placing_twice_places_the_same_markers,
        test_a_growing_loop_does_not_accumulate_markers,
        test_the_module_constant_is_never_handed_out,
        test_a_bad_keep_or_budget_is_rejected,
        test_a_malformed_message_is_rejected_by_both_entry_points,
        test_validation_happens_before_anything_is_copied,
        test_the_config_matches_the_documented_edit_shape,
        test_the_config_rejects_a_policy_this_demo_does_not_send,
        test_the_anchored_region_survives_the_next_round,
        test_the_head_anchor_would_have_covered_a_region_that_changes,
        test_the_static_prefix_carries_one_breakpoint_each_and_is_byte_stable,
        test_all_four_breakpoints_of_a_live_request_fit_the_cap,
        test_the_grown_transcript_extends_the_original,
    ]
    started = time.monotonic()
    for test in tests:
        test()
    elapsed = time.monotonic() - started

    # Checked rather than claimed in prose: the core and the shell's request
    # builders were exercised without the SDK ever being imported, so nothing
    # here could have reached the network or read a key.
    assert "anthropic" not in sys.modules, "the self-test imported the SDK"
    assert elapsed < 1.0, f"self-test took {elapsed:.3f}s; something did I/O"

    print(f"\nAll {len(tests)} self-tests passed with no key and no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
