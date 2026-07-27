"""Offline self-test for the minimal agent loop. No API key, no network.

Run:
    python test_agent.py

Covers:
  1. The calculator tool: correct arithmetic, and safe rejection of forbidden
     input (no execution, no raise).
  2. The manual loop mechanics, driven by a scripted fake client: the loop must
     invoke the tool with the scripted input, send back a tool_result whose
     tool_use_id matches the tool_use block, and return the final text.
"""

from __future__ import annotations

from types import SimpleNamespace

import agent


# --------------------------------------------------------------------------- #
# 1. Tool unit tests
# --------------------------------------------------------------------------- #

def test_calculator_arithmetic() -> None:
    assert agent.calculator("4839 * 1284") == "6213276"
    assert agent.calculator("2 + 3 * 4") == "14"
    assert agent.calculator("(1 + 2) ** 3") == "27"
    assert agent.calculator("10 / 4") == "2.5"
    assert agent.calculator("-5 + 2") == "-3"
    print("ok  calculator evaluates arithmetic correctly")


def test_calculator_rejects_forbidden_input() -> None:
    # Must NOT execute and must NOT raise - it returns an error string.
    for bad in ["__import__('os')", "open('/etc/passwd')", "x + 1", "1 +"]:
        out = agent.calculator(bad)
        assert out.startswith("Error:"), f"expected error for {bad!r}, got {out!r}"
    print("ok  calculator safely rejects forbidden/malformed input")


# --------------------------------------------------------------------------- #
# 2. Loop test with a fake client
# --------------------------------------------------------------------------- #

def _block(**kwargs):
    """A message content block with attribute access, like the SDK's blocks."""
    return SimpleNamespace(**kwargs)


class FakeMessages:
    """Returns a scripted sequence of responses, one per create() call, and
    records the messages it was handed so the test can inspect them."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []  # each entry is the messages list passed to create()

    def create(self, **kwargs):
        # Copy so later mutation of `messages` doesn't rewrite our record.
        self.calls.append(list(kwargs["messages"]))
        return self._scripted.pop(0)


class FakeClient:
    def __init__(self, scripted):
        self.messages = FakeMessages(scripted)


def test_loop_runs_tool_and_returns_final_text() -> None:
    tool_use_id = "toolu_abc123"
    scripted = [
        # Turn 1: Claude asks to call the calculator.
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _block(type="text", text="Let me compute that."),
                _block(
                    type="tool_use",
                    id=tool_use_id,
                    name="calculator",
                    input={"expression": "4839 * 1284"},
                ),
            ],
        ),
        # Turn 2: with the tool result in hand, Claude answers.
        SimpleNamespace(
            stop_reason="end_turn",
            content=[_block(type="text", text="4839 * 1284 = 6213276, which is more than five million.")],
        ),
    ]
    client = FakeClient(scripted)

    answer = agent.run_agent(client, "What is 4839 * 1284, and is that more than five million?")

    # (c) returned the final text
    assert answer == "4839 * 1284 = 6213276, which is more than five million."

    # The loop made exactly two API calls.
    assert len(client.messages.calls) == 2, client.messages.calls

    # (a)+(b) inspect the messages sent on the SECOND call: they must contain the
    # echoed assistant tool_use turn followed by our tool_result turn.
    second_call_messages = client.messages.calls[1]
    tool_result_turn = second_call_messages[-1]
    assert tool_result_turn["role"] == "user"
    result_block = tool_result_turn["content"][0]
    assert result_block["type"] == "tool_result"
    # (b) tool_use_id matches the block we answered
    assert result_block["tool_use_id"] == tool_use_id
    # (a) the tool actually ran on the scripted input and produced the real result
    assert result_block["content"] == "6213276"

    # The assistant tool_use turn was echoed back before the tool_result.
    assistant_turn = second_call_messages[-2]
    assert assistant_turn["role"] == "assistant"

    print("ok  manual loop dispatches the tool and returns the final answer")


def test_loop_raises_when_it_never_finishes() -> None:
    # A client that always demands another tool call should hit the turn cap.
    def endless():
        while True:
            yield SimpleNamespace(
                stop_reason="tool_use",
                content=[
                    _block(
                        type="tool_use",
                        id="toolu_loop",
                        name="calculator",
                        input={"expression": "1 + 1"},
                    )
                ],
            )

    gen = endless()

    class EndlessMessages:
        def create(self, **kwargs):
            return next(gen)

    client = SimpleNamespace(messages=EndlessMessages())

    try:
        agent.run_agent(client, "loop forever", max_turns=3)
    except RuntimeError as exc:
        assert "did not finish" in str(exc)
        print("ok  loop enforces max_turns and raises when exceeded")
    else:
        raise AssertionError("expected RuntimeError when max_turns is exceeded")


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_calculator_arithmetic,
        test_calculator_rejects_forbidden_input,
        test_loop_runs_tool_and_returns_final_text,
        test_loop_raises_when_it_never_finishes,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
