"""Offline self-test for the `clear_thinking_20251015` preview. No API key, no
network, no `anthropic` install.

Run:
    python test_preview_thinking.py

Each test maps to an acceptance criterion from the research note
(research/2026-09-08-context-editing-clear-thinking-preview.md):

  A1. this file            -> exits 0 with no key, no network, no SDK installed
  A2. default policy       -> exactly {"type": "clear_thinking_20251015"}, by
                              dict equality, so a stray "keep": null fails
  A3. keep=N               -> {"type": "thinking_turns", "value": N}
  A4. keep="all"           -> the bare string, not an object
  A5. invalid keeps        -> 0 / -1 / "most" are ValueError, 1.5 / True are
                              TypeError; one test each
  A6. the fixture          -> 13 messages for 6 turns, deterministic, every
                              assistant turn [thinking, text] with an 800-char
                              body and a signature
  A7. 60000 -> 38000       -> applied, 22000 saved, 36.7%
  A8. no context_management-> applied=False, 0 saved, no exception
  A9. the SDK adapter      -> sends the beta, the adaptive thinking config, and
                              the policy's own edit dict

Plus invariant 3 (one shared beta, no second constant), invariant 5 (the core
`preview()` is reused unchanged by a second policy type), and the renderer.
"""

from __future__ import annotations

import sys
import time
from types import SimpleNamespace

import policy as policy_module
import preview as preview_module
import preview_thinking as shell
import thinking_transcript as transcript_module

# --------------------------------------------------------------------------- #
# Test doubles - mirrors of `test_preview.py`'s, owned by this suite so the two
# self-tests cannot break each other.
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
    namespaces would let that bug through. It did once, in the
    `clear_tool_uses` adapter - see `test_preview.py`'s note.
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


# --------------------------------------------------------------------------- #
# A2 / A3 / A4: serialisation, one test per `keep` form
# --------------------------------------------------------------------------- #


def test_an_omitted_keep_serialises_to_the_type_alone():
    """A2: the whole edit is one key. `{"keep": null}` is a different request."""
    edit = policy_module.ClearThinkingPolicy().to_edit()

    assert edit == {"type": "clear_thinking_20251015"}, edit
    assert list(edit) == ["type"], list(edit)
    print("ok  an omitted keep leaves the edit as type alone, not keep: null")


def test_a_turn_count_serialises_to_the_thinking_turns_object():
    """A3: counted in thinking turns - never tokens, never tool uses."""
    edit = policy_module.ClearThinkingPolicy(keep=2).to_edit()

    assert edit == {
        "type": "clear_thinking_20251015",
        "keep": {"type": "thinking_turns", "value": 2},
    }, edit
    assert list(edit)[0] == "type", list(edit)
    print("ok  a turn count serialises to {type: thinking_turns, value: N}")


def test_keep_all_serialises_to_the_bare_string():
    """A4: the documented shorthand is the string, not `{"type": "all"}`."""
    edit = policy_module.ClearThinkingPolicy(keep="all").to_edit()

    assert edit == {"type": "clear_thinking_20251015", "keep": "all"}, edit
    print("ok  keep='all' serialises to the bare string the docs use")


def test_to_config_wraps_one_edit():
    p = policy_module.ClearThinkingPolicy(keep=2)
    assert p.to_config() == {"edits": [p.to_edit()]}, p.to_config()
    print("ok  to_config wraps the edit in the API's edits list")


def test_the_thinking_strategy_reuses_the_one_beta():
    """Invariant 3: same header as `clear_tool_uses`, and no second constant.

    `compact_20260112` needs its own beta; this one does not, and a duplicate
    constant is how the two would drift apart.
    """
    assert policy_module.BETA == "context-management-2025-06-27", policy_module.BETA
    beta_constants = [name for name in vars(policy_module) if name.endswith("BETA")]
    assert beta_constants == ["BETA"], beta_constants
    print("ok  the thinking strategy ships behind the one existing beta")


# --------------------------------------------------------------------------- #
# A5: five invalid keeps, five tests
# --------------------------------------------------------------------------- #


def _rejects(exc_type, fragment: str, keep) -> None:
    """Assert the constructor raises ``exc_type`` naming the offending value."""
    try:
        policy_module.ClearThinkingPolicy(keep=keep)
    except exc_type as exc:
        assert fragment in str(exc), f"the bad value must be named: {exc}"
        return
    except Exception as exc:  # noqa: BLE001 - the wrong error type is a failure
        raise AssertionError(
            f"expected {exc_type.__name__} for keep={keep!r}, got {exc!r}"
        ) from exc
    raise AssertionError(f"expected {exc_type.__name__} for keep={keep!r}")


def test_keeping_zero_turns_is_rejected():
    _rejects(ValueError, "0", 0)
    print("ok  keep=0 is rejected at construction")


def test_a_negative_keep_is_rejected():
    _rejects(ValueError, "-1", -1)
    print("ok  keep=-1 is rejected at construction")


def test_an_unknown_keep_string_is_rejected():
    """`Literal["all"]` is erased at runtime, so the check has to be real."""
    _rejects(ValueError, "'most'", "most")
    print("ok  a keep string other than 'all' is rejected at construction")


def test_a_fractional_keep_is_rejected():
    _rejects(TypeError, "float", 1.5)
    print("ok  a fractional keep is rejected at construction")


def test_a_bool_keep_is_rejected():
    """`bool` is an `int` subclass, so `keep=True` would otherwise serialise as
    `{"type": "thinking_turns", "value": true}`."""
    _rejects(TypeError, "True", True)
    print("ok  keep=True is rejected rather than counted as one turn")


# --------------------------------------------------------------------------- #
# A6: the fixture
# --------------------------------------------------------------------------- #


def test_every_assistant_turn_is_one_thinking_block_then_one_text_block():
    """A6: 6 turns -> 13 messages, each assistant turn [thinking, text]."""
    turns = 6
    messages = transcript_module.build_thinking_transcript(
        turns=turns, thinking_chars=800
    )

    assert len(messages) == 1 + 2 * turns == 13, len(messages)
    assert messages[0]["role"] == "user"
    assert messages[-1]["role"] == "user", "the fixture must end on a user turn"

    for index in range(turns):
        assistant = messages[1 + 2 * index]
        follow_up = messages[2 + 2 * index]
        assert assistant["role"] == "assistant" and follow_up["role"] == "user"

        blocks = assistant["content"]
        assert [b["type"] for b in blocks] == ["thinking", "text"], blocks
        assert len(blocks[0]["thinking"]) == 800, len(blocks[0]["thinking"])
        assert blocks[0]["signature"], "a thinking block without a signature"
        assert blocks[1]["text"]

    print("ok  every assistant turn is one thinking block then one text block")


def test_the_thinking_fixture_is_deterministic():
    a = transcript_module.build_thinking_transcript(turns=6, thinking_chars=800)
    b = transcript_module.build_thinking_transcript(turns=6, thinking_chars=800)
    assert a == b
    print("ok  the same arguments always build the same thinking transcript")


def test_the_thinking_fixture_rejects_a_degenerate_argument():
    for kwargs in (
        {"turns": 0, "thinking_chars": 10},
        {"turns": 2, "thinking_chars": 0},
    ):
        try:
            transcript_module.build_thinking_transcript(**kwargs)
        except ValueError:
            continue
        raise AssertionError(f"expected ValueError for {kwargs}")
    print("ok  a transcript with no turns or empty thinking is rejected")


def test_the_fixture_carries_no_tools():
    """`TOOL_FREE` is a claim about the fixture; this is the check."""
    messages = transcript_module.build_thinking_transcript(
        turns=3, thinking_chars=64
    )
    kinds = {
        block["type"]
        for message in messages
        if isinstance(message["content"], list)
        for block in message["content"]
    }
    assert transcript_module.TOOL_FREE is True
    assert kinds == {"thinking", "text"}, kinds
    print("ok  the fixture holds no tool_use or tool_result blocks to clear")


# --------------------------------------------------------------------------- #
# A7 / A8: the report, through the unchanged core
# --------------------------------------------------------------------------- #


def test_applied_edit_reports_the_delta():
    """A7: 60000 -> 38000 is 22000 tokens, 36.7%."""
    counter = FakeCounter(
        [
            preview_module.TokenCount(input_tokens=60_000, original_input_tokens=None),
            preview_module.TokenCount(
                input_tokens=38_000, original_input_tokens=60_000
            ),
        ]
    )
    report = preview_module.preview(
        counter, [], [], policy_module.ClearThinkingPolicy(keep=2)
    )

    assert report.applied is True
    assert report.original_input_tokens == 60_000
    assert report.edited_input_tokens == 38_000
    assert report.tokens_saved == 22_000, report.tokens_saved
    assert report.percent_saved == 36.7, report.percent_saved
    print("ok  an applied edit reports 22000 tokens saved and 36.7%")


def test_unapplied_edit_reports_zero_and_does_not_raise():
    """A8: nothing was cleared, so there is no original to subtract from.

    For this strategy that is not a trigger that failed to fire - there is no
    trigger - it is a request that had no clearable prior-turn thinking left in
    it, which is what a keep-only-last-turn model produces.
    """
    counter = FakeCounter(
        [
            preview_module.TokenCount(input_tokens=9_000, original_input_tokens=None),
            preview_module.TokenCount(input_tokens=9_000, original_input_tokens=None),
        ]
    )
    report = preview_module.preview(
        counter, [], [], policy_module.ClearThinkingPolicy(keep=2)
    )

    assert report.applied is False
    assert report.tokens_saved == 0, report.tokens_saved
    assert report.percent_saved == 0.0, report.percent_saved
    assert report.original_input_tokens == 9_000
    assert report.edited_input_tokens == 9_000
    print("ok  an unapplied edit reports 0 saved rather than raising or guessing")


def test_the_unchanged_core_counts_the_thinking_fixture_twice():
    """Invariant 5: a second `EditPolicy` drops into `preview()` untouched."""
    p = policy_module.ClearThinkingPolicy(keep=2)
    counter = FakeCounter(
        [
            preview_module.TokenCount(input_tokens=100, original_input_tokens=None),
            preview_module.TokenCount(input_tokens=60, original_input_tokens=100),
        ]
    )
    messages = transcript_module.build_thinking_transcript(
        turns=2, thinking_chars=64
    )

    preview_module.preview(counter, messages, [], p)

    assert len(counter.calls) == 2, counter.calls
    assert counter.calls[0]["context_management"] is None
    assert counter.calls[1]["context_management"] == p.to_config()
    assert counter.calls[0]["messages"] == counter.calls[1]["messages"] == messages
    print("ok  the same core counts the thinking fixture plain, then edited")


# --------------------------------------------------------------------------- #
# A9: the SDK adapter
# --------------------------------------------------------------------------- #


def test_adapter_sends_the_beta_the_thinking_config_and_the_policy_config():
    """A9: the wire call carries the beta, adaptive thinking, and the edit dict."""
    p = shell.DEMO_POLICY
    client = FakeClient([_sdk_count(60_000), _sdk_count(38_000, 60_000)])
    messages = transcript_module.build_thinking_transcript(
        turns=2, thinking_chars=64
    )

    report = preview_module.preview(
        shell.make_thinking_counter(client, model=shell.MODEL), messages, [], p
    )

    plain, edited = client.calls
    for call in (plain, edited):
        assert call["betas"] == ["context-management-2025-06-27"], call["betas"]
        assert call["thinking"] == {"type": "adaptive"}, call["thinking"]
        assert call["model"] == shell.MODEL
        assert call["messages"] == messages
    # The plain count must not mention context_management at all - sending
    # `None` is a different request from not sending the field.
    assert "context_management" not in plain, plain
    assert edited["context_management"] == {
        "edits": [
            {
                "type": "clear_thinking_20251015",
                "keep": {"type": "thinking_turns", "value": p.keep},
            }
        ]
    }, edited["context_management"]
    assert report.tokens_saved == 22_000
    print("ok  the adapter sends the beta, adaptive thinking, and the edit dict")


def test_adapter_omits_tools_when_the_transcript_has_none():
    """The core always passes `tools=`; this fixture never has any.

    Whether `count_tokens` accepts `"tools": []` is untested against the live
    API, so the adapter does not find out: an unset optional is omitted, the
    same rule `to_edit()` follows for `keep`.
    """
    client = FakeClient([_sdk_count(9_000)])
    count = shell.make_thinking_counter(client, model=shell.MODEL)

    count(messages=[], tools=[], context_management=None)

    assert "tools" not in client.calls[0], client.calls[0]
    print("ok  an empty tool list is omitted from the request, not sent as []")


def test_adapter_sends_tools_when_there_are_some():
    """Omitting an *empty* list must not mean dropping a non-empty one."""
    client = FakeClient([_sdk_count(9_000)])
    count = shell.make_thinking_counter(client, model=shell.MODEL)
    tools = [{"name": "read_document", "input_schema": {"type": "object"}}]

    count(messages=[], tools=tools, context_management=None)

    assert client.calls[0]["tools"] == tools, client.calls[0]
    print("ok  a non-empty tool list is still sent")


def test_adapter_maps_a_missing_context_management_to_none():
    """The count endpoint reports only `original_input_tokens`, and omits the
    whole object when nothing was cleared."""
    client = FakeClient([_sdk_count(9_000)])
    count = shell.make_thinking_counter(client, model=shell.MODEL)

    result = count(messages=[], tools=[], context_management=None)

    assert result == preview_module.TokenCount(
        input_tokens=9_000, original_input_tokens=None
    ), result
    print("ok  an absent context_management object becomes original=None")


def test_adapter_propagates_api_errors():
    """A rejected signature or an unsupported model must not become 0 saved."""

    class FailingMessages:
        @staticmethod
        def count_tokens(**_kwargs):
            raise RuntimeError("400 invalid signature in thinking block")

    failing = SimpleNamespace(beta=SimpleNamespace(messages=FailingMessages()))
    count = shell.make_thinking_counter(failing, model=shell.MODEL)
    try:
        count(messages=[], tools=[], context_management=None)
    except RuntimeError as exc:
        assert "400" in str(exc), exc
    else:
        raise AssertionError("expected the API error to propagate")
    print("ok  an API error propagates instead of becoming a zero-saving report")


# --------------------------------------------------------------------------- #
# The shell's own configuration and rendering
# --------------------------------------------------------------------------- #


def test_the_demo_runs_against_a_model_that_keeps_prior_thinking():
    """On a keep-only-last-turn model the API strips prior thinking before
    counting, so the measured saving would be ~0 by construction."""
    assert shell.MODEL == "claude-sonnet-5", shell.MODEL
    assert shell.MODEL != "claude-haiku-4-5"
    print("ok  the demo model is one that keeps prior-turn thinking")


def test_render_shows_the_config_and_the_numbers():
    text = shell.render(
        preview_module.PreviewReport(
            applied=True, original_input_tokens=60_000, edited_input_tokens=38_000
        ),
        shell.DEMO_POLICY,
        model=shell.MODEL,
        turns=8,
        thinking_chars=1200,
    )
    assert '"type": "clear_thinking_20251015"' in text
    assert '"type": "thinking_turns"' in text
    assert "22000 tokens (36.7%)" in text
    assert "applied     True" in text
    assert "17 messages" in text
    print("ok  the report prints the pasteable config and the delta")


def test_render_explains_that_there_was_nothing_left_to_clear():
    text = shell.render(
        preview_module.PreviewReport(
            applied=False, original_input_tokens=9_000, edited_input_tokens=9_000
        ),
        shell.DEMO_POLICY,
        model=shell.MODEL,
        turns=2,
        thinking_chars=64,
    )
    assert "applied     False" in text
    assert "has no trigger" in text
    assert "nothing left to clear" in text
    print("ok  an unapplied edit is explained as an empty clear, not a trigger")


# --------------------------------------------------------------------------- #


def main_() -> int:
    tests = [
        test_an_omitted_keep_serialises_to_the_type_alone,
        test_a_turn_count_serialises_to_the_thinking_turns_object,
        test_keep_all_serialises_to_the_bare_string,
        test_to_config_wraps_one_edit,
        test_the_thinking_strategy_reuses_the_one_beta,
        test_keeping_zero_turns_is_rejected,
        test_a_negative_keep_is_rejected,
        test_an_unknown_keep_string_is_rejected,
        test_a_fractional_keep_is_rejected,
        test_a_bool_keep_is_rejected,
        test_every_assistant_turn_is_one_thinking_block_then_one_text_block,
        test_the_thinking_fixture_is_deterministic,
        test_the_thinking_fixture_rejects_a_degenerate_argument,
        test_the_fixture_carries_no_tools,
        test_applied_edit_reports_the_delta,
        test_unapplied_edit_reports_zero_and_does_not_raise,
        test_the_unchanged_core_counts_the_thinking_fixture_twice,
        test_adapter_sends_the_beta_the_thinking_config_and_the_policy_config,
        test_adapter_omits_tools_when_the_transcript_has_none,
        test_adapter_sends_tools_when_there_are_some,
        test_adapter_maps_a_missing_context_management_to_none,
        test_adapter_propagates_api_errors,
        test_the_demo_runs_against_a_model_that_keeps_prior_thinking,
        test_render_shows_the_config_and_the_numbers,
        test_render_explains_that_there_was_nothing_left_to_clear,
    ]
    started = time.monotonic()
    for test in tests:
        test()
    elapsed = time.monotonic() - started

    # A1, checked rather than asserted in prose: the whole suite exercised the
    # policy, the fixture, the core and the adapter without the SDK ever being
    # imported - so nothing here could have reached the network or read a key.
    assert "anthropic" not in sys.modules, "the self-test imported the SDK"
    assert elapsed < 1.0, f"self-test took {elapsed:.3f}s; something did I/O"

    print(f"\nAll {len(tests)} self-tests passed with no key and no network.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main_())
