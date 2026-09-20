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

and from research/2026-09-16-prompt-caching-1h-ttl.md:

  P10. the TTL parameter        -> the default is byte-identical to the
                                   pre-TTL marker, `1h` marks every block it
                                   places, and an unknown TTL raises before
                                   anything is copied
  P11. one TTL per request      -> all four breakpoints of a 1-hour run carry
                                   the same `ttl`; mixing is out of scope
"""

from __future__ import annotations

import copy
import json
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
# P10-P11: the TTL parameter (research/2026-09-16-prompt-caching-1h-ttl.md)
# --------------------------------------------------------------------------- #


def test_the_default_ttl_is_the_pre_ttl_marker_byte_for_byte():
    """P10: the regression guard - adding the knob must not move the default wire.

    `{"type": "ephemeral"}` with no `ttl` key is the only 5-minute form the docs
    show. An explicit `"ttl": "5m"` would be an unverified wire form *and* would
    change the hashed prefix of every caller that never asked for a TTL.
    """
    messages = _tool_loop(rounds=7)

    implicit = placement.place_breakpoints(messages, budget=2)
    explicit = placement.place_breakpoints(messages, budget=2, ttl=placement.CACHE_TTL_5M)

    assert implicit.messages == explicit.messages
    assert _marked_positions(implicit.messages) == _marked_positions(explicit.messages)
    for placed in (implicit, explicit):
        for message_index, block_index in _marked_positions(placed.messages):
            marker = placed.messages[message_index]["content"][block_index][
                placement.CACHE_CONTROL_KEY
            ]
            assert marker == {"type": "ephemeral"}, marker
            assert placement.TTL_KEY not in marker, marker
    print("ok  the default TTL is the pre-TTL marker byte for byte, with no ttl key")


def test_the_one_hour_ttl_marks_every_block_this_call_marks():
    """P10: both breakpoints, not just the rolling one - and as a plain JSON str."""
    messages = _tool_loop(rounds=7)  # 22 blocks: past the lookback, so two markers

    placed = placement.place_breakpoints(messages, budget=2, ttl=placement.CACHE_TTL_1H)

    positions = _marked_positions(placed.messages)
    assert placed.marker_count == 2, placed.marker_count
    assert len(positions) == 2, positions
    for message_index, block_index in positions:
        marker = placed.messages[message_index]["content"][block_index][
            placement.CACHE_CONTROL_KEY
        ]
        assert marker == {"type": "ephemeral", "ttl": "1h"}, marker
        # It goes on the wire as JSON, so the enum must not leak into the body.
        assert json.loads(json.dumps(marker)) == {"type": "ephemeral", "ttl": "1h"}
        assert type(marker[placement.TTL_KEY]) is str, type(marker[placement.TTL_KEY])
    print("ok  the 1-hour TTL marks every block it places, as a plain JSON string")


def test_an_unknown_ttl_is_rejected_before_anything_is_copied():
    """P10: a typo'd TTL is a silent 5-minute bill otherwise - the exact failure
    the research note's practitioner source describes."""
    messages = _tool_loop(rounds=3)
    before = copy.deepcopy(messages)

    for bad in ("1 hour", "3600", "1H", "5 minutes", "", 3600, None):
        _rejects(
            ValueError,
            lambda bad=bad: placement.place_breakpoints(messages, budget=2, ttl=bad),
            f"ttl={bad!r}",
        )
        _rejects(
            ValueError,
            lambda bad=bad: placement.ephemeral_marker(bad),
            f"ephemeral_marker({bad!r})",
        )
    assert messages == before, "a rejected call still touched the caller's list"

    # And the message names what would have been accepted, rather than just failing.
    try:
        placement.place_breakpoints(messages, budget=2, ttl="1 hour")
    except ValueError as exc:
        assert "'5m'" in str(exc) and "'1h'" in str(exc), str(exc)
    print("ok  an unknown ttl raises ValueError naming both accepted values")


def test_a_ttl_marker_is_a_fresh_dict_not_a_shared_one():
    """P10: same aliasing guard as `EPHEMERAL`, now for the 1-hour form too."""
    first = placement.ephemeral_marker(placement.CACHE_TTL_1H)
    second = placement.ephemeral_marker(placement.CACHE_TTL_1H)

    assert first == second
    assert first is not second
    first["ttl"] = "mutated"
    assert placement.ephemeral_marker(placement.CACHE_TTL_1H)["ttl"] == "1h"
    assert placement.ephemeral_marker(placement.CACHE_TTL_5M) == placement.EPHEMERAL
    assert placement.ephemeral_marker(placement.CACHE_TTL_5M) is not placement.EPHEMERAL
    print("ok  every marker is a fresh dict, so no edit reaches the constants")


def test_a_marker_at_another_ttl_does_not_survive_re_placement():
    """P10: this module owns `cache_control` in `messages` - including its TTL.

    A caller that hand-marked a block at 1 hour and then runs the default policy
    must not keep a stray 1-hour breakpoint, or one request would carry two TTLs
    and the write would be billed at two multipliers.
    """
    messages = _tool_loop(rounds=7)
    messages[0]["content"] = [
        {
            "type": "text",
            "text": "hand-marked",
            placement.CACHE_CONTROL_KEY: {"type": "ephemeral", "ttl": "1h"},
        }
    ]

    placed = placement.place_breakpoints(messages, budget=1)

    markers = [
        placed.messages[m]["content"][b][placement.CACHE_CONTROL_KEY]
        for m, b in _marked_positions(placed.messages)
    ]
    assert placed.marker_count == 1, placed.marker_count
    assert markers == [{"type": "ephemeral"}], markers
    print("ok  a hand-placed marker at another TTL is stripped, not inherited")


def test_all_four_breakpoints_of_a_one_hour_run_share_one_ttl():
    """P11: mixing TTLs is out of scope, so the whole request must agree.

    The static tools and system breakpoints carry the bulk of the cached prefix;
    if they stayed at 5 minutes while the message markers went to 1 hour, turn
    1's write would land mostly in `ephemeral_5m_input_tokens` and the run would
    pay 2x prices on a 5-minute entry.
    """
    ttl = placement.CACHE_TTL_1H
    expected = {"type": "ephemeral", "ttl": "1h"}

    system = main.build_system(ttl=ttl)
    tools = main.build_tools(ttl=ttl)
    placed = placement.place_breakpoints(
        _tool_loop(rounds=7), budget=main.MESSAGES_BUDGET, ttl=ttl
    )

    markers = [block[placement.CACHE_CONTROL_KEY] for block in system]
    markers += [
        tool[placement.CACHE_CONTROL_KEY]
        for tool in tools
        if placement.CACHE_CONTROL_KEY in tool
    ]
    markers += [
        placed.messages[m]["content"][b][placement.CACHE_CONTROL_KEY]
        for m, b in _marked_positions(placed.messages)
    ]

    assert len(markers) == placement.MAX_BREAKPOINTS, len(markers)
    assert all(marker == expected for marker in markers), markers
    # And the default still builds the four 5-minute markers it always did.
    assert main.build_system()[0][placement.CACHE_CONTROL_KEY] == {"type": "ephemeral"}
    assert main.build_tools()[-1][placement.CACHE_CONTROL_KEY] == {"type": "ephemeral"}
    print("ok  all four breakpoints of a 1-hour run carry the same ttl")


def test_the_cached_prefix_bytes_do_not_change_with_the_ttl():
    """P11: only the marker moves - the cached text itself is TTL-independent."""
    five = main.build_system(ttl=placement.CACHE_TTL_5M)
    hour = main.build_system(ttl=placement.CACHE_TTL_1H)

    assert five[0]["text"] == hour[0]["text"]
    assert [tool["name"] for tool in main.build_tools(ttl=placement.CACHE_TTL_1H)] == [
        tool["name"] for tool in main.build_tools(ttl=placement.CACHE_TTL_5M)
    ]
    assert five[0][placement.CACHE_CONTROL_KEY] != hour[0][placement.CACHE_CONTROL_KEY]
    print("ok  the TTL changes the marker and nothing else about the prefix")


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
        test_the_default_ttl_is_the_pre_ttl_marker_byte_for_byte,
        test_the_one_hour_ttl_marks_every_block_this_call_marks,
        test_an_unknown_ttl_is_rejected_before_anything_is_copied,
        test_a_ttl_marker_is_a_fresh_dict_not_a_shared_one,
        test_a_marker_at_another_ttl_does_not_survive_re_placement,
        test_all_four_breakpoints_of_a_one_hour_run_share_one_ttl,
        test_the_cached_prefix_bytes_do_not_change_with_the_ttl,
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
