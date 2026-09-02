"""Offline self-test for the streaming tool loop. No API key, no network.

Run:
    python test_agent.py

Covers the acceptance criteria from
``research/2026-08-16-streaming-tool-loop.md`` (1-5) and
``research/2026-09-02-streaming-thinking-accumulator.md`` (6-8):

  1. Replaying the docs' own tool-use SSE transcript through ``accumulate()``
     rebuilds the ``tool_use`` block's id, name, and fully-parsed ``input``
     from the same ``partial_json`` fragments the real API sent.
  2. A text-only sequence yields the concatenated text and the ``stop_reason``
     from the trailing ``message_delta``.
  3. A scripted two-turn streaming run (tool-use turn -> dispatch -> final text)
     behaves exactly like the non-streaming loop in
     ``examples/minimal-agent-loop/``: same tool call, same matching
     ``tool_use_id``, same final answer.
  4. Malformed accumulated JSON raises ``json.JSONDecodeError`` instead of
     handing the tool a wrong or empty input.
  5. Out-of-order / structurally broken block events raise ``ValueError``
     naming the index, and the ``max_turns`` cap still stops a runaway loop.
  6. A ``thinking`` block is rebuilt from its ``thinking_delta`` fragments plus
     its ``signature_delta``, a ``display:"omitted"`` block (no thinking deltas)
     is legal rather than an error, and a ``redacted_thinking`` block survives
     its delta-less start/stop pair with ``data`` intact.
  7. The loop echoes the thinking block back **unmodified and ahead of** the
     ``tool_use`` block on the tool-result turn - the thing that keeps a
     thinking-enabled tool turn from 400ing.
  8. The verbose shell prints thinking and text fragments as they stream while
     passing every event through unchanged.

Everything is built from ``SimpleNamespace`` fakes with the same attributes the
SDK's raw event objects have, so no SDK import is needed.
"""

from __future__ import annotations

import io
import json
from contextlib import redirect_stdout
from types import SimpleNamespace

import agent
from accumulator import accumulate


# --------------------------------------------------------------------------- #
# Event fixtures: plain objects with the same attributes as the SDK's raw events
# --------------------------------------------------------------------------- #

def _message_start() -> SimpleNamespace:
    # The Message here always arrives with empty content; nothing to accumulate.
    return SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(id="msg_014p7gG3wDgGV9EUtLvnow3U", content=[]),
    )


def _block_start(index: int, **content_block) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_start",
        index=index,
        content_block=SimpleNamespace(**content_block),
    )


def _block_delta(index: int, **delta) -> SimpleNamespace:
    return SimpleNamespace(
        type="content_block_delta",
        index=index,
        delta=SimpleNamespace(**delta),
    )


def _block_stop(index: int) -> SimpleNamespace:
    return SimpleNamespace(type="content_block_stop", index=index)


def _message_delta(stop_reason: str | None) -> SimpleNamespace:
    return SimpleNamespace(
        type="message_delta",
        delta=SimpleNamespace(stop_reason=stop_reason, stop_sequence=None),
        usage=SimpleNamespace(output_tokens=89),
    )


def _message_stop() -> SimpleNamespace:
    return SimpleNamespace(type="message_stop")


def _ping() -> SimpleNamespace:
    return SimpleNamespace(type="ping")


def _text_deltas(index: int, fragments: list[str]) -> list[SimpleNamespace]:
    return [_block_delta(index, type="text_delta", text=f) for f in fragments]


def _json_deltas(index: int, fragments: list[str]) -> list[SimpleNamespace]:
    return [_block_delta(index, type="input_json_delta", partial_json=f) for f in fragments]


def _thinking_deltas(index: int, fragments: list[str]) -> list[SimpleNamespace]:
    return [_block_delta(index, type="thinking_delta", thinking=f) for f in fragments]


def _signature_delta(index: int, signature: str) -> SimpleNamespace:
    return _block_delta(index, type="signature_delta", signature=signature)


def _thinking_start(index: int) -> SimpleNamespace:
    # The real start seeds BOTH fields empty - "empty, not absent", the same way
    # a tool_use block opens with input={}.
    return _block_start(index, type="thinking", thinking="", signature="")


# The tool_use half of this is transcribed verbatim from the streaming docs'
# get_weather SSE example (quoted in the research note): the id, the name, and
# every partial_json fragment in arrival order, including the empty first one.
# The docs' full transcript opens with a text block at index 0 before the tool
# block at index 1; its exact wording is not load-bearing, so a short stand-in is
# used here to keep the block indices contiguous the way a real stream's are.
WEATHER_TOOL_USE_ID = "toolu_01T1x1fJ34qAmk2tNTrN7Up6"
WEATHER_PARTIAL_JSON = ["", '{"location":', ' "San', " Francisc", "o,", ' CA"}']

DOC_TOOL_USE_EVENTS = [
    _message_start(),
    _block_start(0, type="text", text=""),
    _ping(),
    *_text_deltas(0, ["Okay", ", let me look up", " the weather."]),
    _block_stop(0),
    _block_start(1, type="tool_use", id=WEATHER_TOOL_USE_ID, name="get_weather", input={}),
    *_json_deltas(1, WEATHER_PARTIAL_JSON),
    _block_stop(1),
    # Forward-compat: an event type this code has never heard of must be ignored.
    SimpleNamespace(type="some_future_event_type", payload="ignore me"),
    _message_delta("tool_use"),
    _message_stop(),
]


# --------------------------------------------------------------------------- #
# 1. Tool-use reconstruction from the docs' own transcript
# --------------------------------------------------------------------------- #

def test_doc_transcript_rebuilds_tool_use_input() -> None:
    message = accumulate(DOC_TOOL_USE_EVENTS)

    assert message.stop_reason == "tool_use", message.stop_reason
    assert len(message.content) == 2, message.content
    assert message.content[0] == {"type": "text", "text": "Okay, let me look up the weather."}
    assert message.content[1] == {
        "type": "tool_use",
        "id": WEATHER_TOOL_USE_ID,
        "name": "get_weather",
        # Rebuilt from six string fragments, none of which is valid JSON alone.
        "input": {"location": "San Francisco, CA"},
    }
    print("ok  docs' tool_use SSE transcript rebuilds id, name and parsed input")


# --------------------------------------------------------------------------- #
# 2. Text-only stream
# --------------------------------------------------------------------------- #

def test_text_only_stream_concatenates_and_reports_stop_reason() -> None:
    events = [
        _message_start(),
        _block_start(0, type="text", text=""),
        *_text_deltas(0, ["Hello", " there", "!"]),
        _ping(),
        _block_stop(0),
        # An early message_delta may carry no stop_reason yet; the last one wins.
        _message_delta(None),
        _message_delta("end_turn"),
        _message_stop(),
    ]

    message = accumulate(events)

    assert message.content == [{"type": "text", "text": "Hello there!"}], message.content
    assert message.stop_reason == "end_turn", message.stop_reason
    print("ok  text-only stream concatenates deltas and reports stop_reason")


# --------------------------------------------------------------------------- #
# 3. The loop, driven by a fake streaming client
# --------------------------------------------------------------------------- #

class FakeStream:
    """Stands in for the SDK's stream context manager: enter it, iterate events."""

    def __init__(self, events):
        self._events = list(events)

    def __enter__(self) -> "FakeStream":
        return self

    def __exit__(self, *exc_info) -> bool:
        return False

    def __iter__(self):
        return iter(self._events)


class FakeStreamingMessages:
    """Serves one scripted event list per stream() call, recording what it was sent."""

    def __init__(self, scripted_turns):
        self._scripted = list(scripted_turns)
        self.calls = []  # each entry is the messages list passed to stream()
        self.requests = []  # each entry is the full kwargs passed to stream()

    def stream(self, **kwargs) -> FakeStream:
        # Copy so later mutation of `messages` doesn't rewrite our record.
        self.calls.append(list(kwargs["messages"]))
        self.requests.append(dict(kwargs))
        return FakeStream(self._scripted.pop(0))


class FakeClient:
    def __init__(self, scripted_turns):
        self.messages = FakeStreamingMessages(scripted_turns)


CALC_TOOL_USE_ID = "toolu_abc123"

# Turn 1: a text block, then a calculator tool_use whose input arrives in pieces
# that split mid-string, exactly as the docs warn real fragments can.
TURN_ONE_EVENTS = [
    _message_start(),
    _block_start(0, type="text", text=""),
    *_text_deltas(0, ["Let me", " compute that."]),
    _block_stop(0),
    _block_start(1, type="tool_use", id=CALC_TOOL_USE_ID, name="calculator", input={}),
    *_json_deltas(1, ["", '{"expression"', ': "4839', " * 12", '84"}']),
    _block_stop(1),
    _message_delta("tool_use"),
    _message_stop(),
]

TURN_TWO_EVENTS = [
    _message_start(),
    _block_start(0, type="text", text=""),
    *_text_deltas(
        0,
        ["4839 * 1284 = 6213276", ", which is more", " than five million."],
    ),
    _block_stop(0),
    _message_delta("end_turn"),
    _message_stop(),
]


def test_streaming_loop_dispatches_tool_and_returns_final_text() -> None:
    client = FakeClient([TURN_ONE_EVENTS, TURN_TWO_EVENTS])

    answer = agent.run_agent_streaming(
        client, "What is 4839 * 1284, and is that more than five million?"
    )

    # (c) returned the final text, concatenated across deltas
    assert answer == "4839 * 1284 = 6213276, which is more than five million.", answer

    # The loop opened exactly two streams.
    assert len(client.messages.calls) == 2, client.messages.calls

    # (a)+(b) inspect the messages sent on the SECOND call: the echoed assistant
    # tool_use turn followed by our tool_result turn.
    second_call_messages = client.messages.calls[1]
    tool_result_turn = second_call_messages[-1]
    assert tool_result_turn["role"] == "user"
    result_block = tool_result_turn["content"][0]
    assert result_block["type"] == "tool_result"
    # (b) tool_use_id matches the block we answered
    assert result_block["tool_use_id"] == CALC_TOOL_USE_ID
    # (a) the tool actually ran on the reassembled input, not on a fragment
    assert result_block["content"] == "6213276", result_block

    # The assistant tool_use turn was echoed back before the tool_result, in the
    # wire shape the API expects.
    assistant_turn = second_call_messages[-2]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["content"][1] == {
        "type": "tool_use",
        "id": CALC_TOOL_USE_ID,
        "name": "calculator",
        "input": {"expression": "4839 * 1284"},
    }, assistant_turn["content"]

    print("ok  streaming loop dispatches the tool and returns the final answer")


# --------------------------------------------------------------------------- #
# 4. Malformed partial JSON
# --------------------------------------------------------------------------- #

def test_truncated_tool_input_raises_json_error() -> None:
    events = [
        _message_start(),
        _block_start(0, type="tool_use", id="toolu_trunc", name="calculator", input={}),
        # The stream died mid-value: this never becomes valid JSON.
        *_json_deltas(0, ['{"expression"', ': "4839 * 12']),
        _block_stop(0),
        _message_delta("tool_use"),
        _message_stop(),
    ]

    try:
        accumulate(events)
    except json.JSONDecodeError as exc:
        assert "content block 0" in str(exc), str(exc)
        print("ok  truncated tool input raises JSONDecodeError instead of an empty dict")
    else:
        raise AssertionError("expected json.JSONDecodeError for truncated partial_json")


# --------------------------------------------------------------------------- #
# 5. Structural failure modes and the turn cap
# --------------------------------------------------------------------------- #

def _bad_sequences() -> list[tuple[str, list[SimpleNamespace]]]:
    """Each case is (what's wrong, the event sequence) and must raise ValueError."""
    return [
        (
            "delta for an index that was never started",
            [_block_delta(0, type="text_delta", text="hi")],
        ),
        (
            "stop for an index that was never started",
            [_block_stop(0)],
        ),
        (
            "second start on an index that is still open",
            [_block_start(0, type="text", text=""), _block_start(0, type="text", text="")],
        ),
        (
            "start on an index that already stopped",
            [
                _block_start(0, type="text", text=""),
                _block_stop(0),
                _block_start(0, type="text", text=""),
            ],
        ),
        (
            "text_delta into a tool_use block",
            [
                _block_start(0, type="tool_use", id="toolu_x", name="calculator", input={}),
                _block_delta(0, type="text_delta", text="oops"),
            ],
        ),
        (
            "input_json_delta into a text block",
            [
                _block_start(0, type="text", text=""),
                _block_delta(0, type="input_json_delta", partial_json="{}"),
            ],
        ),
        (
            # `thinking` used to live here; it is a supported type now, so the
            # guard is exercised with a real API block type this example still
            # does not assemble.
            "unsupported block type",
            [_block_start(0, type="server_tool_use", id="srvtoolu_x", name="web_search")],
        ),
        (
            # The axonhub#1105 service-side bug: a signature_delta whose index
            # pointed at a tool_use block. Must not be silently absorbed.
            "signature_delta whose index is the tool_use block",
            [
                _block_start(0, type="tool_use", id="toolu_z", name="calculator", input={}),
                _signature_delta(0, "EqQBCgIYAhIM1gbcDa9GJwZA2b"),
            ],
        ),
        (
            "thinking_delta into a text block",
            [
                _block_start(0, type="text", text=""),
                *_thinking_deltas(0, ["oops"]),
            ],
        ),
        (
            "text_delta into a thinking block",
            [
                _thinking_start(0),
                _block_delta(0, type="text_delta", text="oops"),
            ],
        ),
        (
            "any delta at all for a redacted_thinking index",
            [
                _block_start(0, type="redacted_thinking", data="EroBCoYBGAIiQL"),
                *_thinking_deltas(0, ["oops"]),
            ],
        ),
        (
            "redacted_thinking start with no data to round-trip",
            [_block_start(0, type="redacted_thinking")],
        ),
        (
            "tool input that parses to a non-object",
            [
                _block_start(0, type="tool_use", id="toolu_y", name="calculator", input={}),
                *_json_deltas(0, ["[1, 2]"]),
                _block_stop(0),
            ],
        ),
        (
            "stream ends with a block still open",
            [_block_start(0, type="text", text=""), _message_stop()],
        ),
        (
            "gap in the block indices",
            [
                _block_start(1, type="text", text="only block"),
                _block_stop(1),
            ],
        ),
    ]


def test_broken_block_sequences_raise_value_error() -> None:
    for description, events in _bad_sequences():
        try:
            accumulate(events)
        except json.JSONDecodeError as exc:  # must be a plain ValueError, not this
            raise AssertionError(f"{description}: got JSONDecodeError {exc}") from exc
        except ValueError as exc:
            assert str(exc), f"{description}: raised an empty message"
        else:
            raise AssertionError(f"{description}: expected ValueError, nothing raised")
    print(f"ok  {len(_bad_sequences())} broken event sequences each raise ValueError")


def test_loop_enforces_max_turns() -> None:
    # A stream that always demands another tool call should hit the turn cap.
    class EndlessMessages:
        def stream(self, **kwargs) -> FakeStream:
            return FakeStream(TURN_ONE_EVENTS)

    client = SimpleNamespace(messages=EndlessMessages())

    try:
        agent.run_agent_streaming(client, "loop forever", max_turns=3)
    except RuntimeError as exc:
        assert "did not finish" in str(exc)
        print("ok  loop enforces max_turns and raises when exceeded")
    else:
        raise AssertionError("expected RuntimeError when max_turns is exceeded")


# --------------------------------------------------------------------------- #
# 6. Thinking and redacted_thinking blocks
#
# Fixtures follow the streaming docs' thinking trace, quoted in
# research/2026-09-02-streaming-thinking-accumulator.md: the block opens with
# BOTH `thinking` and `signature` empty, one or more `thinking_delta`s carry the
# reasoning text, and a single `signature_delta` lands just before the stop.
# --------------------------------------------------------------------------- #

# The docs print the signature truncated ("EqQBCgIYAhIM1gbcDa9GJwZA2b..."), so
# this is a stand-in of the right shape rather than a transcription. Only its
# byte-for-byte survival through the accumulator is under test; its contents are
# opaque to every line of code here.
DOC_SIGNATURE = "EqQBCgIYAhIM1gbcDa9GJwZA2b7dEgxgDcpOiZOaMMbrxx4aDGKvsY0i5nFvR3ImIA"
DOC_THINKING_FRAGMENTS = [
    "I need to find the GCD",
    " of 27 and 18. Let me use",
    " the Euclidean algorithm.",
]

DOC_THINKING_EVENTS = [
    _message_start(),
    _thinking_start(0),
    _ping(),
    *_thinking_deltas(0, DOC_THINKING_FRAGMENTS),
    # Exactly one, immediately before the stop, per the docs.
    _signature_delta(0, DOC_SIGNATURE),
    _block_stop(0),
    _block_start(1, type="text", text=""),
    *_text_deltas(1, ["The GCD of 27 and 18", " is 9."]),
    _block_stop(1),
    _message_delta("end_turn"),
    _message_stop(),
]


def test_thinking_transcript_rebuilds_thinking_and_signature() -> None:
    message = accumulate(DOC_THINKING_EVENTS)

    assert message.stop_reason == "end_turn", message.stop_reason
    assert message.content == [
        {
            "type": "thinking",
            "thinking": "".join(DOC_THINKING_FRAGMENTS),
            # Byte-for-byte: the server decrypts this, so any normalisation is a 400.
            "signature": DOC_SIGNATURE,
        },
        {"type": "text", "text": "The GCD of 27 and 18 is 9."},
    ], message.content
    print("ok  thinking_delta fragments + signature_delta rebuild a thinking block")


def test_omitted_thinking_block_is_legal_with_empty_thinking() -> None:
    # display:"omitted" (the default on the newest models): no thinking_delta
    # events at all. The block opens, takes one signature_delta, and closes.
    # thinking == "" is a legal finished value, NOT the truncation an empty
    # tool_use input signals.
    events = [
        _message_start(),
        _thinking_start(0),
        _signature_delta(0, DOC_SIGNATURE),
        _block_stop(0),
        _block_start(1, type="text", text=""),
        *_text_deltas(1, ["9."]),
        _block_stop(1),
        _message_delta("end_turn"),
        _message_stop(),
    ]

    message = accumulate(events)

    assert message.content[0] == {
        "type": "thinking",
        "thinking": "",
        "signature": DOC_SIGNATURE,
    }, message.content
    print("ok  display:omitted thinking block yields thinking=\"\" without raising")


def test_signature_policy_allows_missing_and_joins_fragments() -> None:
    """The two decisions this example makes where the docs are silent.

    (a) Zero signature_delta events -> signature "" and no exception: a pure
        accumulator cannot know the model or display mode, so the loud failure
        for a genuinely missing signature belongs at the API boundary.
    (b) More than one signature_delta -> joined in arrival order, which is a
        superset of the documented "exactly one" and identity for it.
    """
    no_signature = accumulate(
        [_thinking_start(0), *_thinking_deltas(0, ["hmm"]), _block_stop(0)]
    )
    assert no_signature.content == [
        {"type": "thinking", "thinking": "hmm", "signature": ""}
    ], no_signature.content

    split_signature = accumulate(
        [
            _thinking_start(0),
            *_thinking_deltas(0, ["hmm"]),
            _signature_delta(0, DOC_SIGNATURE[:10]),
            _signature_delta(0, DOC_SIGNATURE[10:]),
            _block_stop(0),
        ]
    )
    assert split_signature.content == [
        {"type": "thinking", "thinking": "hmm", "signature": DOC_SIGNATURE}
    ], split_signature.content
    print("ok  missing signature is allowed and split signatures join in order")


# Likewise a stand-in: the docs describe `data` as opaque and never print a real
# one. Shape only; the test is that it comes back unchanged.
REDACTED_DATA = "EroBCoYBGAIiQAmvSHSk1Xl1z9vOJRPPZ8bVDo7QqPqIWLpqTGWvBOaB9ivQ"


def test_redacted_thinking_survives_its_deltaless_start_stop_pair() -> None:
    # redacted_thinking arrives COMPLETE in content_block_start - no deltas at
    # all - and must be round-tripped byte-for-byte. See the README note on this
    # shape's provenance.
    events = [
        _message_start(),
        _block_start(0, type="redacted_thinking", data=REDACTED_DATA),
        _block_stop(0),
        _block_start(1, type="text", text=""),
        *_text_deltas(1, ["Done."]),
        _block_stop(1),
        _message_delta("end_turn"),
        _message_stop(),
    ]

    message = accumulate(events)

    assert message.content == [
        {"type": "redacted_thinking", "data": REDACTED_DATA},
        {"type": "text", "text": "Done."},
    ], message.content
    print("ok  redacted_thinking block round-trips its opaque data with no deltas")


# --------------------------------------------------------------------------- #
# 7. The loop with thinking on: the assistant turn must go back unmodified
# --------------------------------------------------------------------------- #

THINKING_TURN_ONE_EVENTS = [
    _message_start(),
    _thinking_start(0),
    *_thinking_deltas(0, ["The user wants 4839 * 1284.", " I should use the calculator."]),
    _signature_delta(0, DOC_SIGNATURE),
    _block_stop(0),
    _block_start(1, type="tool_use", id=CALC_TOOL_USE_ID, name="calculator", input={}),
    *_json_deltas(1, ["", '{"expression"', ': "4839', " * 12", '84"}']),
    _block_stop(1),
    _message_delta("tool_use"),
    _message_stop(),
]


def test_loop_echoes_thinking_block_ahead_of_tool_use() -> None:
    client = FakeClient([THINKING_TURN_ONE_EVENTS, TURN_TWO_EVENTS])

    answer = agent.run_agent_streaming(
        client, "What is 4839 * 1284, and is that more than five million?"
    )

    assert answer == "4839 * 1284 = 6213276, which is more than five million.", answer
    assert len(client.messages.calls) == 2, client.messages.calls

    # The whole point: on the SECOND request the assistant turn still carries the
    # thinking block, complete, unmodified, and BEFORE the tool_use block. Drop
    # it, reorder it, or edit the signature and the real API answers 400.
    assistant_turn = client.messages.calls[1][-2]
    assert assistant_turn["role"] == "assistant"
    assert assistant_turn["content"] == [
        {
            "type": "thinking",
            "thinking": "The user wants 4839 * 1284. I should use the calculator.",
            "signature": DOC_SIGNATURE,
        },
        {
            "type": "tool_use",
            "id": CALC_TOOL_USE_ID,
            "name": "calculator",
            "input": {"expression": "4839 * 1284"},
        },
    ], assistant_turn["content"]

    # ...and the tool still ran on the reassembled input, thinking notwithstanding.
    tool_result_turn = client.messages.calls[1][-1]
    assert tool_result_turn["role"] == "user"
    assert tool_result_turn["content"][0]["tool_use_id"] == CALC_TOOL_USE_ID
    assert tool_result_turn["content"][0]["content"] == "6213276", tool_result_turn

    # The request that produced it asked for thinking in the first place, with a
    # budget the API will accept: budget_tokens >= 1024 and strictly < max_tokens
    # (equal or greater is a 400 before a single token is generated).
    request = client.messages.requests[0]
    assert request["thinking"] == {"type": "enabled", "budget_tokens": 1024}, request
    assert 1024 <= request["thinking"]["budget_tokens"] < request["max_tokens"], request

    print("ok  loop echoes the thinking block unmodified, ahead of the tool_use")


# --------------------------------------------------------------------------- #
# 8. The verbose shell: printing is a pass-through, never a transformation
# --------------------------------------------------------------------------- #

def test_echo_deltas_prints_thinking_and_text_and_passes_events_through() -> None:
    buffer = io.StringIO()
    with redirect_stdout(buffer):
        echoed = list(agent._echo_deltas(DOC_THINKING_EVENTS))

    printed = buffer.getvalue()
    assert "[thinking] " + "".join(DOC_THINKING_FRAGMENTS) in printed, printed
    assert "The GCD of 27 and 18 is 9." in printed, printed
    # The signature is not prose; it must not be echoed as if it were.
    assert DOC_SIGNATURE not in printed, printed
    # Pass-through: every event, same objects, same order. accumulate() must see
    # an identical stream whether or not the shell is printing.
    assert echoed == DOC_THINKING_EVENTS
    assert accumulate(echoed).content == accumulate(DOC_THINKING_EVENTS).content

    print("ok  verbose echo prints thinking + text and yields every event unchanged")


# --------------------------------------------------------------------------- #
# The duplicated calculator: this example ships its own copy, so it gets its own
# guard that forbidden input is rejected rather than executed.
# --------------------------------------------------------------------------- #

def test_calculator_rejects_forbidden_input() -> None:
    for bad in ["__import__('os')", "open('/etc/passwd')", "x + 1", "1 +"]:
        out = agent.calculator(bad)
        assert out.startswith("Error:"), f"expected error for {bad!r}, got {out!r}"
    assert agent.calculator("4839 * 1284") == "6213276"
    print("ok  calculator safely rejects forbidden/malformed input")


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_doc_transcript_rebuilds_tool_use_input,
        test_text_only_stream_concatenates_and_reports_stop_reason,
        test_streaming_loop_dispatches_tool_and_returns_final_text,
        test_truncated_tool_input_raises_json_error,
        test_broken_block_sequences_raise_value_error,
        test_loop_enforces_max_turns,
        test_thinking_transcript_rebuilds_thinking_and_signature,
        test_omitted_thinking_block_is_legal_with_empty_thinking,
        test_signature_policy_allows_missing_and_joins_fragments,
        test_redacted_thinking_survives_its_deltaless_start_stop_pair,
        test_loop_echoes_thinking_block_ahead_of_tool_use,
        test_echo_deltas_prints_thinking_and_text_and_passes_events_through,
        test_calculator_rejects_forbidden_input,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
