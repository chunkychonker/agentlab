"""Offline self-test for the context-editing preview. No API key, no network,
no `anthropic` install.

Run:
    python test_preview.py

Each test maps to an acceptance criterion from the research note
(research/2026-08-11-context-editing-preview.md):

  A1. this file            -> exits 0 with no key, no network, no SDK installed
  A2. invalid policies     -> four constructor arguments, four ValueErrors,
                              one test each
  A3. minimal serialisation-> exactly {type, trigger, keep}, by dict equality,
                              so a stray "clear_at_least": null fails
  A4. 70000 -> 25000       -> applied, 45000 saved, 64.3%
  A5. no context_management-> applied=False, 0 saved, no exception
  A6. the SDK adapter      -> sends the beta and the policy's own config

Plus the transcript fixture's invariants and the report's own guards.
"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import main
import policy as policy_module
import preview as preview_module
import transcript as transcript_module

# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


class FakeCounter:
    """Replays one scripted `TokenCount` per call and records every call's
    keyword arguments."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def __call__(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("fake counter ran out of scripted responses")
        return self._scripted.pop(0)


class FakeMessages:
    """Stands in for `client.beta.messages`: records count_tokens kwargs,
    replays scripted SDK-shaped responses."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []

    def count_tokens(self, **kwargs):
        self.calls.append(kwargs)
        if not self._scripted:
            raise AssertionError("fake client ran out of scripted responses")
        return self._scripted.pop(0)


class FakeClient:
    """A client that exposes **only** the beta namespace.

    Deliberately missing `.messages`: the real `client.messages.count_tokens`
    has no `betas` parameter and raises TypeError, so a fake that answered both
    namespaces would let that bug through. It did, once, before a stub-server
    run against the real SDK caught it.
    """

    def __init__(self, scripted):
        messages = FakeMessages(scripted)
        self.beta = SimpleNamespace(messages=messages)
        self.calls = messages.calls


def _sdk_count(input_tokens: int, original_input_tokens: int | None = None):
    """An object shaped like `BetaMessageTokensCount`.

    `context_management` is None when the API applied no edit, mirroring the
    SDK's `Optional[BetaCountTokensContextManagementResponse] = None`.
    """
    context_management = (
        None
        if original_input_tokens is None
        else SimpleNamespace(original_input_tokens=original_input_tokens)
    )
    return SimpleNamespace(
        input_tokens=input_tokens, context_management=context_management
    )


_KEEP_ONLY = {"keep": 3, "trigger_kind": "tool_uses", "trigger_value": 5}


# --------------------------------------------------------------------------- #
# A3 and invariant 1: serialisation
# --------------------------------------------------------------------------- #


def test_minimal_policy_serialises_to_exactly_three_keys():
    """A3: no optional key is emitted as null when it was never set."""
    edit = policy_module.ClearToolUsesPolicy(**_KEEP_ONLY).to_edit()

    assert edit == {
        "type": "clear_tool_uses_20250919",
        "trigger": {"type": "tool_uses", "value": 5},
        "keep": {"type": "tool_uses", "value": 3},
    }, edit
    # Invariant 1: `type` first, and no key beyond these three in any order.
    assert list(edit) == ["type", "trigger", "keep"], list(edit)
    print("ok  an unset optional is omitted, not emitted as null")


def test_full_policy_serialises_every_optional():
    """The optionals use the API's own units: input_tokens, not tool_uses."""
    edit = policy_module.ClearToolUsesPolicy(
        keep=2,
        trigger_kind="input_tokens",
        trigger_value=100_000,
        clear_at_least=5_000,
        exclude_tools=["read_document"],
    ).to_edit()

    assert edit == {
        "type": "clear_tool_uses_20250919",
        "trigger": {"type": "input_tokens", "value": 100_000},
        "keep": {"type": "tool_uses", "value": 2},
        "clear_at_least": {"type": "input_tokens", "value": 5_000},
        "exclude_tools": ["read_document"],
    }, edit
    print("ok  clear_at_least is in input tokens and keep is in tool uses")


def test_to_config_wraps_one_edit():
    p = policy_module.ClearToolUsesPolicy(**_KEEP_ONLY)
    assert p.to_config() == {"edits": [p.to_edit()]}, p.to_config()
    print("ok  to_config wraps the edit in the API's edits list")


def test_a_list_of_excluded_tools_is_frozen_into_a_tuple():
    """A frozen policy that still holds a caller's mutable list is not frozen."""
    names = ["read_document"]
    p = policy_module.ClearToolUsesPolicy(**_KEEP_ONLY, exclude_tools=names)
    names.append("mutated")
    assert p.exclude_tools == ("read_document",), p.exclude_tools
    assert hash(p) is not None
    print("ok  exclude_tools is normalised to a tuple at construction")


# --------------------------------------------------------------------------- #
# A2: four invalid policies, four tests
# --------------------------------------------------------------------------- #


def _rejects(message: str, **kwargs) -> None:
    """Assert the constructor raises ValueError naming the offending value."""
    try:
        policy_module.ClearToolUsesPolicy(**kwargs)
    except ValueError as exc:
        assert message in str(exc), f"the bad value must be named: {exc}"
        return
    raise AssertionError(f"expected ValueError for {kwargs}")


def test_negative_keep_is_rejected():
    _rejects("-1", keep=-1, trigger_kind="tool_uses", trigger_value=5)
    print("ok  keep < 0 is rejected at construction")


def test_trigger_value_below_one_is_rejected():
    _rejects("0", keep=3, trigger_kind="tool_uses", trigger_value=0)
    print("ok  a trigger value below 1 is rejected at construction")


def test_clear_at_least_below_one_is_rejected():
    _rejects("0", **_KEEP_ONLY, clear_at_least=0)
    print("ok  clear_at_least below 1 is rejected at construction")


def test_blank_excluded_tool_name_is_rejected():
    _rejects("exclude_tools[1]", **_KEEP_ONLY, exclude_tools=("read_document", "  "))
    print("ok  a blank excluded tool name is rejected at construction")


def test_unknown_trigger_kind_is_rejected():
    """Literal is erased at runtime, so the check has to be real."""
    _rejects("'tokens'", keep=3, trigger_kind="tokens", trigger_value=5)
    print("ok  an unknown trigger kind is rejected at construction")


def test_a_bare_string_of_excluded_tools_is_rejected():
    """Iterating "read_document" would exclude 13 one-letter tools and clear the
    one the caller meant to protect."""
    try:
        policy_module.ClearToolUsesPolicy(**_KEEP_ONLY, exclude_tools="read_document")
    except TypeError as exc:
        assert "bare str" in str(exc), exc
    else:
        raise AssertionError("expected TypeError for a bare str")
    print("ok  a bare str of excluded tools is rejected at construction")


# --------------------------------------------------------------------------- #
# A4 / A5: the report
# --------------------------------------------------------------------------- #


def test_applied_edit_reports_the_delta():
    """A4: 70000 -> 25000 is 45000 tokens, 64.3%."""
    counter = FakeCounter(
        [
            preview_module.TokenCount(input_tokens=70_000, original_input_tokens=None),
            preview_module.TokenCount(
                input_tokens=25_000, original_input_tokens=70_000
            ),
        ]
    )
    report = preview_module.preview(
        counter, [], [], policy_module.ClearToolUsesPolicy(**_KEEP_ONLY)
    )

    assert report.applied is True
    assert report.original_input_tokens == 70_000
    assert report.edited_input_tokens == 25_000
    assert report.tokens_saved == 45_000, report.tokens_saved
    assert report.percent_saved == 64.3, report.percent_saved
    print("ok  an applied edit reports 45000 tokens saved and 64.3%")


def test_unapplied_edit_reports_zero_and_does_not_raise():
    """A5: the trigger did not fire, so there is no original to subtract from.

    The failure this guards against is reporting a negative saving, or crashing
    on the absent `context_management` object.
    """
    counter = FakeCounter(
        [
            preview_module.TokenCount(input_tokens=8_000, original_input_tokens=None),
            preview_module.TokenCount(input_tokens=8_000, original_input_tokens=None),
        ]
    )
    report = preview_module.preview(
        counter, [], [], policy_module.ClearToolUsesPolicy(**_KEEP_ONLY)
    )

    assert report.applied is False
    assert report.tokens_saved == 0, report.tokens_saved
    assert report.percent_saved == 0.0, report.percent_saved
    assert report.original_input_tokens == 8_000
    assert report.edited_input_tokens == 8_000
    print("ok  an unapplied edit reports 0 saved rather than raising or guessing")


def test_preview_sends_the_plain_count_then_the_edited_one():
    """Two counts of the same request: the second carries the policy's config."""
    p = policy_module.ClearToolUsesPolicy(**_KEEP_ONLY)
    counter = FakeCounter(
        [
            preview_module.TokenCount(input_tokens=100, original_input_tokens=None),
            preview_module.TokenCount(input_tokens=60, original_input_tokens=100),
        ]
    )
    messages = transcript_module.build_transcript(rounds=2, result_chars=64)
    tools = [transcript_module.tool_schema()]

    preview_module.preview(counter, messages, tools, p)

    assert len(counter.calls) == 2, counter.calls
    assert counter.calls[0]["context_management"] is None
    assert counter.calls[1]["context_management"] == p.to_config()
    assert counter.calls[0]["messages"] == counter.calls[1]["messages"] == messages
    assert counter.calls[0]["tools"] == counter.calls[1]["tools"] == tools
    print("ok  the same request is counted twice, once plain, once edited")


def test_disagreeing_counts_raise_instead_of_reporting():
    """A deterministic tokenizer counting the same messages twice cannot give two
    answers; if it does, no derived number is trustworthy."""
    counter = FakeCounter(
        [
            preview_module.TokenCount(input_tokens=100, original_input_tokens=None),
            preview_module.TokenCount(input_tokens=60, original_input_tokens=90),
        ]
    )
    try:
        preview_module.preview(
            counter, [], [], policy_module.ClearToolUsesPolicy(**_KEEP_ONLY)
        )
    except preview_module.InconsistentCount as exc:
        assert "90" in str(exc) and "100" in str(exc), exc
    else:
        raise AssertionError("expected InconsistentCount")
    print("ok  two counts that disagree about the original raise, not report")


def test_a_report_cannot_hold_a_negative_saving():
    """tokens_saved is derived, so the only way to make it negative is to build
    an impossible report - which the constructor refuses."""
    try:
        preview_module.PreviewReport(
            applied=True, original_input_tokens=100, edited_input_tokens=120
        )
    except ValueError as exc:
        assert "120" in str(exc), exc
    else:
        raise AssertionError("expected ValueError for an edit that grew the request")

    try:
        preview_module.PreviewReport(
            applied=False, original_input_tokens=100, edited_input_tokens=60
        )
    except ValueError as exc:
        assert "no edit was applied" in str(exc), exc
    else:
        raise AssertionError("expected ValueError for an unapplied non-zero saving")
    print("ok  an impossible report is unconstructible, so no negative saving")


def test_percent_of_an_empty_request_is_zero_not_a_crash():
    report = preview_module.PreviewReport(
        applied=False, original_input_tokens=0, edited_input_tokens=0
    )
    assert report.percent_saved == 0.0
    print("ok  a zero-token original gives 0.0%, not a ZeroDivisionError")


def test_a_token_count_cannot_be_negative():
    for kwargs in (
        {"input_tokens": -1, "original_input_tokens": None},
        {"input_tokens": 10, "original_input_tokens": -5},
    ):
        try:
            preview_module.TokenCount(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")
    print("ok  a negative token count is rejected at the boundary")


# --------------------------------------------------------------------------- #
# A6: the SDK adapter
# --------------------------------------------------------------------------- #


def test_adapter_sends_the_beta_and_the_policy_config():
    """A6: the wire call carries the beta header and the exact edit dict."""
    p = policy_module.ClearToolUsesPolicy(**_KEEP_ONLY)
    client = FakeClient([_sdk_count(70_000), _sdk_count(25_000, 70_000)])
    messages = transcript_module.build_transcript(rounds=2, result_chars=64)
    tools = [transcript_module.tool_schema()]

    report = preview_module.preview(
        main.make_counter(client, model=main.MODEL), messages, tools, p
    )

    plain, edited = client.calls
    for call in (plain, edited):
        assert call["betas"] == ["context-management-2025-06-27"], call["betas"]
        assert call["model"] == main.MODEL
        assert call["messages"] == messages
        assert call["tools"] == tools
    # The plain count must not mention context_management at all - sending
    # `None` is a different request from not sending the field.
    assert "context_management" not in plain, plain
    assert edited["context_management"] == {
        "edits": [
            {
                "type": "clear_tool_uses_20250919",
                "trigger": {"type": "tool_uses", "value": 5},
                "keep": {"type": "tool_uses", "value": 3},
            }
        ]
    }, edited["context_management"]
    assert report.tokens_saved == 45_000
    print("ok  the adapter sends the beta and the policy's exact edit dict")


def test_adapter_maps_a_missing_context_management_to_none():
    """The count endpoint reports only `original_input_tokens`, and omits the
    whole object when nothing was cleared."""
    client = FakeClient([_sdk_count(8_000)])
    count = main.make_counter(client, model=main.MODEL)

    result = count(messages=[], tools=[], context_management=None)

    assert result == preview_module.TokenCount(
        input_tokens=8_000, original_input_tokens=None
    ), result
    print("ok  an absent context_management object becomes original=None")


def test_adapter_propagates_api_errors():
    """An unsupported model or a rejected beta must not become a 0-token saving."""

    class FailingMessages:
        @staticmethod
        def count_tokens(**_kwargs):
            raise RuntimeError("400 model does not support context management")

    failing = SimpleNamespace(beta=SimpleNamespace(messages=FailingMessages()))
    count = main.make_counter(failing, model="claude-nonexistent")
    try:
        count(messages=[], tools=[], context_management=None)
    except RuntimeError as exc:
        assert "400" in str(exc), exc
    else:
        raise AssertionError("expected the API error to propagate")
    print("ok  an API error propagates instead of becoming a zero-saving report")


# --------------------------------------------------------------------------- #
# The transcript fixture
# --------------------------------------------------------------------------- #


def test_transcript_pairs_every_tool_use_with_one_result():
    rounds = 7
    messages = transcript_module.build_transcript(rounds=rounds, result_chars=200)

    assert len(messages) == 1 + 2 * rounds, len(messages)

    seen: list[str] = []
    for index in range(rounds):
        assistant = messages[1 + 2 * index]
        user = messages[2 + 2 * index]
        uses = [b for b in assistant["content"] if b["type"] == "tool_use"]
        results = [b for b in user["content"] if b["type"] == "tool_result"]
        assert assistant["role"] == "assistant" and user["role"] == "user"
        assert len(uses) == 1 and len(results) == 1
        assert results[0]["tool_use_id"] == uses[0]["id"]
        assert uses[0]["name"] == transcript_module.TOOL_NAME
        assert len(results[0]["content"]) == 200
        seen.append(uses[0]["id"])

    assert len(set(seen)) == rounds, seen
    print("ok  every tool_use is answered by exactly one matching tool_result")


def test_transcript_is_deterministic():
    a = transcript_module.build_transcript(rounds=3, result_chars=99)
    b = transcript_module.build_transcript(rounds=3, result_chars=99)
    assert a == b
    print("ok  the same arguments always build the same transcript")


def test_transcript_rejects_a_degenerate_fixture():
    for kwargs in ({"rounds": 0, "result_chars": 10}, {"rounds": 2, "result_chars": 0}):
        try:
            transcript_module.build_transcript(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")
    print("ok  a transcript with no rounds or empty results is rejected")


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def test_render_shows_the_config_and_the_numbers():
    p = policy_module.ClearToolUsesPolicy(**_KEEP_ONLY)
    text = main.render(
        preview_module.PreviewReport(
            applied=True, original_input_tokens=70_000, edited_input_tokens=25_000
        ),
        p,
        model=main.MODEL,
        rounds=20,
        result_chars=1200,
    )
    assert '"type": "clear_tool_uses_20250919"' in text
    assert "45000 tokens (64.3%)" in text
    assert "applied     True" in text
    assert "clear_at_least" not in text
    print("ok  the report prints the pasteable config and the delta")


def test_render_explains_why_nothing_was_applied():
    text = main.render(
        preview_module.PreviewReport(
            applied=False, original_input_tokens=8_000, edited_input_tokens=8_000
        ),
        policy_module.ClearToolUsesPolicy(**_KEEP_ONLY),
        model=main.MODEL,
        rounds=2,
        result_chars=64,
    )
    assert "applied     False" in text
    assert "trigger did not fire" in text
    assert "clear_at_least" in text
    print("ok  an unapplied edit is explained, not silently reported as 0")


# --------------------------------------------------------------------------- #


def main_() -> int:
    tests = [
        test_minimal_policy_serialises_to_exactly_three_keys,
        test_full_policy_serialises_every_optional,
        test_to_config_wraps_one_edit,
        test_a_list_of_excluded_tools_is_frozen_into_a_tuple,
        test_negative_keep_is_rejected,
        test_trigger_value_below_one_is_rejected,
        test_clear_at_least_below_one_is_rejected,
        test_blank_excluded_tool_name_is_rejected,
        test_unknown_trigger_kind_is_rejected,
        test_a_bare_string_of_excluded_tools_is_rejected,
        test_applied_edit_reports_the_delta,
        test_unapplied_edit_reports_zero_and_does_not_raise,
        test_preview_sends_the_plain_count_then_the_edited_one,
        test_disagreeing_counts_raise_instead_of_reporting,
        test_a_report_cannot_hold_a_negative_saving,
        test_percent_of_an_empty_request_is_zero_not_a_crash,
        test_a_token_count_cannot_be_negative,
        test_adapter_sends_the_beta_and_the_policy_config,
        test_adapter_maps_a_missing_context_management_to_none,
        test_adapter_propagates_api_errors,
        test_transcript_pairs_every_tool_use_with_one_result,
        test_transcript_is_deterministic,
        test_transcript_rejects_a_degenerate_fixture,
        test_render_shows_the_config_and_the_numbers,
        test_render_explains_why_nothing_was_applied,
    ]
    started = time.monotonic()
    for test in tests:
        test()
    elapsed = time.monotonic() - started

    # A1, checked rather than asserted in prose: the whole suite exercised the
    # core, the adapter and the renderer without the SDK ever being imported -
    # so nothing here could have reached the network or read a key.
    assert "anthropic" not in sys.modules, "the self-test imported the SDK"
    assert elapsed < 1.0, f"self-test took {elapsed:.3f}s; something did I/O"

    print(f"\nAll {len(tests)} self-tests passed with no key and no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
