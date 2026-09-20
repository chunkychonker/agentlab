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

and from research/2026-09-16-prompt-caching-1h-ttl.md:

  R9.  the 1-hour price    -> a 2x write is a 100% premium, four times the
                              5-minute one, and does not pay for itself on a
                              single read; the default is untouched
  R10. the TTL breakdown   -> a split that does not sum to
                              `cache_creation_input_tokens` is unconstructable;
                              the adapter reads the nested `usage.cache_creation`
  R11. the TTL guard       -> a 1-hour run whose write landed at 5 minutes
                              raises TTLMismatch and exits 3; a bad `--ttl`
                              exits 64 before anything is spent
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


def _ttl_usage(creation: int, read: int, fresh: int, *, five_m: int, one_h: int):
    """A `usage` that also carries the nested `cache_creation` breakdown.

    Shaped like `anthropic` 1.2.0's `Usage.cache_creation`, verified against the
    installed SDK: an optional object with `ephemeral_5m_input_tokens` and
    `ephemeral_1h_input_tokens`.
    """
    return SimpleNamespace(
        cache_creation_input_tokens=creation,
        cache_read_input_tokens=read,
        input_tokens=fresh,
        cache_creation=SimpleNamespace(
            ephemeral_5m_input_tokens=five_m,
            ephemeral_1h_input_tokens=one_h,
        ),
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


def _one_hour_client(*, creation=3000, read=3000, delta=120, wrote_5m=0):
    """A two-turn client whose server wrote `creation - wrote_5m` at 1 hour.

    `wrote_5m > 0` is the case the guard exists for: the request asked for the
    1-hour TTL and the server billed some or all of it at 5 minutes anyway.
    """
    return FakeClient(
        [
            _response(
                _tool_call(),
                _ttl_usage(
                    creation, 0, 4, five_m=wrote_5m, one_h=creation - wrote_5m
                ),
            ),
            _response(
                _text_reply(),
                _ttl_usage(delta, read, 2, five_m=0, one_h=delta),
            ),
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
# R9: the 1-hour price (research/2026-09-16-prompt-caching-1h-ttl.md)
# --------------------------------------------------------------------------- #


def test_the_one_hour_write_premium_is_a_full_extra_base_rate():
    """R9: 2x on the write is a 100% premium, not 25% - four times the money."""
    written = 2000
    base = 2.0
    hour = report.Saving(
        written=written,
        read=2000,
        base_usd_per_mtok=base,
        write_multiplier=report.CACHE_WRITE_1H_MULTIPLIER,
    )
    five = report.Saving(written=written, read=2000, base_usd_per_mtok=base)

    # The dollar figure, computed from the constants rather than asserted loosely.
    assert hour.write_premium_multiplier == 1.0, hour.write_premium_multiplier
    assert hour.write_premium_usd == round(written * base / 1e6 * 1.0, 6)
    assert hour.write_premium_usd == 0.004, hour.write_premium_usd
    assert hour.write_premium_usd == round(4 * five.write_premium_usd, 6)
    # The read side is identical: only the write multiplier differs.
    assert hour.read_cost_usd == five.read_cost_usd
    assert hour.saved_on_read_usd == five.saved_on_read_usd
    print("ok  a 1-hour write costs a 100% premium: 4x the 5-minute one, $0.004000")


def test_the_default_write_multiplier_is_still_the_five_minute_one():
    """R9: the regression guard - a caller that never heard of TTLs is unchanged."""
    implicit = report.Saving(written=2000, read=2000, base_usd_per_mtok=2.0)
    explicit = report.Saving(
        written=2000,
        read=2000,
        base_usd_per_mtok=2.0,
        write_multiplier=report.CACHE_WRITE_5M_MULTIPLIER,
    )

    assert implicit == explicit
    assert implicit.write_multiplier == report.CACHE_WRITE_5M_MULTIPLIER == 1.25
    assert implicit.write_premium_multiplier == 0.25
    assert implicit.write_premium_usd == 0.001
    print("ok  a Saving built without a multiplier still prices the write at 1.25x")


def test_the_one_hour_ttl_does_not_pay_for_itself_on_a_single_read():
    """R9: the break-even number the README states, derived from the constants.

    Reads needed to repay the premium = (write - 1) / (1 - read). At 1.25x that
    is 0.25/0.9 = 0.278 of a read, so the first read is already profit. At 2x it
    is 1.0/0.9 = 1.111 reads, so a prefix read back exactly once is a *loss* and
    the second read is what makes the 1-hour TTL worth buying.
    """
    per_read = 1 - report.CACHE_READ_MULTIPLIER
    five_break_even = (report.CACHE_WRITE_5M_MULTIPLIER - 1) / per_read
    hour_break_even = (report.CACHE_WRITE_1H_MULTIPLIER - 1) / per_read

    assert round(five_break_even, 4) == 0.2778, five_break_even
    assert round(hour_break_even, 4) == 1.1111, hour_break_even

    written = 3667  # the token count of the README's measured 5-minute run
    one_read = report.Saving(
        written=written,
        read=written,
        base_usd_per_mtok=2.0,
        write_multiplier=report.CACHE_WRITE_1H_MULTIPLIER,
    )
    two_reads = report.Saving(
        written=written,
        read=2 * written,  # the same prefix read back on two later turns
        base_usd_per_mtok=2.0,
        write_multiplier=report.CACHE_WRITE_1H_MULTIPLIER,
    )

    assert one_read.net_saving_usd == -0.000733, one_read.net_saving_usd
    assert two_reads.net_saving_usd == 0.005867, two_reads.net_saving_usd
    # The same prefix at 5 minutes is in profit after the first read already.
    assert report.Saving(
        written=written, read=written, base_usd_per_mtok=2.0
    ).net_saving_usd == 0.004767
    print("ok  break-even is 0.278 reads at 5 minutes and 1.111 at 1 hour")


def test_a_write_multiplier_below_one_is_rejected():
    """R9: a cache write is never cheaper than uncached input; below 1x is a bug."""
    _rejects(
        ValueError,
        lambda: report.Saving(
            written=10, read=10, base_usd_per_mtok=2.0, write_multiplier=0.9
        ),
        "a write multiplier below 1.0",
    )
    assert (
        report.Saving(
            written=10, read=10, base_usd_per_mtok=2.0, write_multiplier=1.0
        ).write_premium_usd
        == 0.0
    )
    print("ok  a write multiplier below 1.0 raises instead of inventing a discount")


def test_render_shows_whichever_premium_multiplier_was_paid():
    """R9: the printed report must not claim 0.25x on a run billed at 1.0x."""
    hour = report.render(
        report.Saving(
            written=2000,
            read=2000,
            base_usd_per_mtok=2.0,
            write_multiplier=report.CACHE_WRITE_1H_MULTIPLIER,
        )
    )
    five = report.render(report.Saving(written=2000, read=2000, base_usd_per_mtok=2.0))

    assert "(1.0x base, paid once)" in hour, hour
    assert "$0.004000" in hour  # the 1-hour premium
    # A single read does not repay a 2x write, and the report says so. (The sign
    # lands after the dollar sign - that is `render`'s existing format, unchanged.)
    assert "$-0.000400" in hour, hour
    assert "(0.25x base, paid once)" in five, five
    assert "$0.001000" in five
    print("ok  render prints the premium multiplier the run actually paid")


def test_summarize_passes_the_write_multiplier_through():
    """R9: the entry point picks the TTL's price; summarize must not re-decide it."""
    turn1 = report.TurnUsage(
        cache_creation_input_tokens=2000, cache_read_input_tokens=0, input_tokens=15
    )
    turn2 = report.TurnUsage(
        cache_creation_input_tokens=90, cache_read_input_tokens=2000, input_tokens=8
    )

    hour = report.summarize(
        turn1,
        turn2,
        base_usd_per_mtok=2.0,
        write_multiplier=report.CACHE_WRITE_1H_MULTIPLIER,
    )
    default = report.summarize(turn1, turn2, base_usd_per_mtok=2.0)

    assert hour.write_multiplier == 2.0
    assert default.write_multiplier == report.CACHE_WRITE_5M_MULTIPLIER
    assert hour.written == default.written == 2000
    print("ok  summarize prices the write at whatever multiplier it is handed")


# --------------------------------------------------------------------------- #
# R10: the nested TTL breakdown
# --------------------------------------------------------------------------- #


def test_a_ttl_breakdown_that_does_not_sum_is_unconstructable():
    """R10: the docs' identity, enforced - the flat counter *is* the sum."""
    _rejects(
        ValueError,
        lambda: report.TurnUsage(
            cache_creation_input_tokens=300,
            cache_read_input_tokens=0,
            input_tokens=5,
            ephemeral_5m_input_tokens=100,
            ephemeral_1h_input_tokens=100,
        ),
        "a breakdown summing to 200 against a flat count of 300",
    )
    _rejects(
        ValueError,
        lambda: report.TurnUsage(
            cache_creation_input_tokens=300,
            cache_read_input_tokens=0,
            input_tokens=5,
            ephemeral_5m_input_tokens=-1,
            ephemeral_1h_input_tokens=301,
        ),
        "a negative bucket",
    )
    _rejects(
        TypeError,
        lambda: report.TurnUsage(
            cache_creation_input_tokens=300,
            cache_read_input_tokens=0,
            input_tokens=5,
            ephemeral_5m_input_tokens="0",
            ephemeral_1h_input_tokens=300,
        ),
        "a string bucket",
    )

    # Sums correctly: fine. Partial or absent: no claim made, so no check.
    exact = report.TurnUsage(
        cache_creation_input_tokens=300,
        cache_read_input_tokens=0,
        input_tokens=5,
        ephemeral_5m_input_tokens=0,
        ephemeral_1h_input_tokens=300,
    )
    assert exact.ephemeral_1h_input_tokens == 300
    partial = report.TurnUsage(
        cache_creation_input_tokens=300,
        cache_read_input_tokens=0,
        input_tokens=5,
        ephemeral_1h_input_tokens=1,
    )
    assert partial.ephemeral_5m_input_tokens is None
    neither = report.TurnUsage(
        cache_creation_input_tokens=300, cache_read_input_tokens=0, input_tokens=5
    )
    assert neither.ephemeral_1h_input_tokens is None
    assert neither.total_input_tokens == 305
    print("ok  a TTL breakdown that contradicts the flat counter cannot be built")


def test_the_adapter_reads_the_nested_cache_creation_object():
    """R10: `usage.cache_creation` is optional in the SDK, so absence is not a rename."""
    usage = main._usage_of(
        _response(_text_reply(), _ttl_usage(3000, 0, 12, five_m=0, one_h=3000))
    )
    assert usage.cache_creation_input_tokens == 3000
    assert usage.ephemeral_5m_input_tokens == 0
    assert usage.ephemeral_1h_input_tokens == 3000

    # A response with no breakdown at all - every pre-TTL double in this file.
    without = main._usage_of(_response(_text_reply(), _usage(3000, 0, 12)))
    assert without.ephemeral_5m_input_tokens is None
    assert without.ephemeral_1h_input_tokens is None

    # A breakdown that contradicts the flat counter fails at the boundary.
    _rejects(
        ValueError,
        lambda: main._usage_of(
            _response(_text_reply(), _ttl_usage(3000, 0, 12, five_m=1, one_h=1))
        ),
        "a breakdown that does not sum",
    )
    print("ok  the adapter reads the nested breakdown, and its absence is not an error")


# --------------------------------------------------------------------------- #
# R11: the TTL guard and the flag
# --------------------------------------------------------------------------- #


def test_a_one_hour_run_marks_every_breakpoint_and_prices_the_write_at_2x():
    """R11: the whole opt-in path, end to end against a fake server."""
    client = _one_hour_client()

    saving = main.run(
        client, model="claude-sonnet-5", base_rate=2.0, ttl=placement.CACHE_TTL_1H
    )

    assert saving.write_multiplier == report.CACHE_WRITE_1H_MULTIPLIER
    assert saving.write_premium_usd == round(3000 * 2.0 / 1e6, 6)
    first, second = client.calls
    marker = {"type": "ephemeral", "ttl": "1h"}
    assert first["system"][0][placement.CACHE_CONTROL_KEY] == marker
    assert first["tools"][-1][placement.CACHE_CONTROL_KEY] == marker
    assert second["messages"][2]["content"][0][placement.CACHE_CONTROL_KEY] == marker
    # The prefix itself is identical across the turns, exactly as at 5 minutes.
    assert first["system"] == second["system"]
    assert first["tools"] == second["tools"]
    print("ok  a 1-hour run marks all four breakpoints and prices the write at 2x")


def test_a_one_hour_run_billed_at_five_minutes_raises_ttl_mismatch():
    """R11: proof, not trust - the note's whole reason for reading the breakdown."""
    _rejects(
        main.TTLMismatch,
        lambda: main.run(
            _one_hour_client(wrote_5m=3000),
            model="claude-sonnet-5",
            base_rate=2.0,
            ttl=placement.CACHE_TTL_1H,
        ),
        "a 1-hour request written entirely at 5 minutes",
    )
    _rejects(
        main.TTLMismatch,
        lambda: main.run(
            _one_hour_client(wrote_5m=1),
            model="claude-sonnet-5",
            base_rate=2.0,
            ttl=placement.CACHE_TTL_1H,
        ),
        "a 1-hour request with one token written at 5 minutes",
    )
    # No breakdown at all is also no proof, and a 2x bill is not assumed.
    _rejects(
        main.TTLMismatch,
        lambda: main.run(
            _two_turn_client(),
            model="claude-sonnet-5",
            base_rate=2.0,
            ttl=placement.CACHE_TTL_1H,
        ),
        "a 1-hour request whose response carried no breakdown",
    )
    # But the default path keeps working against exactly that response.
    assert main.run(_two_turn_client(), model="claude-sonnet-5", base_rate=2.0).written

    try:
        main.run(
            _one_hour_client(wrote_5m=3000),
            model="m",
            base_rate=2.0,
            ttl=placement.CACHE_TTL_1H,
        )
    except main.TTLMismatch as exc:
        assert "1h" in str(exc) and "5m=3000" in str(exc), str(exc)
    print("ok  a 1-hour run the server wrote at 5 minutes raises TTLMismatch")


def test_a_ttl_mismatch_exits_three_and_a_cache_miss_still_exits_two():
    """R11: a new failure gets a new exit code; the old one does not move."""
    assert main.EXIT_TTL_MISMATCH == 3
    assert main.EXIT_NO_CACHE_HIT == 2
    assert main.EXIT_USAGE == 64
    assert len({main.EXIT_TTL_MISMATCH, main.EXIT_NO_CACHE_HIT, main.EXIT_USAGE}) == 3
    assert issubclass(main.TTLMismatch, RuntimeError)
    assert not issubclass(main.TTLMismatch, main.CacheMiss)
    print("ok  TTLMismatch is its own failure with its own exit code, 3")


def test_parse_ttl_takes_the_two_documented_forms_and_nothing_else():
    """R11: a typo'd TTL must not silently fall back to the cheap default."""
    assert main.parse_ttl([]) is placement.CACHE_TTL_5M
    assert main.parse_ttl(["--ttl", "5m"]) is placement.CACHE_TTL_5M
    assert main.parse_ttl(["--ttl", "1h"]) is placement.CACHE_TTL_1H

    for argv in (
        ["--ttl"],
        ["--ttl", "1 hour"],
        ["--ttl", "3600"],
        ["--ttl", "1h", "extra"],
        ["1h"],
        ["--tll", "1h"],
    ):
        _rejects(main.UsageError, lambda argv=argv: main.parse_ttl(argv), repr(argv))
    print("ok  parse_ttl accepts '5m' and '1h' and refuses to guess at anything else")


def test_every_ttl_has_a_price():
    """R11: exhaustive over the enum - a new TTL cannot default to the cheap rate."""
    prices = {ttl: main.write_multiplier_for(ttl) for ttl in placement.CacheTTL}

    assert prices[placement.CACHE_TTL_5M] == report.CACHE_WRITE_5M_MULTIPLIER
    assert prices[placement.CACHE_TTL_1H] == report.CACHE_WRITE_1H_MULTIPLIER
    assert len(prices) == len(placement.CacheTTL)
    print("ok  every CacheTTL member has a write multiplier, checked exhaustively")


def test_a_bad_ttl_flag_exits_64_before_a_key_is_even_read():
    """R11: a usage error costs nothing - no key read, no SDK import, no call."""
    saved = os.environ.pop(main.API_KEY_ENV, None)
    os.environ[main.API_KEY_ENV] = "sk-ant-not-a-real-key"
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            code = main.main(["--ttl", "1 hour"])
    finally:
        del os.environ[main.API_KEY_ENV]
        if saved is not None:
            os.environ[main.API_KEY_ENV] = saved

    assert code == main.EXIT_USAGE, code
    assert "'5m'" in stderr.getvalue() and "'1h'" in stderr.getvalue()
    assert main.TTL_FLAG in stderr.getvalue()
    assert "anthropic" not in sys.modules, "the usage-error path imported the SDK"
    print("ok  a bad --ttl exits 64 with the usage line, before any key is read")


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
        test_the_one_hour_write_premium_is_a_full_extra_base_rate,
        test_the_default_write_multiplier_is_still_the_five_minute_one,
        test_the_one_hour_ttl_does_not_pay_for_itself_on_a_single_read,
        test_a_write_multiplier_below_one_is_rejected,
        test_render_shows_whichever_premium_multiplier_was_paid,
        test_summarize_passes_the_write_multiplier_through,
        test_a_ttl_breakdown_that_does_not_sum_is_unconstructable,
        test_the_adapter_reads_the_nested_cache_creation_object,
        test_a_one_hour_run_marks_every_breakpoint_and_prices_the_write_at_2x,
        test_a_one_hour_run_billed_at_five_minutes_raises_ttl_mismatch,
        test_a_ttl_mismatch_exits_three_and_a_cache_miss_still_exits_two,
        test_parse_ttl_takes_the_two_documented_forms_and_nothing_else,
        test_every_ttl_has_a_price,
        test_a_bad_ttl_flag_exits_64_before_a_key_is_even_read,
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
