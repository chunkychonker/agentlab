"""Offline self-test for the saving report and the shell that feeds it. No API
key, no network, no `anthropic` install.

Run:
    python3 test_report.py

Each test maps to an acceptance criterion from the research note
(research/2026-08-29-prompt-caching-tool-loop.md):

  R1. summarize            -> turn 1's write against turn 2's read, at 0.10x
                              and 1.25x, netted; 2000 tokens at $2/MTok is a
                              $0.0036 saving against a $0.0010 premium
  R2. degenerate runs      -> a write nobody read is a *negative* net saving;
                              a run that wrote nothing is 0.0, not a crash
  R3. boundary failures    -> negative or non-int counters, negative rate
  R4. render               -> both counts and every dollar figure, no trailing
                              newline
  R5. the usage adapter    -> the three 1.x field names, null -> 0, a renamed
                              field raises
  R6. the two-turn run     -> exactly two calls, byte-identical tools/system
                              prefix, breakpoints only on the second request,
                              every tool_use answered
  R7. the cache-miss guard -> raises CacheMiss rather than reporting ~0 saved
  R8. no key               -> one line, exit 0, no client ever constructed
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
import time
from types import SimpleNamespace

import main
import placement
import report

# --------------------------------------------------------------------------- #
# Test doubles: the shapes `anthropic` 1.x returns, and nothing more
# --------------------------------------------------------------------------- #


class FakeBlock:
    """A content block shaped like an SDK model: attributes plus `model_dump`."""

    def __init__(self, **payload):
        self._payload = dict(payload)
        for name, value in payload.items():
            setattr(self, name, value)

    def model_dump(self, exclude_none: bool = False) -> dict:
        return {
            name: value
            for name, value in self._payload.items()
            if not (exclude_none and value is None)
        }


def _usage(creation: int, read: int, fresh: int) -> SimpleNamespace:
    return SimpleNamespace(
        cache_creation_input_tokens=creation,
        cache_read_input_tokens=read,
        input_tokens=fresh,
    )


def _response(blocks, usage) -> SimpleNamespace:
    return SimpleNamespace(content=list(blocks), usage=usage)


def _text_reply(text: str = "The product is 6213276."):
    return [FakeBlock(type="text", text=text, citations=None)]


def _tool_call(call_id: str = "toolu_01", expression: str = "4839 * 1284"):
    return [
        FakeBlock(type="text", text="Let me calculate that."),
        FakeBlock(
            type="tool_use",
            id=call_id,
            name="calculator",
            input={"expression": expression},
        ),
    ]


class FakeMessages:
    """Stands in for `client.messages`: records create kwargs, replays responses."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("fake client ran out of scripted responses")
        return self._scripted.pop(0)


class FakeClient:
    """A client exposing only `.messages.create` - all `run()` is allowed to use."""

    def __init__(self, scripted):
        self.messages = FakeMessages(scripted)

    @property
    def calls(self):
        return self.messages.calls


def _two_turn_client(*, first_blocks=None, creation=3000, read=3000, delta=120):
    """A client scripted for a healthy two-turn run: write 3000, read 3000 back."""
    return FakeClient(
        [
            _response(first_blocks or _tool_call(), _usage(creation, 0, 4)),
            _response(_text_reply(), _usage(delta, read, 2)),
        ]
    )


def _rejects(exception_type, call, label: str) -> None:
    try:
        call()
    except exception_type:
        return
    raise AssertionError(f"{label} was accepted; expected {exception_type.__name__}")


# --------------------------------------------------------------------------- #
# R1-R2: the arithmetic
# --------------------------------------------------------------------------- #


def test_summarize_prices_turn_ones_write_against_turn_twos_read():
    """R1: the note's worked example - 2000 tokens both ways at $2/MTok."""
    turn1 = report.TurnUsage(
        cache_creation_input_tokens=2000, cache_read_input_tokens=0, input_tokens=15
    )
    turn2 = report.TurnUsage(
        cache_creation_input_tokens=90, cache_read_input_tokens=2000, input_tokens=8
    )

    saving = report.summarize(turn1, turn2, base_usd_per_mtok=2.0)

    assert saving.written == 2000, saving.written
    assert saving.read == 2000, saving.read
    assert saving.read_fraction == 1.0, saving.read_fraction
    assert saving.read_cost_usd == round(2000 * 2.0 / 1e6 * 0.10, 6)
    assert saving.read_cost_if_uncached_usd == round(2000 * 2.0 / 1e6, 6)
    assert saving.saved_on_read_usd == round(2000 * 2.0 / 1e6 * 0.9, 6)
    assert saving.write_premium_usd == round(2000 * 2.0 / 1e6 * 0.25, 6)
    print("ok  a 2000-token hit at $2/MTok saves $0.0036 and cost a $0.0010 premium")


def test_the_net_saving_is_the_read_saving_minus_the_write_premium():
    """R1: the headline number, and it is positive on the first re-read already."""
    saving = report.Saving(written=2000, read=2000, base_usd_per_mtok=2.0)

    expected = round(saving.saved_on_read_usd - saving.write_premium_usd, 6)
    assert saving.net_saving_usd == expected, saving.net_saving_usd
    assert saving.net_saving_usd > 0
    print("ok  net saving is the read saving minus the once-paid write premium")


def test_a_write_nobody_read_is_a_loss_not_a_zero():
    """R2: the 1.25x premium is real money, so an unread write must read negative."""
    saving = report.Saving(written=2000, read=0, base_usd_per_mtok=2.0)

    assert saving.read_fraction == 0.0, saving.read_fraction
    assert saving.saved_on_read_usd == 0.0
    assert saving.net_saving_usd < 0
    assert saving.net_saving_usd == -saving.write_premium_usd
    print("ok  a cache write that is never read reports a loss, not a zero")


def test_a_run_that_cached_nothing_does_not_divide_by_zero():
    """R2: 0 written is a true 0.0 fraction, not a ZeroDivisionError."""
    saving = report.Saving(written=0, read=0, base_usd_per_mtok=2.0)

    assert saving.read_fraction == 0.0
    assert saving.net_saving_usd == 0.0
    print("ok  a run with no cache at all reports 0.0, not a crash")


def test_the_three_counters_partition_the_prompt():
    """R1: `input_tokens` is the remainder after the last breakpoint, not the total."""
    usage = report.TurnUsage(
        cache_creation_input_tokens=120, cache_read_input_tokens=3000, input_tokens=45
    )

    assert usage.total_input_tokens == 3165, usage.total_input_tokens
    print("ok  total prompt = input + cache_creation + cache_read")


# --------------------------------------------------------------------------- #
# R3: boundary failures
# --------------------------------------------------------------------------- #


def test_a_negative_or_non_integer_counter_is_rejected():
    """R3: a `None` counter reaching the arithmetic would read as a free run."""
    _rejects(
        ValueError,
        lambda: report.TurnUsage(
            cache_creation_input_tokens=-1, cache_read_input_tokens=0, input_tokens=0
        ),
        "a negative cache_creation_input_tokens",
    )
    _rejects(
        TypeError,
        lambda: report.TurnUsage(
            cache_creation_input_tokens=None, cache_read_input_tokens=0, input_tokens=0
        ),
        "a None cache_creation_input_tokens",
    )
    _rejects(
        ValueError,
        lambda: report.Saving(written=10, read=-1, base_usd_per_mtok=2.0),
        "a negative read count",
    )
    print("ok  a negative or non-int token counter raises at construction")


def test_a_negative_price_is_rejected():
    """R3: a negative rate would turn a saving into a profit."""
    _rejects(
        ValueError,
        lambda: report.Saving(written=10, read=10, base_usd_per_mtok=-2.0),
        "a negative base rate",
    )
    print("ok  a negative base rate raises instead of inverting the report")


# --------------------------------------------------------------------------- #
# R4: rendering
# --------------------------------------------------------------------------- #


def test_render_shows_both_counts_and_every_dollar_figure():
    """R4: the report is the deliverable; nothing it computes may go unprinted."""
    text = report.render(report.Saving(written=2000, read=2000, base_usd_per_mtok=2.0))

    assert "2000 tokens" in text
    assert "$0.000400" in text  # the cached read
    assert "$0.004000" in text  # the same tokens uncached
    assert "$0.003600" in text  # saved on the read
    assert "$0.001000" in text  # the write premium
    assert "$0.002600" in text  # net
    assert "$2.00/MTok" in text
    assert not text.endswith("\n")
    print("ok  render prints both counts, the rate, and all five dollar figures")


# --------------------------------------------------------------------------- #
# R5: the usage adapter - the one place the SDK response shape is touched
# --------------------------------------------------------------------------- #


def test_the_adapter_reads_the_three_1x_counter_names():
    """R5: `cache_creation_input_tokens` / `cache_read_input_tokens` / `input_tokens`."""
    usage = main._usage_of(_response(_text_reply(), _usage(3000, 0, 12)))

    assert usage.cache_creation_input_tokens == 3000
    assert usage.cache_read_input_tokens == 0
    assert usage.input_tokens == 12
    print("ok  the adapter reads the three input counters the 1.x SDK reports")


def test_a_null_counter_becomes_zero_and_a_renamed_one_raises():
    """R5: `None` is the API's empty bucket; a missing field is a changed schema."""
    usage = main._usage_of(_response(_text_reply(), _usage(None, None, 7)))
    assert usage.cache_creation_input_tokens == 0
    assert usage.cache_read_input_tokens == 0

    renamed = SimpleNamespace(
        cache_creation_tokens=3000, cache_read_input_tokens=0, input_tokens=1
    )
    _rejects(
        AttributeError,
        lambda: main._usage_of(_response(_text_reply(), renamed)),
        "a usage object missing cache_creation_input_tokens",
    )
    _rejects(
        TypeError,
        lambda: main._usage_of(_response(_text_reply(), _usage("3000", 0, 1))),
        "a string counter",
    )
    print("ok  a null counter is 0; a renamed or non-int one fails loudly")


# --------------------------------------------------------------------------- #
# R6-R7: the two-turn run
# --------------------------------------------------------------------------- #


def test_the_run_makes_exactly_two_calls_over_one_identical_prefix():
    """R6: the cache hit's precondition - `tools` and `system` byte-identical."""
    client = _two_turn_client()

    main.run(client, model="claude-sonnet-5", base_rate=2.0)

    assert len(client.calls) == 2, len(client.calls)
    first, second = client.calls
    assert first["system"] == second["system"]
    assert first["tools"] == second["tools"]
    assert first["model"] == second["model"] == "claude-sonnet-5"
    # Turn 2 extends turn 1; it does not rebuild it.
    assert second["messages"][0]["content"] == first["messages"][0]["content"]
    assert len(second["messages"]) == 3, len(second["messages"])
    print("ok  two calls, one byte-identical tools+system prefix, a grown message list")


def test_only_the_second_request_carries_a_message_breakpoint():
    """R6: turn 1 has no frozen tail to mark; turn 2's tool_result is the tail."""
    client = _two_turn_client()

    main.run(client, model="claude-sonnet-5", base_rate=2.0)
    first, second = client.calls

    def markers(messages):
        return [
            (m, b)
            for m, message in enumerate(messages)
            if not isinstance(message["content"], str)
            for b, block in enumerate(message["content"])
            if placement.CACHE_CONTROL_KEY in block
        ]

    assert markers(first["messages"]) == []
    assert markers(second["messages"]) == [(2, 0)]
    print("ok  the rolling breakpoint lands on turn 2's frozen tail, and only there")


def test_every_tool_use_in_the_reply_is_answered():
    """R6: the API rejects a turn that leaves one `tool_use` unanswered."""
    blocks = [
        FakeBlock(type="tool_use", id="toolu_a", name="calculator", input={}),
        FakeBlock(type="tool_use", id="toolu_b", name="word_count", input={}),
    ]
    client = _two_turn_client(first_blocks=blocks)

    main.run(client, model="claude-sonnet-5", base_rate=2.0)
    tool_results = client.calls[1]["messages"][2]["content"]

    assert [block["tool_use_id"] for block in tool_results] == ["toolu_a", "toolu_b"]
    assert all(block["type"] == "tool_result" for block in tool_results)
    print("ok  every tool_use block is answered, so the second turn is well-formed")


def test_a_reply_with_no_tool_call_still_grows_the_turn():
    """R6: the demo needs a longer second request, not a correct tool result."""
    client = _two_turn_client(first_blocks=_text_reply())

    main.run(client, model="claude-sonnet-5", base_rate=2.0)
    second_messages = client.calls[1]["messages"]

    assert len(second_messages) == 3, len(second_messages)
    assert second_messages[1]["content"][0]["type"] == "text"
    # The text block's `citations: None` is dropped rather than sent as a null.
    assert "citations" not in second_messages[1]["content"][0]
    assert second_messages[2]["content"][0]["text"] == main.FOLLOW_UP_TEXT
    print("ok  a tool-free reply still produces a longer second request")


def test_the_run_reports_the_saving_from_the_two_usages():
    """R6: turn 1's creation and turn 2's read, in that order, not crossed."""
    client = _two_turn_client(creation=3000, read=3000, delta=120)

    saving = main.run(client, model="claude-sonnet-5", base_rate=2.0)

    assert saving.written == 3000, saving.written
    assert saving.read == 3000, saving.read
    assert saving.net_saving_usd > 0
    print("ok  the report pairs turn 1's write with turn 2's read")


def test_a_missing_cache_hit_raises_instead_of_reporting_nothing():
    """R7: three ways the prefix can fail to survive; all three are loud."""
    cases = {
        "turn 1 wrote nothing": _two_turn_client(creation=0, read=0),
        "turn 2 read nothing": _two_turn_client(creation=3000, read=0),
        "turn 2 read a fraction back": _two_turn_client(creation=3000, read=100),
    }
    for label, client in cases.items():
        _rejects(
            main.CacheMiss,
            lambda client=client: main.run(
                client, model="claude-sonnet-5", base_rate=2.0
            ),
            label,
        )
    # And the diagnostic points at the checklist rather than just failing.
    try:
        main.run(_two_turn_client(creation=3000, read=0), model="m", base_rate=2.0)
    except main.CacheMiss as exc:
        assert "README.md" in str(exc), str(exc)
        assert "1,024 tokens" in str(exc)
    print("ok  a run that paid twice and cached nothing raises CacheMiss")


# --------------------------------------------------------------------------- #
# R8: the no-key path
# --------------------------------------------------------------------------- #


def test_without_a_key_main_prints_one_line_and_exits_zero():
    """R8: a skip, not a failure - and it happens before the SDK is even imported."""
    saved = os.environ.pop(main.API_KEY_ENV, None)
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            code = main.main()
    finally:
        if saved is not None:
            os.environ[main.API_KEY_ENV] = saved

    assert code == 0, code
    assert code == main.EXIT_NO_KEY
    assert stderr.getvalue().count("\n") == 1, repr(stderr.getvalue())
    assert main.API_KEY_ENV in stderr.getvalue()
    assert "anthropic" not in sys.modules, "the no-key path imported the SDK"
    print("ok  no key: one line on stderr, exit 0, no SDK import and no call")


# --------------------------------------------------------------------------- #


def main_() -> int:
    tests = [
        test_summarize_prices_turn_ones_write_against_turn_twos_read,
        test_the_net_saving_is_the_read_saving_minus_the_write_premium,
        test_a_write_nobody_read_is_a_loss_not_a_zero,
        test_a_run_that_cached_nothing_does_not_divide_by_zero,
        test_the_three_counters_partition_the_prompt,
        test_a_negative_or_non_integer_counter_is_rejected,
        test_a_negative_price_is_rejected,
        test_render_shows_both_counts_and_every_dollar_figure,
        test_the_adapter_reads_the_three_1x_counter_names,
        test_a_null_counter_becomes_zero_and_a_renamed_one_raises,
        test_the_run_makes_exactly_two_calls_over_one_identical_prefix,
        test_only_the_second_request_carries_a_message_breakpoint,
        test_every_tool_use_in_the_reply_is_answered,
        test_a_reply_with_no_tool_call_still_grows_the_turn,
        test_the_run_reports_the_saving_from_the_two_usages,
        test_a_missing_cache_hit_raises_instead_of_reporting_nothing,
        test_without_a_key_main_prints_one_line_and_exits_zero,
    ]
    started = time.monotonic()
    for test in tests:
        test()
    elapsed = time.monotonic() - started

    # Checked rather than claimed in prose: the whole shell - adapter, run loop,
    # cache-miss guard and the no-key path - was exercised without the SDK ever
    # being imported, so nothing here could have reached the network.
    assert "anthropic" not in sys.modules, "the self-test imported the SDK"
    assert elapsed < 1.0, f"self-test took {elapsed:.3f}s; something did I/O"

    print(f"\nAll {len(tests)} self-tests passed with no key and no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
