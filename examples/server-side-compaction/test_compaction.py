"""Offline self-test for the server-side compaction example. No API key, no
network, no `anthropic` install.

Run:
    python3 test_compaction.py

Each test maps to an acceptance criterion from the research note
(research/2026-08-12-server-side-compaction.md):

  A1. this file            -> exits 0 with no key, no network, no SDK installed
  A2. the policy's floors  -> 49_999 rejected, 50_000 accepted, blank
                              instructions rejected, absent instructions accepted
  A3. minimal serialisation-> exactly {type, trigger, pause_after_compaction},
                              by dict equality, so a stray "instructions": null
                              fails
  A4. the docs' own usage  -> 180000 compaction input tokens and 207500 billed,
                              not the 46234 the top-level fields imply
  A5. content: null        -> triggered, paused, summary None, nothing sliced
  A6. no compaction        -> triggered False, billed from the top-level usage
  A7. continuation         -> original + one assistant message, encrypted_content
                              intact
  A8. preflight            -> go above the trigger, no-go with a shortfall below

The fixtures below are the ones A4-A6 use, and they are module-level so that
`verify_fixture_schema.py` can validate these exact objects against the real
SDK's `BetaMessage` model (A9). If a fixture drifts from Anthropic's schema,
that script fails even though these tests still pass.
"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import cost as cost_module
import main
import policy as policy_module
import preflight as preflight_module
import report as report_module
import transcript as transcript_module

# --------------------------------------------------------------------------- #
# Fixtures - wire-shaped, as `response.model_dump(mode="json")` would produce.
# `cache_creation_input_tokens` / `cache_read_input_tokens` are required by
# BetaCompactionIterationUsage and BetaMessageIterationUsage, and `model` by the
# latter; they are zeroed here because the docs' worked example does not give
# them, not because a real response would have them at zero.
# --------------------------------------------------------------------------- #

_FIXTURE_SUMMARY = (
    "<summary>SYNTHETIC FIXTURE, not model output: the user pasted incident "
    "notes 000-059 and the assistant kept a running timeline.</summary>"
)

# A4: the usage numbers from the docs' own compaction example - top-level
# 45000/1234 alongside a compaction iteration that alone consumed 180000/3500.
DOCS_USAGE_RESPONSE: dict[str, object] = {
    "id": "msg_fixture_docs_usage",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [
        {
            "type": "compaction",
            "content": _FIXTURE_SUMMARY,
            "encrypted_content": "enc_fixture_docs_usage",
        },
        {"type": "text", "text": "The rollout of the new partitioner caused it."},
    ],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 45_000,
        "output_tokens": 1_234,
        "iterations": [
            {
                "type": "compaction",
                "input_tokens": 180_000,
                "output_tokens": 3_500,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
            {
                "type": "message",
                "model": "claude-sonnet-5",
                "input_tokens": 23_000,
                "output_tokens": 1_000,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            },
        ],
    },
}

# A5: the failure mode both vendors' example snippets crash on. The top-level
# numbers are zeroed because what they hold under `pause_after_compaction` is an
# open question in the note - and the report ignores them whenever `iterations`
# is present, which is exactly why the answer does not matter here.
FAILED_COMPACTION_RESPONSE: dict[str, object] = {
    "id": "msg_fixture_failed_compaction",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [{"type": "compaction", "content": None, "encrypted_content": "abc"}],
    "stop_reason": "compaction",
    "stop_sequence": None,
    "usage": {
        "input_tokens": 0,
        "output_tokens": 0,
        "iterations": [
            {
                "type": "compaction",
                "input_tokens": 56_000,
                "output_tokens": 40,
                "cache_creation_input_tokens": 0,
                "cache_read_input_tokens": 0,
            }
        ],
    },
}

# A6: an ordinary turn. No compaction block, and no `iterations` key at all -
# the documented shape when no new compaction fires.
NO_COMPACTION_RESPONSE: dict[str, object] = {
    "id": "msg_fixture_no_compaction",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [{"type": "text", "text": "Nothing to compact yet."}],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 8_000, "output_tokens": 250},
}

# A compaction block round-tripped from an earlier turn: present in `content`,
# but no new compaction ran, so `iterations` is absent and the top-level numbers
# are accurate. Documented as incurring no additional compaction cost.
REAPPLIED_COMPACTION_RESPONSE: dict[str, object] = {
    "id": "msg_fixture_reapplied",
    "type": "message",
    "role": "assistant",
    "model": "claude-sonnet-5",
    "content": [
        {
            "type": "compaction",
            "content": _FIXTURE_SUMMARY,
            "encrypted_content": "enc_fixture_reapplied",
        },
        {"type": "text", "text": "Continuing from the summary."},
    ],
    "stop_reason": "end_turn",
    "stop_sequence": None,
    "usage": {"input_tokens": 12_000, "output_tokens": 300},
}

# Every fixture `verify_fixture_schema.py` checks against the real SDK.
SCHEMA_CHECKED_FIXTURES: dict[str, dict[str, object]] = {
    "DOCS_USAGE_RESPONSE": DOCS_USAGE_RESPONSE,
    "FAILED_COMPACTION_RESPONSE": FAILED_COMPACTION_RESPONSE,
    "NO_COMPACTION_RESPONSE": NO_COMPACTION_RESPONSE,
    "REAPPLIED_COMPACTION_RESPONSE": REAPPLIED_COMPACTION_RESPONSE,
}


# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


class _FakeResponse:
    """Stands in for a `BetaMessage`: dumps to a fixture dict."""

    def __init__(self, payload: dict[str, object]) -> None:
        self._payload = payload
        self.dump_calls: list[str] = []

    def model_dump(self, *, mode: str) -> dict[str, object]:
        self.dump_calls.append(mode)
        return self._payload


class FakeCompactClient:
    """A client exposing **only** `.beta.messages.create`.

    Deliberately missing `.messages`: creating with a beta header requires the
    beta namespace, and a fake that answered both would let a call to the wrong
    one through.
    """

    def __init__(self, payload: dict[str, object]) -> None:
        self.response = _FakeResponse(payload)
        self.calls: list[dict[str, object]] = []
        outer = self

        class _Messages:
            @staticmethod
            def create(**kwargs):
                outer.calls.append(kwargs)
                return outer.response

        self.beta = SimpleNamespace(messages=_Messages())


class FakeCountingClient:
    """A client exposing **only** `.messages.count_tokens`.

    The stable namespace, on purpose: `client.messages.count_tokens` has no
    `betas` parameter, so a fake that tolerated one would hide the `TypeError`
    the real SDK raises.
    """

    def __init__(self, input_tokens: int) -> None:
        self.calls: list[dict[str, object]] = []
        outer = self

        class _Messages:
            @staticmethod
            def count_tokens(**kwargs):
                outer.calls.append(kwargs)
                if "betas" in kwargs:
                    raise TypeError(
                        "count_tokens() got an unexpected keyword argument 'betas'"
                    )
                return SimpleNamespace(input_tokens=input_tokens)

        self.messages = _Messages()


# --------------------------------------------------------------------------- #
# A3 and invariant 2: serialisation
# --------------------------------------------------------------------------- #


def test_the_edit_is_exactly_type_trigger_and_pause():
    """A3: no optional key is emitted as null when it was never set."""
    edit = policy_module.CompactionPolicy(
        trigger_tokens=50_000, pause_after_compaction=True
    ).to_edit()

    assert edit == {
        "type": "compact_20260112",
        "trigger": {"type": "input_tokens", "value": 50_000},
        "pause_after_compaction": True,
    }, edit
    assert list(edit) == ["type", "trigger", "pause_after_compaction"], list(edit)
    print("ok  the edit is exactly type, trigger and pause_after_compaction")


def test_instructions_are_emitted_only_when_set():
    """Custom instructions replace the default prompt, so their presence in the
    request is the whole difference between the two behaviours."""
    with_instructions = policy_module.CompactionPolicy(
        trigger_tokens=150_000,
        instructions="Summarise only the operator actions.",
    ).to_edit()

    assert with_instructions["instructions"] == "Summarise only the operator actions."
    assert with_instructions["pause_after_compaction"] is False
    print("ok  instructions appear only when set, never as null")


def test_to_config_wraps_one_edit():
    compaction_policy = policy_module.CompactionPolicy(trigger_tokens=60_000)
    assert compaction_policy.to_config() == {"edits": [compaction_policy.to_edit()]}
    print("ok  to_config wraps the edit in the API's edits list")


# --------------------------------------------------------------------------- #
# A2: four assertions about what a policy will and will not hold
# --------------------------------------------------------------------------- #


def test_a_trigger_below_the_server_floor_is_rejected():
    """A2: 49_999 is a 400 waiting to happen, so it is never constructible."""
    try:
        policy_module.CompactionPolicy(trigger_tokens=49_999)
    except ValueError as exc:
        assert "49999" in str(exc), exc
        assert "50000" in str(exc), exc
    else:
        raise AssertionError("expected ValueError below the documented floor")
    print("ok  a trigger below the 50,000-token floor is rejected at construction")


def test_the_floor_itself_is_accepted():
    """A2: "at least 50,000" includes 50,000."""
    at_floor = policy_module.CompactionPolicy(trigger_tokens=50_000)
    assert at_floor.trigger_tokens == 50_000
    print("ok  the floor value itself constructs")


def test_blank_instructions_are_rejected():
    """A2: a blank prompt is not "use the default" - it replaces the default
    with nothing at all."""
    for blank in ("", "   "):
        try:
            policy_module.CompactionPolicy(trigger_tokens=50_000, instructions=blank)
        except ValueError as exc:
            assert "replaces" in str(exc), exc
            continue
        raise AssertionError(f"expected ValueError for instructions={blank!r}")
    print("ok  empty and whitespace-only instructions are both rejected")


def test_absent_instructions_are_accepted():
    """A2: None is how you ask for the default summarization prompt."""
    compaction_policy = policy_module.CompactionPolicy(
        trigger_tokens=50_000, instructions=None
    )
    assert compaction_policy.instructions is None
    assert "instructions" not in compaction_policy.to_edit()
    print("ok  instructions=None constructs and sends nothing")


# --------------------------------------------------------------------------- #
# A8: the preflight
# --------------------------------------------------------------------------- #


def test_preflight_says_go_above_the_trigger():
    check = preflight_module.Preflight(counted_tokens=57_000, trigger_tokens=50_000)
    assert check.will_fire is True
    assert check.shortfall == 0
    print("ok  a transcript above the trigger is a go, with no shortfall")


def test_preflight_says_no_go_with_the_shortfall():
    check = preflight_module.Preflight(counted_tokens=41_000, trigger_tokens=50_000)
    assert check.will_fire is False
    assert check.shortfall == 9_001, check.shortfall
    print("ok  a transcript below the trigger is a no-go, with the shortfall")


def test_preflight_at_exactly_the_trigger_is_one_token_short():
    """Neither docs page says what happens at exactly the threshold, and this
    decision spends money, so the ambiguous case is the no-go case."""
    check = preflight_module.Preflight(counted_tokens=50_000, trigger_tokens=50_000)
    assert check.will_fire is False
    assert check.shortfall == 1
    print("ok  exactly at the trigger is one token short, not ready")


# --------------------------------------------------------------------------- #
# A4 / A5 / A6: reading a response
# --------------------------------------------------------------------------- #


def test_the_bill_is_the_iterations_not_the_top_level():
    """A4: the top-level usage implies 46234 tokens. The real bill is 207500."""
    compaction_report = report_module.read_response(DOCS_USAGE_RESPONSE)

    assert compaction_report.triggered is True
    assert compaction_report.paused is False
    assert compaction_report.compaction_input_tokens == 180_000
    assert compaction_report.compaction_output_tokens == 3_500
    assert compaction_report.message_input_tokens == 23_000
    assert compaction_report.message_output_tokens == 1_000
    assert compaction_report.total_billed_tokens == 207_500, (
        compaction_report.total_billed_tokens
    )
    assert compaction_report.total_billed_tokens != 45_000 + 1_234
    print("ok  the bill sums usage.iterations (207500), not the top level (46234)")


def test_a_null_summary_is_reported_not_sliced():
    """A5: `content: null` means the compaction failed. Both docs pages'
    snippets do `block["content"][:200]` here and raise TypeError."""
    compaction_report = report_module.read_response(FAILED_COMPACTION_RESPONSE)

    assert compaction_report.triggered is True
    assert compaction_report.paused is True
    assert compaction_report.summary is None
    assert compaction_report.encrypted_content == "abc"
    assert compaction_report.total_billed_tokens == 56_040
    print("ok  a compaction block with null content is reported, not sliced")


def test_no_compaction_falls_back_to_the_top_level_usage():
    """A6: `iterations` is absent whenever no new compaction fired, and then the
    top-level numbers are the accurate ones."""
    compaction_report = report_module.read_response(NO_COMPACTION_RESPONSE)

    assert compaction_report.triggered is False
    assert compaction_report.paused is False
    assert compaction_report.summary is None
    assert compaction_report.compaction_input_tokens == 0
    assert compaction_report.compaction_output_tokens == 0
    assert compaction_report.total_billed_tokens == 8_250
    print("ok  with no compaction the bill comes from the top-level usage")


def test_a_reapplied_block_is_triggered_but_costs_no_new_compaction():
    """Re-applying an existing compaction block is free: the block is in
    `content`, but no compaction iteration was billed."""
    compaction_report = report_module.read_response(REAPPLIED_COMPACTION_RESPONSE)

    assert compaction_report.triggered is True
    assert compaction_report.paused is False
    assert compaction_report.compaction_input_tokens == 0
    assert compaction_report.total_billed_tokens == 12_300
    print("ok  a re-applied compaction block bills nothing extra")


def test_the_last_compaction_block_wins():
    """"The last compaction block reflects the final state of the prompt.\""""
    response = dict(NO_COMPACTION_RESPONSE)
    response["content"] = [
        {"type": "compaction", "content": "first", "encrypted_content": "enc_1"},
        {"type": "text", "text": "between"},
        {"type": "compaction", "content": "second", "encrypted_content": "enc_2"},
    ]
    compaction_report = report_module.read_response(response)

    assert compaction_report.summary == "second"
    assert compaction_report.encrypted_content == "enc_2"
    print("ok  the last compaction block is the one reported")


def test_an_empty_iterations_array_raises_rather_than_billing_zero():
    response = dict(NO_COMPACTION_RESPONSE)
    response["usage"] = {"input_tokens": 8_000, "output_tokens": 250, "iterations": []}
    try:
        report_module.read_response(response)
    except ValueError as exc:
        assert "empty" in str(exc), exc
    else:
        raise AssertionError("expected ValueError for an empty iterations array")
    print("ok  an empty iterations array raises instead of billing zero")


def test_a_malformed_response_raises_instead_of_reporting_nothing():
    """A response missing `content` or `usage` is not a response with no
    compaction; it is something we do not understand."""
    for broken in ({"usage": {"input_tokens": 1, "output_tokens": 1}}, {"content": []}):
        try:
            report_module.read_response(broken)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {broken}")
    print("ok  a malformed response raises instead of reporting an empty result")


def test_a_blank_summary_is_unconstructible():
    """The API forbids empty compaction content, so "" is malformed - and must
    not be confused with the null case, which means the compaction failed."""
    try:
        report_module.CompactionReport(
            triggered=True,
            paused=True,
            summary="   ",
            encrypted_content=None,
            compaction_input_tokens=1,
            compaction_output_tokens=1,
            message_input_tokens=0,
            message_output_tokens=0,
        )
    except ValueError as exc:
        assert "blank" in str(exc), exc
    else:
        raise AssertionError("expected ValueError for a blank summary")
    print("ok  a blank summary is unconstructible, so '' cannot pass for failure")


def test_a_report_cannot_be_paused_without_being_triggered():
    """`stop_reason == "compaction"` without a compaction is not a state the API
    can produce, so it is not a state this type can hold."""
    try:
        report_module.CompactionReport(
            triggered=False,
            paused=True,
            summary=None,
            encrypted_content=None,
            compaction_input_tokens=0,
            compaction_output_tokens=0,
            message_input_tokens=10,
            message_output_tokens=10,
        )
    except ValueError as exc:
        assert "paused" in str(exc), exc
    else:
        raise AssertionError("expected ValueError for paused without triggered")
    print("ok  paused without triggered is not a representable report")


# --------------------------------------------------------------------------- #
# A7: the continuation
# --------------------------------------------------------------------------- #


def test_continuation_appends_the_response_content_verbatim():
    """A7: original + exactly one assistant message, `encrypted_content` intact.

    Hand-rebuilding the compaction block instead of round-tripping it is a
    silent correctness bug: the opaque metadata is dropped and nothing
    complains.
    """
    original = transcript_module.build_transcript(turns=2, chars_per_turn=200)
    content = FAILED_COMPACTION_RESPONSE["content"]

    continuation = report_module.continuation_messages(original, content)

    assert len(continuation) == len(original) + 1
    assert continuation[: len(original)] == original
    appended = continuation[-1]
    assert appended["role"] == "assistant"
    assert appended["content"] == list(content)
    assert appended["content"][0]["encrypted_content"] == "abc"
    print("ok  the continuation appends one assistant message, verbatim")


def test_continuation_refuses_an_empty_assistant_message():
    try:
        report_module.continuation_messages([{"role": "user", "content": "hi"}], [])
    except ValueError as exc:
        assert "empty" in str(exc), exc
    else:
        raise AssertionError("expected ValueError for empty response content")
    print("ok  an assistant message with no content is refused")


# --------------------------------------------------------------------------- #
# Cost
# --------------------------------------------------------------------------- #


def test_cost_is_dollars_per_million_tokens():
    assert cost_module.estimate_usd(1_000_000, 0, 2.0, 10.0) == 2.0
    assert cost_module.estimate_usd(0, 1_000_000, 2.0, 10.0) == 10.0
    # The docs' compaction iteration, priced at Sonnet 5's rates.
    assert cost_module.estimate_usd(180_000, 3_500, 2.0, 10.0) == 0.395
    print("ok  cost is input and output priced separately, per million tokens")


def test_a_negative_token_count_has_no_price():
    for args in ((-1, 0, 2.0, 10.0), (0, -1, 2.0, 10.0), (1, 1, -2.0, 10.0)):
        try:
            cost_module.estimate_usd(*args)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {args}")
    print("ok  a negative count or price is rejected rather than priced")


# --------------------------------------------------------------------------- #
# The transcript fixture
# --------------------------------------------------------------------------- #


def test_the_transcript_alternates_and_ends_on_a_user_turn():
    turns = 4
    messages = transcript_module.build_transcript(turns=turns, chars_per_turn=300)

    assert len(messages) == 2 * turns + 1, len(messages)
    assert [m["role"] for m in messages] == ["user", "assistant"] * turns + ["user"]
    for message in messages[:-1]:
        assert len(message["content"]) == 300, len(message["content"])
    assert "[note 003]" in messages[6]["content"]
    print("ok  the transcript alternates roles and ends on a user turn")


def test_the_same_arguments_always_build_the_same_transcript():
    a = transcript_module.build_transcript(turns=3, chars_per_turn=250)
    b = transcript_module.build_transcript(turns=3, chars_per_turn=250)
    assert a == b
    print("ok  the same arguments always build the same transcript")


def test_a_degenerate_transcript_is_rejected():
    for kwargs in (
        {"turns": 0, "chars_per_turn": 300},
        {"turns": 2, "chars_per_turn": 4},
    ):
        try:
            transcript_module.build_transcript(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")
    print("ok  a transcript with no turns or unlabellable turns is rejected")


# --------------------------------------------------------------------------- #
# The shell
# --------------------------------------------------------------------------- #


def test_the_shell_sends_the_beta_and_the_policy_config():
    """The wire call carries `compact-2026-01-12` - which the SDK does not
    type-check - and the policy's exact edit dict."""
    compaction_policy = policy_module.CompactionPolicy(
        trigger_tokens=50_000, pause_after_compaction=True
    )
    client = FakeCompactClient(FAILED_COMPACTION_RESPONSE)
    messages = transcript_module.build_transcript(turns=2, chars_per_turn=200)

    payload = main.compact_once(
        client,
        model=main.MODEL,
        messages=messages,
        compaction_policy=compaction_policy,
        max_tokens=main.MAX_TOKENS,
    )

    (call,) = client.calls
    assert call["betas"] == ["compact-2026-01-12"], call["betas"]
    assert call["model"] == main.MODEL == "claude-sonnet-5"
    assert call["messages"] == messages
    assert call["max_tokens"] == main.MAX_TOKENS
    assert call["context_management"] == {
        "edits": [
            {
                "type": "compact_20260112",
                "trigger": {"type": "input_tokens", "value": 50_000},
                "pause_after_compaction": True,
            }
        ]
    }, call["context_management"]
    assert client.response.dump_calls == ["json"], client.response.dump_calls
    assert payload is FAILED_COMPACTION_RESPONSE
    print("ok  the shell sends the beta and the policy's exact edit dict")


def test_the_preflight_count_never_sends_a_beta():
    """Counting is free and needs no beta, and `client.messages.count_tokens`
    raises TypeError if handed one."""
    client = FakeCountingClient(57_123)
    messages = transcript_module.build_transcript(turns=2, chars_per_turn=200)

    counted = main.count_transcript_tokens(client, model=main.MODEL, messages=messages)

    (call,) = client.calls
    assert counted == 57_123
    assert "betas" not in call, call
    assert "context_management" not in call, call
    print("ok  the free preflight count sends no beta and no edits")


def test_an_api_error_propagates_instead_of_becoming_an_empty_report():
    class _Failing:
        @staticmethod
        def create(**_kwargs):
            raise RuntimeError("400 unknown beta: compact-2026-01-13")

    failing = SimpleNamespace(beta=SimpleNamespace(messages=_Failing()))
    try:
        main.compact_once(
            failing,
            model=main.MODEL,
            messages=[],
            compaction_policy=policy_module.CompactionPolicy(trigger_tokens=50_000),
            max_tokens=16,
        )
    except RuntimeError as exc:
        assert "400" in str(exc), exc
    else:
        raise AssertionError("expected the API error to propagate")
    print("ok  an API error propagates instead of becoming an empty report")


def test_render_shows_the_true_bill_and_the_summary():
    compaction_report = report_module.read_response(DOCS_USAGE_RESPONSE)
    continuation = report_module.continuation_messages(
        [{"role": "user", "content": "hi"}], DOCS_USAGE_RESPONSE["content"]
    )
    text = main.render(
        compaction_report,
        main.DEMO_POLICY,
        continuation,
        model=main.MODEL,
        turns=60,
        chars_per_turn=2_000,
        usd_in_per_mtok=main.USD_IN_PER_MTOK,
        usd_out_per_mtok=main.USD_OUT_PER_MTOK,
    )

    assert '"type": "compact_20260112"' in text
    assert "180000 in / 3500 out  ($0.3950)" in text
    assert "207500 tokens billed" in text
    assert "last blocks compaction, text" in text
    assert "SYNTHETIC FIXTURE" in text
    print("ok  the render prints the pasteable config, the true bill and the summary")


def test_render_explains_a_failed_compaction():
    compaction_report = report_module.read_response(FAILED_COMPACTION_RESPONSE)
    continuation = report_module.continuation_messages(
        [{"role": "user", "content": "hi"}], FAILED_COMPACTION_RESPONSE["content"]
    )
    text = main.render(
        compaction_report,
        main.DEMO_POLICY,
        continuation,
        model=main.MODEL,
        turns=60,
        chars_per_turn=2_000,
        usd_in_per_mtok=main.USD_IN_PER_MTOK,
        usd_out_per_mtok=main.USD_OUT_PER_MTOK,
    )

    assert "summary: none" in text
    assert "must still be" in text
    assert "round-tripped" in text
    print("ok  a failed compaction is explained, not printed as an empty summary")


def test_the_preflight_render_states_the_shortfall():
    text = main.render_preflight(
        preflight_module.Preflight(counted_tokens=41_000, trigger_tokens=50_000)
    )
    assert "NO-GO" in text
    assert "9001 input tokens" in text
    assert "Not sending it." in text
    print("ok  a no-go preflight prints the shortfall and refuses to spend")


# --------------------------------------------------------------------------- #


def main_() -> int:
    tests = [
        test_the_edit_is_exactly_type_trigger_and_pause,
        test_instructions_are_emitted_only_when_set,
        test_to_config_wraps_one_edit,
        test_a_trigger_below_the_server_floor_is_rejected,
        test_the_floor_itself_is_accepted,
        test_blank_instructions_are_rejected,
        test_absent_instructions_are_accepted,
        test_preflight_says_go_above_the_trigger,
        test_preflight_says_no_go_with_the_shortfall,
        test_preflight_at_exactly_the_trigger_is_one_token_short,
        test_the_bill_is_the_iterations_not_the_top_level,
        test_a_null_summary_is_reported_not_sliced,
        test_no_compaction_falls_back_to_the_top_level_usage,
        test_a_reapplied_block_is_triggered_but_costs_no_new_compaction,
        test_the_last_compaction_block_wins,
        test_an_empty_iterations_array_raises_rather_than_billing_zero,
        test_a_malformed_response_raises_instead_of_reporting_nothing,
        test_a_blank_summary_is_unconstructible,
        test_a_report_cannot_be_paused_without_being_triggered,
        test_continuation_appends_the_response_content_verbatim,
        test_continuation_refuses_an_empty_assistant_message,
        test_cost_is_dollars_per_million_tokens,
        test_a_negative_token_count_has_no_price,
        test_the_transcript_alternates_and_ends_on_a_user_turn,
        test_the_same_arguments_always_build_the_same_transcript,
        test_a_degenerate_transcript_is_rejected,
        test_the_shell_sends_the_beta_and_the_policy_config,
        test_the_preflight_count_never_sends_a_beta,
        test_an_api_error_propagates_instead_of_becoming_an_empty_report,
        test_render_shows_the_true_bill_and_the_summary,
        test_render_explains_a_failed_compaction,
        test_the_preflight_render_states_the_shortfall,
    ]
    started = time.monotonic()
    for test in tests:
        test()
    elapsed = time.monotonic() - started

    # A1, checked rather than asserted in prose: the whole suite exercised the
    # core, the shell's two adapters and the renderer without the SDK ever being
    # imported - so nothing here could have reached the network or read a key.
    assert "anthropic" not in sys.modules, "the self-test imported the SDK"
    assert elapsed < 1.0, f"self-test took {elapsed:.3f}s; something did I/O"

    print(f"\nAll {len(tests)} self-tests passed with no key and no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
