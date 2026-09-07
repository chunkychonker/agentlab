"""Offline self-test for the payback arithmetic and the shell that feeds it. No
API key, no network, no `anthropic` install.

Run:
    python3 test_payback.py

Each test maps to an acceptance criterion from the research note
(research/2026-09-07-context-editing-cache-tradeoff.md):

  B1. the payback model     -> 20,000 rewritten against 160,000 removed pays
                               back in 1.438 turns; 10,000 against 12,000 takes
                               9.583; the base rate cancels out of both
  B2. degenerate clears     -> removed=0 is `inf`, not a ZeroDivisionError
  B3. boundary failures     -> negative or non-int counts, negative rate
  B4. render                -> every figure printed, no trailing newline
  B5. the usage adapter     -> the three 1.x field names, null -> 0, a renamed
                               field raises
  B6. the applied_edits     -> cleared_input_tokens / cleared_tool_uses off the
      adapter                  generation response; absent -> NoInvalidation
  B7. the run               -> two free counts, two billed creates, the edit and
                               the beta on both, breakpoints inside the cap
  B8. the three gates       -> NoInvalidation / PreviewMismatch / NoRecache
                               instead of a report nothing supports, and the
                               first two fire before turn B is paid for
  B9. no key                -> one line, exit 0, no client ever constructed
"""

from __future__ import annotations

import contextlib
import io
import math
import os
import sys
import time
from types import SimpleNamespace

import compose
import main
import payback

# --------------------------------------------------------------------------- #
# Test doubles: the shapes `anthropic` 1.x returns, and nothing more
# --------------------------------------------------------------------------- #


def _count_response(input_tokens: int, original: int | None = None) -> SimpleNamespace:
    """A `beta.messages.count_tokens` response.

    `context_management` carries `original_input_tokens` and **no**
    `applied_edits` - the documented asymmetry between the two endpoints.
    """
    return SimpleNamespace(
        input_tokens=input_tokens,
        context_management=(
            None
            if original is None
            else SimpleNamespace(original_input_tokens=original)
        ),
    )


def _create_response(
    *,
    creation: int,
    read: int,
    fresh: int = 5,
    cleared_tokens: int | None = 4000,
    cleared_uses: int = 9,
) -> SimpleNamespace:
    """A `beta.messages.create` response with `usage` and `applied_edits`."""
    context_management = (
        None
        if cleared_tokens is None
        else SimpleNamespace(
            applied_edits=[
                SimpleNamespace(
                    type=compose.STRATEGY,
                    cleared_input_tokens=cleared_tokens,
                    cleared_tool_uses=cleared_uses,
                )
            ]
        )
    )
    return SimpleNamespace(
        usage=SimpleNamespace(
            cache_creation_input_tokens=creation,
            cache_read_input_tokens=read,
            input_tokens=fresh,
        ),
        content=[SimpleNamespace(type="text", text="done")],
        context_management=context_management,
    )


class FakeBetaMessages:
    """Stands in for `client.beta.messages`: records kwargs, replays responses."""

    def __init__(self, counts, creates):
        self._counts = list(counts)
        self._creates = list(creates)
        self.count_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def count_tokens(self, **kwargs):
        self.count_calls.append(kwargs)
        if not self._counts:
            raise AssertionError("fake client ran out of scripted count responses")
        return self._counts.pop(0)

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        if not self._creates:
            raise AssertionError("fake client ran out of scripted create responses")
        return self._creates.pop(0)


class FakeClient:
    """A client exposing only `.beta.messages` - all `run()` is allowed to use."""

    def __init__(self, counts, creates):
        self.messages = FakeBetaMessages(counts, creates)
        self.beta = SimpleNamespace(messages=self.messages)


def _healthy_client(
    *,
    original: int = 6000,
    edited: int = 2000,
    creation: int = 2000,
    turn_b_read: int = 1950,
    cleared_tokens: int = 4000,
) -> FakeClient:
    """A client scripted for a run where everything worked.

    The clear removes 4,000 tokens and forces a 2,000-token re-write, so payback
    lands at `11.5 * 2000 / 4000 = 5.75` turns.
    """
    return FakeClient(
        counts=[_count_response(original), _count_response(edited, original)],
        creates=[
            _create_response(
                creation=creation, read=300, cleared_tokens=cleared_tokens
            ),
            _create_response(
                creation=200, read=turn_b_read, cleared_tokens=cleared_tokens
            ),
        ],
    )


def _rejects(exception_type, call, label: str) -> None:
    try:
        call()
    except exception_type:
        return
    raise AssertionError(f"{label} was accepted; expected {exception_type.__name__}")


# --------------------------------------------------------------------------- #
# B1-B2: the payback model
# --------------------------------------------------------------------------- #


def test_a_clear_that_frees_far_more_than_it_rewrites_pays_back_in_under_two_turns():
    """B1: the note's first worked example - 160,000 removed, 20,000 rewritten."""
    tradeoff = payback.Tradeoff(
        removed=160_000, rewritten=20_000, base_usd_per_mtok=2.0
    )

    assert tradeoff.payback_turns == round(11.5 * 20_000 / 160_000, 3)
    assert tradeoff.payback_turns == 1.438, tradeoff.payback_turns
    assert tradeoff.invalidation_cost_usd == round(20_000 * 1.15 * 2.0 / 1e6, 6)
    assert tradeoff.saving_per_future_turn_usd == round(160_000 * 0.10 * 2.0 / 1e6, 6)
    print("ok  160,000 removed against 20,000 rewritten pays back in 1.438 turns")


def test_a_clear_that_barely_beats_its_own_rewrite_takes_ten_turns():
    """B1: the second worked example - the `clear_at_least` lesson, as a number."""
    tradeoff = payback.Tradeoff(
        removed=12_000, rewritten=10_000, base_usd_per_mtok=2.0
    )

    assert tradeoff.payback_turns == round(11.5 * 10_000 / 12_000, 3)
    assert tradeoff.payback_turns == 9.583, tradeoff.payback_turns
    print("ok  12,000 removed against 10,000 rewritten takes 9.583 turns")


def test_payback_is_a_token_ratio_and_ignores_the_price():
    """B1: the base rate cancels, which is why `clear_at_least` is the only lever."""
    turns = {
        rate: payback.Tradeoff(
            removed=43_060, rewritten=5_000, base_usd_per_mtok=rate
        ).payback_turns
        for rate in (1.0, 2.0, 5.0, 10.0)
    }

    assert len(set(turns.values())) == 1, turns
    assert set(turns.values()) == {round(11.5 * 5_000 / 43_060, 3)}
    print("ok  payback is identical at $1, $2, $5 and $10 per MTok")


def test_summarize_reads_rewritten_off_the_clearing_turns_write():
    """B1: `rewritten` is the clearing turn's creation count, never its read."""
    clearing_turn = payback.TurnUsage(
        cache_creation_input_tokens=5_000,
        cache_read_input_tokens=900,
        input_tokens=12,
    )

    tradeoff = payback.summarize(
        clearing_turn, removed=43_060, base_usd_per_mtok=2.0
    )

    assert tradeoff.rewritten == 5_000
    assert tradeoff.removed == 43_060
    assert tradeoff.payback_turns == round(11.5 * 5_000 / 43_060, 3)
    print("ok  summarize takes rewritten from cache_creation_input_tokens")


def test_a_clear_that_removed_nothing_never_pays_back():
    """B2: `inf` is a true statement about a wasted write, not a crash."""
    tradeoff = payback.Tradeoff(removed=0, rewritten=5_000, base_usd_per_mtok=2.0)

    assert math.isinf(tradeoff.payback_turns)
    assert tradeoff.saving_per_future_turn_usd == 0.0
    assert tradeoff.invalidation_cost_usd > 0
    print("ok  removed=0 reports an infinite payback, not a ZeroDivisionError")


def test_the_three_counters_partition_the_prompt():
    """B1: `input_tokens` is the remainder after the last breakpoint, not the total."""
    usage = payback.TurnUsage(
        cache_creation_input_tokens=2_000,
        cache_read_input_tokens=300,
        input_tokens=45,
    )

    assert usage.total_input_tokens == 2_345, usage.total_input_tokens
    print("ok  total prompt = input + cache_creation + cache_read")


# --------------------------------------------------------------------------- #
# B3: boundary failures
# --------------------------------------------------------------------------- #


def test_a_negative_or_non_integer_counter_is_rejected():
    """B3: a `None` counter reaching the arithmetic would read as a free clear."""
    _rejects(
        ValueError,
        lambda: payback.TurnUsage(
            cache_creation_input_tokens=-1,
            cache_read_input_tokens=0,
            input_tokens=0,
        ),
        "a negative cache_creation_input_tokens",
    )
    _rejects(
        TypeError,
        lambda: payback.TurnUsage(
            cache_creation_input_tokens=None,
            cache_read_input_tokens=0,
            input_tokens=0,
        ),
        "a None cache_creation_input_tokens",
    )
    _rejects(
        ValueError,
        lambda: payback.Tradeoff(
            removed=-1, rewritten=10, base_usd_per_mtok=2.0
        ),
        "a negative removed",
    )
    _rejects(
        TypeError,
        lambda: payback.Tradeoff(
            removed=10, rewritten="20", base_usd_per_mtok=2.0
        ),
        "a string rewritten",
    )
    print("ok  a negative or non-int token count raises at construction")


def test_a_negative_price_is_rejected():
    """B3: a negative rate would invert both halves of the trade."""
    _rejects(
        ValueError,
        lambda: payback.Tradeoff(
            removed=10, rewritten=10, base_usd_per_mtok=-2.0
        ),
        "a negative base rate",
    )
    print("ok  a negative base rate raises instead of inverting the report")


# --------------------------------------------------------------------------- #
# B4: rendering
# --------------------------------------------------------------------------- #


def test_render_shows_both_counts_the_two_dollar_figures_and_the_payback():
    """B4: the report is the deliverable; nothing it computes may go unprinted."""
    text = payback.render(
        payback.Tradeoff(removed=160_000, rewritten=20_000, base_usd_per_mtok=2.0)
    )

    assert "160000 tokens" in text
    assert "20000 tokens" in text
    assert "$0.046000" in text  # the one-time invalidation premium
    assert "$0.032000" in text  # what every later turn saves
    assert "1.438 turns" in text
    assert "$2.00/MTok" in text
    assert text
    assert not text.endswith("\n")
    print("ok  render prints both counts, both dollar figures and the payback")


def test_render_says_never_rather_than_printing_infinity():
    """B4: `inf turns` is not a sentence; a clear that freed nothing is `never`."""
    text = payback.render(
        payback.Tradeoff(removed=0, rewritten=20_000, base_usd_per_mtok=2.0)
    )

    assert "never" in text
    assert "inf" not in text
    assert not text.endswith("\n")
    print("ok  an infinite payback renders as 'never', not 'inf turns'")


# --------------------------------------------------------------------------- #
# B5-B6: the two places the SDK response shape is touched
# --------------------------------------------------------------------------- #


def test_the_usage_adapter_reads_the_three_1x_counters():
    """B5: field names are the contract with the SDK, so they are asserted."""
    usage = main._usage_of(_create_response(creation=2_000, read=300, fresh=7))

    assert usage.cache_creation_input_tokens == 2_000
    assert usage.cache_read_input_tokens == 300
    assert usage.input_tokens == 7
    print("ok  the usage adapter reads the three anthropic 1.x counters")


def test_a_null_counter_is_zero_and_a_renamed_one_raises():
    """B5: `None` is "no tokens in this bucket"; a *missing* field is a rename."""
    nulled = SimpleNamespace(
        usage=SimpleNamespace(
            cache_creation_input_tokens=None,
            cache_read_input_tokens=None,
            input_tokens=11,
        )
    )
    usage = main._usage_of(nulled)
    assert usage.cache_creation_input_tokens == 0
    assert usage.cache_read_input_tokens == 0

    renamed = SimpleNamespace(
        usage=SimpleNamespace(
            cache_creation_tokens=2_000,  # the 1.x name, dropped
            cache_read_input_tokens=0,
            input_tokens=11,
        )
    )
    _rejects(AttributeError, lambda: main._usage_of(renamed), "a renamed counter")
    print("ok  a null counter is 0 and a renamed one raises AttributeError")


def test_the_applied_edits_adapter_reads_what_the_clear_actually_did():
    """B6: the generation response's numbers, the ones `count_tokens` cannot give."""
    response = _create_response(
        creation=2_000, read=300, cleared_tokens=4_321, cleared_uses=9
    )

    assert main._cleared_input_tokens(response) == 4_321
    assert main._cleared_tool_uses(response) == 9
    print("ok  the applied_edits adapter reads cleared tokens and cleared uses")


def test_a_response_without_applied_edits_is_a_loud_failure():
    """B6: no edit fired, so there is nothing to attribute a cache write to."""
    missing = _create_response(creation=2_000, read=300, cleared_tokens=None)
    empty = SimpleNamespace(
        usage=missing.usage,
        context_management=SimpleNamespace(applied_edits=[]),
    )

    _rejects(
        main.NoInvalidation,
        lambda: main._cleared_input_tokens(missing),
        "a response with no context_management",
    )
    _rejects(
        main.NoInvalidation,
        lambda: main._cleared_input_tokens(empty),
        "a response with an empty applied_edits list",
    )
    print("ok  a response without applied_edits raises NoInvalidation")


# --------------------------------------------------------------------------- #
# B7: the run
# --------------------------------------------------------------------------- #


def test_the_run_counts_twice_for_free_then_generates_twice():
    """B7: two free counts, two billed creates - and not one call more."""
    client = _healthy_client()

    report = main.run(client, model="test-model", base_rate=2.0)

    assert len(client.messages.count_calls) == 2, client.messages.count_calls
    assert len(client.messages.create_calls) == 2, client.messages.create_calls

    plain, edited = client.messages.count_calls
    assert "context_management" not in plain
    assert edited["context_management"] == compose.clearing_config(
        keep_tool_uses=main.KEEP, trigger_tool_uses=main.TRIGGER
    )
    assert plain["betas"] == [compose.BETA] and edited["betas"] == [compose.BETA]
    assert "removed       4000 tokens" in report
    print("ok  the run makes two free counts and exactly two billed generations")


def test_both_billed_turns_send_the_edit_the_beta_and_a_stable_prefix():
    """B7: only `messages` may differ between the turns; everything else is the
    prefix the cache is keyed on."""
    client = _healthy_client()

    main.run(client, model="test-model", base_rate=2.0)
    first, second = client.messages.create_calls

    assert first["system"] == second["system"]
    assert first["tools"] == second["tools"]
    assert first["context_management"] == second["context_management"]
    assert first["betas"] == second["betas"] == [compose.BETA]
    assert first["model"] == second["model"] == "test-model"
    assert len(second["messages"]) == len(first["messages"]) + 2
    print("ok  both turns send the same tools, system, edit and beta")


def test_the_billed_turns_stay_inside_the_breakpoint_cap():
    """B7: two static markers plus what the message list carries is at most four."""
    client = _healthy_client()

    main.run(client, model="test-model", base_rate=2.0)

    for call in client.messages.create_calls:
        in_messages = sum(
            1
            for message in call["messages"]
            if isinstance(message["content"], list)
            for block in message["content"]
            if compose.CACHE_CONTROL_KEY in block
        )
        in_tools = sum(
            1 for tool in call["tools"] if compose.CACHE_CONTROL_KEY in tool
        )
        in_system = sum(
            1 for block in call["system"] if compose.CACHE_CONTROL_KEY in block
        )
        assert in_messages == main.MESSAGES_BUDGET, in_messages
        assert in_tools + in_system == main.STATIC_BREAKPOINTS
        total = in_messages + in_tools + in_system
        assert total == compose.MAX_BREAKPOINTS, total
    print("ok  every billed request carries exactly the four allowed breakpoints")


def test_the_report_carries_the_payback_the_runs_own_numbers_imply():
    """B7: acceptance criterion 5 - the printed figure is `11.5 x rewritten /
    removed` for this run's counters, not a number from anywhere else."""
    client = _healthy_client(creation=2_000, cleared_tokens=4_000)

    report = main.run(client, model="test-model", base_rate=2.0)

    expected = round(11.5 * 2_000 / 4_000, 3)
    assert expected == 5.75
    assert f"{expected} turns" in report, report
    assert "cleared 9 tool uses, 4000 input tokens" in report
    print("ok  the report prints the payback this run's own counters imply")


# --------------------------------------------------------------------------- #
# B8: the gates
# --------------------------------------------------------------------------- #


def test_a_clearing_turn_that_wrote_nothing_never_reaches_turn_b():
    """B8: a run that measured nothing must not pay for a second generation."""
    client = _healthy_client(creation=0)

    _rejects(
        main.NoInvalidation,
        lambda: main.run(client, model="test-model", base_rate=2.0),
        "a clearing turn with cache_creation_input_tokens=0",
    )
    assert len(client.messages.create_calls) == 1, "turn B was paid for anyway"
    print("ok  a clearing turn that wrote nothing raises before turn B is billed")


def test_a_preview_that_contradicts_the_bill_stops_the_run():
    """B8: two measurements of one edit disagree, so neither is reported."""
    # Preview says 4,000 removed; the billed response says 6,000.
    client = _healthy_client(cleared_tokens=6_000)

    _rejects(
        main.PreviewMismatch,
        lambda: main.run(client, model="test-model", base_rate=2.0),
        "a preview 50% away from the billed figure",
    )
    assert len(client.messages.create_calls) == 1, "turn B was paid for anyway"
    print("ok  a preview disagreeing with the bill raises before turn B is billed")


def test_a_small_tokenizer_difference_between_the_endpoints_is_tolerated():
    """B8: 2% of slack, because the two endpoints are not required to agree to
    the token - but 2% is slack, not a disagreement about what was cleared."""
    client = _healthy_client(cleared_tokens=4_040)  # 1% above the preview's 4,000

    report = main.run(client, model="test-model", base_rate=2.0)

    assert f"{round(11.5 * 2_000 / 4_040, 3)} turns" in report
    print("ok  a 1% gap between the free and billed figures is tolerated")


def test_a_prefix_that_is_never_read_back_stops_the_run():
    """B8: the write the clear forced has to be reusable or there is no payback."""
    client = _healthy_client(creation=2_000, turn_b_read=400)  # 20% of the write

    _rejects(
        main.NoRecache,
        lambda: main.run(client, model="test-model", base_rate=2.0),
        "a turn B that read back 20% of the write",
    )
    assert len(client.messages.create_calls) == 2
    print("ok  a re-cached prefix nobody read back raises NoRecache")


def test_a_preview_reporting_no_edit_at_all_stops_the_run_before_any_spend():
    """B8: the trigger never fired, so nothing billed should be attempted."""
    client = FakeClient(
        counts=[_count_response(6_000), _count_response(6_000, None)],
        creates=[],
    )

    _rejects(
        main.NoInvalidation,
        lambda: main.run(client, model="test-model", base_rate=2.0),
        "a preview with no context_management on the count response",
    )
    assert client.messages.create_calls == [], "money was spent after a dead preview"
    print("ok  a preview reporting no edit stops the run before anything is billed")


def test_the_two_free_counts_must_agree_about_the_original_size():
    """B8: the plain count is the independent check on the API's own original."""
    client = FakeClient(
        counts=[_count_response(6_000), _count_response(2_000, 5_500)],
        creates=[],
    )

    _rejects(
        main.PreviewMismatch,
        lambda: main.run(client, model="test-model", base_rate=2.0),
        "two counts disagreeing about the original size",
    )
    assert client.messages.create_calls == []
    print("ok  the plain count and the reported original must agree")


# --------------------------------------------------------------------------- #
# B9: no key
# --------------------------------------------------------------------------- #


def test_no_key_prints_one_line_and_exits_zero_without_a_client():
    """B9: a missing key is a $0 skip, and a skip is not a failure."""
    saved = os.environ.pop(main.API_KEY_ENV, None)
    stderr = io.StringIO()
    try:
        with contextlib.redirect_stderr(stderr):
            code = main.main()
    finally:
        if saved is not None:
            os.environ[main.API_KEY_ENV] = saved

    assert code == main.EXIT_NO_KEY == 0, code
    lines = stderr.getvalue().strip().splitlines()
    assert len(lines) == 1, lines
    assert main.API_KEY_ENV in lines[0]
    assert "anthropic" not in sys.modules, "the SDK was imported without a key"
    print("ok  no key prints one line, exits 0, and never imports the SDK")


# --------------------------------------------------------------------------- #


def main_() -> int:
    tests = [
        test_a_clear_that_frees_far_more_than_it_rewrites_pays_back_in_under_two_turns,
        test_a_clear_that_barely_beats_its_own_rewrite_takes_ten_turns,
        test_payback_is_a_token_ratio_and_ignores_the_price,
        test_summarize_reads_rewritten_off_the_clearing_turns_write,
        test_a_clear_that_removed_nothing_never_pays_back,
        test_the_three_counters_partition_the_prompt,
        test_a_negative_or_non_integer_counter_is_rejected,
        test_a_negative_price_is_rejected,
        test_render_shows_both_counts_the_two_dollar_figures_and_the_payback,
        test_render_says_never_rather_than_printing_infinity,
        test_the_usage_adapter_reads_the_three_1x_counters,
        test_a_null_counter_is_zero_and_a_renamed_one_raises,
        test_the_applied_edits_adapter_reads_what_the_clear_actually_did,
        test_a_response_without_applied_edits_is_a_loud_failure,
        test_the_run_counts_twice_for_free_then_generates_twice,
        test_both_billed_turns_send_the_edit_the_beta_and_a_stable_prefix,
        test_the_billed_turns_stay_inside_the_breakpoint_cap,
        test_the_report_carries_the_payback_the_runs_own_numbers_imply,
        test_a_clearing_turn_that_wrote_nothing_never_reaches_turn_b,
        test_a_preview_that_contradicts_the_bill_stops_the_run,
        test_a_small_tokenizer_difference_between_the_endpoints_is_tolerated,
        test_a_prefix_that_is_never_read_back_stops_the_run,
        test_a_preview_reporting_no_edit_at_all_stops_the_run_before_any_spend,
        test_the_two_free_counts_must_agree_about_the_original_size,
        test_no_key_prints_one_line_and_exits_zero_without_a_client,
    ]
    started = time.monotonic()
    for test in tests:
        test()
    elapsed = time.monotonic() - started

    # Checked rather than claimed in prose: the core and the whole shell were
    # exercised without the SDK ever being imported, so nothing here could have
    # reached the network or read a key.
    assert "anthropic" not in sys.modules, "the self-test imported the SDK"
    assert elapsed < 1.0, f"self-test took {elapsed:.3f}s; something did I/O"

    print(f"\nAll {len(tests)} self-tests passed with no key and no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
