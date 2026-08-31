"""Offline self-test for breakpoint placement. No API key, no network, no
`anthropic` install.

Run:
    python3 test_placement.py

Each test maps to an acceptance criterion from the research note
(research/2026-08-29-prompt-caching-tool-loop.md):

  P1. one message               -> exactly one marker, on its last block
  P2. short loop                -> rolling marker only; an anchor below the
                                   20-block lookback window buys nothing
  P3. loop past 20 blocks       -> rolling marker plus a head anchor
  P4. budget=1                  -> the rolling marker, never the anchor
  P5. budget=0 / budget=9       -> nothing placed / clamped to the cap
  P6. purity                    -> input unmutated, placement idempotent
  P7. normalisation             -> a marked str becomes a text block; an
                                   unmarked one is left alone entirely
  P8. boundary failures         -> ValueError / TypeError, never a silent skip
  P9. the static breakpoints    -> system and tools carry exactly one each, the
                                   prefix is byte-stable, and all four fit the cap
"""

from __future__ import annotations

import copy
import sys
import time

import main
import placement

# --------------------------------------------------------------------------- #
# Fixtures and helpers
# --------------------------------------------------------------------------- #


def _tool_loop(rounds: int) -> list[dict]:
    """A hand-written tool loop's message list: user, then (assistant, user) x N.

    Block count is 1 + 3 * rounds, so `rounds` is the dial that moves a
    transcript across the 20-block lookback window.
    """
    messages: list[dict] = [{"role": "user", "content": "What is 4839 * 1284?"}]
    for n in range(rounds):
        messages.append(
            {
                "role": "assistant",
                "content": [
                    {"type": "text", "text": f"Step {n}: I will use the calculator."},
                    {
                        "type": "tool_use",
                        "id": f"toolu_{n}",
                        "name": "calculator",
                        "input": {"expression": "4839 * 1284"},
                    },
                ],
            }
        )
        messages.append(
            {
                "role": "user",
                "content": [
                    {
                        "type": "tool_result",
                        "tool_use_id": f"toolu_{n}",
                        "content": "6213276",
                    }
                ],
            }
        )
    return messages


def _single_block_messages(count: int) -> list[dict]:
    """`count` messages of exactly one block each - so block count == message count."""
    return [
        {"role": "user" if n % 2 == 0 else "assistant", "content": f"turn {n}"}
        for n in range(count)
    ]


def _marked_positions(messages: list[dict]) -> list[tuple[int, int]]:
    """Every (message index, block index) carrying a `cache_control` key."""
    found = []
    for message_index, message in enumerate(messages):
        content = message["content"]
        if isinstance(content, str):
            continue
        for block_index, block in enumerate(content):
            if placement.CACHE_CONTROL_KEY in block:
                found.append((message_index, block_index))
    return found


# --------------------------------------------------------------------------- #
# P1-P4: where the markers go
# --------------------------------------------------------------------------- #


def test_a_single_message_is_marked_on_its_last_block():
    """P1: one message, budget to spare - one rolling marker, nothing else."""
    messages = [
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "first"},
                {"type": "text", "text": "last"},
            ],
        }
    ]

    placed = placement.place_breakpoints(messages, budget=2)

    assert placed.marker_count == 1, placed.marker_count
    assert _marked_positions(placed.messages) == [(0, 1)]
    assert placed.messages[0]["content"][1][placement.CACHE_CONTROL_KEY] == {
        "type": "ephemeral"
    }
    print("ok  a single message is marked on its last block, and only there")


def test_a_short_loop_gets_the_rolling_marker_only():
    """P2: below the lookback window one breakpoint already chains turn to turn."""
    messages = _tool_loop(rounds=2)  # 5 messages, 7 blocks
    assert placement._count_blocks(messages) <= placement.LOOKBACK_BLOCKS

    placed = placement.place_breakpoints(messages, budget=2)

    assert placed.marker_count == 1, placed.marker_count
    assert _marked_positions(placed.messages) == [(len(messages) - 1, 0)]
    print("ok  a short loop spends one breakpoint, not two, below the lookback")


def test_a_loop_past_the_lookback_window_also_gets_an_anchor():
    """P3: past 20 blocks the tail breakpoint can no longer see the head."""
    messages = _tool_loop(rounds=7)  # 15 messages, 22 blocks
    assert placement._count_blocks(messages) > placement.LOOKBACK_BLOCKS

    placed = placement.place_breakpoints(messages, budget=2)

    assert placed.marker_count == 2, placed.marker_count
    # The head anchor is on the first message's only block; the rolling marker on
    # the last message's only block.
    assert _marked_positions(placed.messages) == [(0, 0), (len(messages) - 1, 0)]
    print("ok  past 20 blocks the head is anchored as well as the tail")


def test_the_anchor_appears_only_above_the_lookback_window():
    """P3: the rule is strictly `>` 20 blocks - 20 exactly is still one marker."""
    at_window = placement.place_breakpoints(
        _single_block_messages(placement.LOOKBACK_BLOCKS), budget=2
    )
    past_window = placement.place_breakpoints(
        _single_block_messages(placement.LOOKBACK_BLOCKS + 1), budget=2
    )

    assert at_window.marker_count == 1, at_window.marker_count
    assert past_window.marker_count == 2, past_window.marker_count
    print("ok  20 blocks is one marker, 21 is two - the window boundary is exact")


def test_a_budget_of_one_is_spent_on_the_tail_not_the_head():
    """P4: with one breakpoint left, the moving tail is worth more than the head."""
    messages = _tool_loop(rounds=7)

    placed = placement.place_breakpoints(messages, budget=1)

    assert placed.marker_count == 1, placed.marker_count
    assert _marked_positions(placed.messages) == [(len(messages) - 1, 0)]
    print("ok  a budget of one goes to the rolling marker, never the anchor")


# --------------------------------------------------------------------------- #
# P5: the budget itself
# --------------------------------------------------------------------------- #


def test_a_budget_of_zero_places_nothing_and_copies_everything():
    """P5: a caller that already spent all four breakpoints gets its list back."""
    messages = _tool_loop(rounds=7)

    placed = placement.place_breakpoints(messages, budget=0)

    assert placed.marker_count == 0, placed.marker_count
    assert _marked_positions(placed.messages) == []
    assert placed.messages == messages
    assert placed.messages is not messages
    print("ok  a budget of zero returns an unmarked copy, not the original list")


def test_an_empty_message_list_places_nothing():
    """P5: nothing to mark is not an error, it is zero markers."""
    placed = placement.place_breakpoints([], budget=4)

    assert placed.marker_count == 0, placed.marker_count
    assert placed.messages == []
    print("ok  an empty message list places nothing and does not raise")


def test_a_budget_over_the_cap_is_clamped():
    """P5: the API's cap is four; a caller asking for nine still gets at most two."""
    messages = _tool_loop(rounds=7)

    placed = placement.place_breakpoints(messages, budget=9)

    assert placed.marker_count <= placement.MAX_BREAKPOINTS, placed.marker_count
    assert placed.marker_count == 2, placed.marker_count
    print("ok  a budget above the documented cap of 4 is clamped, not obeyed")


# --------------------------------------------------------------------------- #
# P6: purity
# --------------------------------------------------------------------------- #


def test_the_input_is_never_mutated():
    """P6: the caller's own history is untouched, markers and all."""
    messages = _tool_loop(rounds=7)
    before = copy.deepcopy(messages)

    placement.place_breakpoints(messages, budget=2)

    assert messages == before
    print("ok  placing breakpoints leaves the caller's message list untouched")


def test_placing_twice_places_the_same_markers():
    """P6: idempotent - the next turn re-marks the same positions, not more."""
    messages = _tool_loop(rounds=7)

    once = placement.place_breakpoints(messages, budget=2)
    twice = placement.place_breakpoints(once.messages, budget=2)

    assert once.marker_count == twice.marker_count == 2
    assert _marked_positions(once.messages) == _marked_positions(twice.messages)
    assert once.messages == twice.messages
    print("ok  re-placing over an already-marked list is a no-op")


def test_a_growing_loop_does_not_accumulate_markers():
    """P6: idempotent across a GROWING list - the module's actual advertised use.

    `test_placing_twice_places_the_same_markers` re-places over the *same* list,
    where the tail never moves and the marker lands on the same block twice. A
    real tool loop appends a turn and feeds the result back, so the tail moves
    and the previous tail keeps a stale marker. Regression test for markers
    accumulating 1, 2, 3, 4... until a request exceeds `MAX_BREAKPOINTS` and the
    API rejects it - while `marker_count` keeps reporting only what the last
    call placed.
    """
    messages = _tool_loop(rounds=1)
    budget = placement.MAX_BREAKPOINTS - 2  # main.py spends 2 on tools + system

    for turn in range(5):
        placed = placement.place_breakpoints(messages, budget=budget)
        marked = _marked_positions(placed.messages)

        assert len(marked) == placed.marker_count, (
            f"turn {turn}: the list carries {len(marked)} markers but "
            f"marker_count reports {placed.marker_count}"
        )
        assert len(marked) <= budget, (
            f"turn {turn}: {len(marked)} markers exceeds the budget of {budget}"
        )

        messages = [
            *placed.messages,
            {"role": "assistant", "content": [{"type": "text", "text": f"r{turn}"}]},
            {"role": "user", "content": [{"type": "text", "text": f"n{turn}"}]},
        ]

    print("ok  a growing loop re-marks the moved tail without accumulating")


# --------------------------------------------------------------------------- #
# P7: normalisation
# --------------------------------------------------------------------------- #


def test_a_marked_string_becomes_a_block_and_an_unmarked_one_is_left_alone():
    """P7: `cache_control` attaches to a block, so a marked str has to become one.

    The unmarked message keeps the exact `content` object it came in with - the
    deep copy of an immutable str is that same str - so nothing about a message
    this policy did not choose is rewritten.
    """
    messages = [
        {"role": "user", "content": "the head, unmarked"},
        {"role": "assistant", "content": "the tail, marked"},
    ]

    placed = placement.place_breakpoints(messages, budget=2)

    assert placed.messages[1]["content"] == [
        {
            "type": "text",
            "text": "the tail, marked",
            "cache_control": {"type": "ephemeral"},
        }
    ]
    assert placed.messages[0]["content"] is messages[0]["content"]
    print("ok  a marked str becomes a text block; an unmarked one is not rewritten")


def test_an_unmarked_block_list_is_copied_not_aliased():
    """P7: no returned message shares a mutable block with the caller's input."""
    messages = _tool_loop(rounds=2)

    placed = placement.place_breakpoints(messages, budget=2)

    assert placed.messages[1]["content"] == messages[1]["content"]
    assert placed.messages[1]["content"] is not messages[1]["content"]
    print("ok  an unmarked block list is deep-copied, so no later edit reaches back")


def test_the_module_constant_is_never_handed_out():
    """P7: every marker is a fresh dict, so mutating one cannot poison the rest."""
    placed = placement.place_breakpoints(_single_block_messages(21), budget=2)
    positions = _marked_positions(placed.messages)

    markers = [
        placed.messages[m]["content"][b][placement.CACHE_CONTROL_KEY]
        for m, b in positions
    ]
    assert all(marker == placement.EPHEMERAL for marker in markers)
    assert all(marker is not placement.EPHEMERAL for marker in markers)
    assert markers[0] is not markers[1]
    print("ok  each marker is a fresh copy of EPHEMERAL, not the constant itself")


# --------------------------------------------------------------------------- #
# P8: boundary failures
# --------------------------------------------------------------------------- #


def _rejects(exception_type, call, label: str) -> None:
    try:
        call()
    except exception_type:
        return
    raise AssertionError(f"{label} was accepted; expected {exception_type.__name__}")


def test_a_negative_budget_is_rejected():
    """P8: a caller who over-spent its four breakpoints has a bug, not a budget."""
    _rejects(
        ValueError,
        lambda: placement.place_breakpoints(_tool_loop(1), budget=-1),
        "budget=-1",
    )
    print("ok  a negative budget raises ValueError instead of clamping to zero")


def test_a_non_integer_budget_is_rejected():
    """P8: `budget=True` reads as a flag and would silently mean one breakpoint."""
    _rejects(
        TypeError,
        lambda: placement.place_breakpoints(_tool_loop(1), budget=2.0),
        "budget=2.0",
    )
    _rejects(
        TypeError,
        lambda: placement.place_breakpoints(_tool_loop(1), budget=True),
        "budget=True",
    )
    print("ok  a float or bool budget raises TypeError")


def test_a_malformed_message_is_rejected():
    """P8: role/content are the contract; a missing one is a TypeError, not a skip."""
    _rejects(
        TypeError,
        lambda: placement.place_breakpoints([{"role": "user"}], budget=2),
        "a message with no content",
    )
    _rejects(
        TypeError,
        lambda: placement.place_breakpoints([{"content": "hi"}], budget=2),
        "a message with no role",
    )
    _rejects(
        TypeError,
        lambda: placement.place_breakpoints(["not a message"], budget=2),
        "a str in place of a message",
    )
    _rejects(
        TypeError,
        lambda: placement.place_breakpoints([{"role": "user", "content": 7}], budget=2),
        "an int content",
    )
    print("ok  a message that is not {role, str|list} raises TypeError")


def test_a_malformed_content_block_is_rejected():
    """P8: an untyped block would be marked and then rejected on the wire."""
    _rejects(
        ValueError,
        lambda: placement.place_breakpoints(
            [{"role": "user", "content": [{"text": "no type key"}]}], budget=2
        ),
        "a block with no type",
    )
    _rejects(
        ValueError,
        lambda: placement.place_breakpoints(
            [{"role": "user", "content": []}], budget=2
        ),
        "an empty content list",
    )
    _rejects(
        TypeError,
        lambda: placement.place_breakpoints(
            [{"role": "user", "content": ["bare string block"]}], budget=2
        ),
        "a str in place of a content block",
    )
    print("ok  an untyped, empty or non-mapping content block raises at the boundary")


def test_validation_happens_before_anything_is_copied():
    """P8: a bad message anywhere fails the call, not just a bad marked one."""
    messages = _tool_loop(rounds=3)
    messages[2] = {"role": "assistant", "content": [{"text": "no type key"}]}

    _rejects(
        ValueError,
        lambda: placement.place_breakpoints(messages, budget=2),
        "a malformed message in the middle of the list",
    )
    print("ok  every message is validated, not only the ones about to be marked")


# --------------------------------------------------------------------------- #
# P9: the two static breakpoints, in the shell
# --------------------------------------------------------------------------- #


def test_the_system_block_carries_one_breakpoint_and_clears_the_minimum():
    """P9: the system prefix is one marked block, far above the 1,024-token floor."""
    system = main.build_system()

    assert len(system) == 1, len(system)
    assert system[0][placement.CACHE_CONTROL_KEY] == {"type": "ephemeral"}
    # ~4 characters per token, so >= 10,000 characters is >= ~2,500 tokens -
    # comfortably above every current minimum cacheable prefix (512 / 1,024 /
    # 4,096 tokens by model).
    assert len(system[0]["text"]) >= main.MIN_SYSTEM_CHARS, len(system[0]["text"])
    print("ok  the system prefix is one marked block above the minimum prefix size")


def test_only_the_last_tool_carries_the_tools_breakpoint():
    """P9: one breakpoint caches the whole tools array; two would waste a slot."""
    tools = main.build_tools()

    marked = [
        index
        for index, tool in enumerate(tools)
        if placement.CACHE_CONTROL_KEY in tool
    ]
    assert len(tools) == 2, len(tools)
    assert marked == [len(tools) - 1], marked
    print("ok  the tools breakpoint sits on the last tool, and only there")


def test_the_static_prefix_is_byte_stable():
    """P9: the top cache-killer is a prefix that varies per request. This one cannot.

    No timestamp, no request id, no re-sorted tool list: two builds are equal,
    including tool order.
    """
    assert main.build_system() == main.build_system()
    assert main.build_tools() == main.build_tools()
    assert [tool["name"] for tool in main.build_tools()] == ["calculator", "word_count"]
    print("ok  system and tools rebuild byte-for-byte identically, in a fixed order")


def test_all_four_breakpoints_of_a_live_request_fit_the_cap():
    """P9: 1 tools + 1 system + 2 messages == the documented cap of 4, exactly."""
    assert main.STATIC_BREAKPOINTS + main.MESSAGES_BUDGET == placement.MAX_BREAKPOINTS

    placed = placement.place_breakpoints(
        _tool_loop(rounds=7), budget=main.MESSAGES_BUDGET
    )
    total = (
        len(main.build_system())
        + sum(1 for tool in main.build_tools() if placement.CACHE_CONTROL_KEY in tool)
        + placed.marker_count
    )
    assert total == placement.MAX_BREAKPOINTS, total
    print("ok  a full request spends exactly the four breakpoints the API allows")


# --------------------------------------------------------------------------- #


def main_() -> int:
    tests = [
        test_a_single_message_is_marked_on_its_last_block,
        test_a_short_loop_gets_the_rolling_marker_only,
        test_a_loop_past_the_lookback_window_also_gets_an_anchor,
        test_the_anchor_appears_only_above_the_lookback_window,
        test_a_budget_of_one_is_spent_on_the_tail_not_the_head,
        test_a_budget_of_zero_places_nothing_and_copies_everything,
        test_an_empty_message_list_places_nothing,
        test_a_budget_over_the_cap_is_clamped,
        test_the_input_is_never_mutated,
        test_placing_twice_places_the_same_markers,
        test_a_growing_loop_does_not_accumulate_markers,
        test_a_marked_string_becomes_a_block_and_an_unmarked_one_is_left_alone,
        test_an_unmarked_block_list_is_copied_not_aliased,
        test_the_module_constant_is_never_handed_out,
        test_a_negative_budget_is_rejected,
        test_a_non_integer_budget_is_rejected,
        test_a_malformed_message_is_rejected,
        test_a_malformed_content_block_is_rejected,
        test_validation_happens_before_anything_is_copied,
        test_the_system_block_carries_one_breakpoint_and_clears_the_minimum,
        test_only_the_last_tool_carries_the_tools_breakpoint,
        test_the_static_prefix_is_byte_stable,
        test_all_four_breakpoints_of_a_live_request_fit_the_cap,
    ]
    started = time.monotonic()
    for test in tests:
        test()
    elapsed = time.monotonic() - started

    # Checked rather than claimed in prose: the core and both prefix builders
    # were exercised without the SDK ever being imported, so nothing here could
    # have reached the network or read a key.
    assert "anthropic" not in sys.modules, "the self-test imported the SDK"
    assert elapsed < 1.0, f"self-test took {elapsed:.3f}s; something did I/O"

    print(f"\nAll {len(tests)} self-tests passed with no key and no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
