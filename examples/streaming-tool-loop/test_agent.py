"""Offline self-test for the streaming tool loop. No API key, no network.

Run:
    python test_agent.py

Covers the acceptance criteria from
``research/2026-08-16-streaming-tool-loop.md``:

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

Everything is built from ``SimpleNamespace`` fakes with the same attributes the
SDK's raw event objects have, so no SDK import is needed.
"""

from __future__ import annotations

import json
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

    def stream(self, **kwargs) -> FakeStream:
        # Copy so later mutation of `messages` doesn't rewrite our record.
        self.calls.append(list(kwargs["messages"]))
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
            "unsupported block type",
            [_block_start(0, type="thinking", thinking="")],
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
        test_calculator_rejects_forbidden_input,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
