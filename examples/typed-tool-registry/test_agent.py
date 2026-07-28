"""Offline self-test for the typed tool registry. No API key, no network.

Run:
    python test_agent.py

Unlike minimal-agent-loop's stdlib-only test, this one imports `anthropic`
directly (for `@beta_tool`) - see requirements.txt. Schema generation and
Pydantic validation are real, pure, local code, so there is no fake client to
script: every assertion below exercises the actual `BetaFunctionTool` objects
built in agent.py.

Covers:
  1. Each tool's generated schema: name, non-empty description, object type,
     the exact required parameter set, and the JSON-Schema type per property
     matching the Python type hint.
  2. TOOL_REGISTRY has exactly 3 entries, keyed by name, with true identity
     (not just equality) to the decorated tool objects.
  3. Each tool's `.call(...)` returns the correct value for a known input.
  4. Each tool's `.call(...)` raises ValueError on a wrong-typed argument and
     on a missing required argument - Pydantic validation runs before the
     tool body ever executes.
  5. run_agent's handling of the runner's final message: returns the joined
     text on a clean finish, and raises RuntimeError when max_iterations was
     hit mid-tool-call (final stop_reason == "tool_use"). A tiny fake client
     stands in for the runner - no key, no network.
"""

from __future__ import annotations

import agent


# --------------------------------------------------------------------------- #
# Fakes for the runner seam (test 5). Only the surface run_agent touches:
# client.beta.messages.tool_runner(...).until_done() -> message with
# .stop_reason and .content (a list of blocks with .type / .text).
# --------------------------------------------------------------------------- #

class _FakeBlock:
    def __init__(self, text: str) -> None:
        self.type = "text"
        self.text = text


class _FakeMessage:
    def __init__(self, *, stop_reason: str, content: list) -> None:
        self.stop_reason = stop_reason
        self.content = content


class _FakeRunner:
    def __init__(self, message: _FakeMessage) -> None:
        self._message = message

    def until_done(self) -> _FakeMessage:
        return self._message


class _FakeClient:
    """Returns a preset final message from tool_runner(...).until_done()."""

    def __init__(self, message: _FakeMessage) -> None:
        message_holder = message

        class _Messages:
            def tool_runner(self, **_kwargs):
                return _FakeRunner(message_holder)

        class _Beta:
            messages = _Messages()

        self.beta = _Beta()

# Expected JSON-Schema property name -> JSON-Schema type, per tool. Also
# doubles as the expected `required` set (every parameter here is required).
TOOL_PARAM_TYPES = {
    "word_count": {"text": "string"},
    "reverse_text": {"text": "string"},
    "is_prime": {"n": "integer"},
}


# --------------------------------------------------------------------------- #
# 1. Schema shape, generated from real type hints
# --------------------------------------------------------------------------- #

def test_schemas_match_type_hints() -> None:
    assert {t.name for t in agent.TOOLS} == set(TOOL_PARAM_TYPES), (
        f"unexpected tool set: {[t.name for t in agent.TOOLS]}"
    )

    for tool in agent.TOOLS:
        expected_params = TOOL_PARAM_TYPES[tool.name]

        assert tool.description, f"{tool.name} has an empty description"

        schema = tool.input_schema
        assert schema["type"] == "object", f"{tool.name} schema type: {schema['type']!r}"
        assert set(schema["required"]) == set(expected_params), (
            f"{tool.name} required params: {schema['required']!r}"
        )

        for param, expected_type in expected_params.items():
            actual_type = schema["properties"][param]["type"]
            assert actual_type == expected_type, (
                f"{tool.name}.{param}: expected JSON type {expected_type!r}, got {actual_type!r}"
            )

    print("ok  each tool's schema (name/description/type/required) matches its type hints")


# --------------------------------------------------------------------------- #
# 2. Registry lookup
# --------------------------------------------------------------------------- #

def test_registry_is_a_plain_dict_by_name() -> None:
    assert len(agent.TOOL_REGISTRY) == 3, agent.TOOL_REGISTRY
    assert agent.TOOL_REGISTRY.keys() == {"word_count", "reverse_text", "is_prime"}
    # Identity, not just equality: the registry holds the same tool objects.
    assert agent.TOOL_REGISTRY["word_count"] is agent.word_count
    assert agent.TOOL_REGISTRY["reverse_text"] is agent.reverse_text
    assert agent.TOOL_REGISTRY["is_prime"] is agent.is_prime
    print("ok  TOOL_REGISTRY has exactly 3 entries, keyed by name with true identity")


# --------------------------------------------------------------------------- #
# 3. Correct-input results
# --------------------------------------------------------------------------- #

def test_tools_call_correctly_on_valid_input() -> None:
    assert agent.word_count.call({"text": "the quick brown fox"}) == "4"
    assert agent.reverse_text.call({"text": "agent"}) == "tnega"
    assert agent.is_prime.call({"n": 91}) == "false"  # 91 = 7 * 13, a classic false-prime trap
    assert agent.is_prime.call({"n": 97}) == "true"
    print("ok  each tool's .call(...) returns the correct value for a known input")


# --------------------------------------------------------------------------- #
# 4. Bad input is rejected before the tool body runs
# --------------------------------------------------------------------------- #

def test_tools_reject_bad_input() -> None:
    for tool, bad_input in [
        (agent.word_count, {"text": 123}),
        (agent.word_count, {}),
        (agent.reverse_text, {"text": 123}),
        (agent.reverse_text, {}),
        (agent.is_prime, {"n": "seven"}),
        (agent.is_prime, {}),
    ]:
        try:
            tool.call(bad_input)
        except ValueError:
            pass
        else:
            raise AssertionError(f"{tool.name}.call({bad_input!r}) should have raised ValueError")
    print("ok  each tool's .call(...) raises ValueError on wrong-typed or missing arguments")


# --------------------------------------------------------------------------- #
# 5. run_agent's handling of the runner's final message
# --------------------------------------------------------------------------- #

def test_run_agent_returns_text_on_clean_finish() -> None:
    client = _FakeClient(
        _FakeMessage(stop_reason="end_turn", content=[_FakeBlock("42"), _FakeBlock(" done")])
    )
    assert agent.run_agent(client, "anything") == "42 done"
    print("ok  run_agent joins the final text blocks on a clean finish")


def test_run_agent_raises_on_max_iterations() -> None:
    # Model still wanted a tool when the cap was hit -> stop_reason "tool_use".
    client = _FakeClient(_FakeMessage(stop_reason="tool_use", content=[]))
    try:
        agent.run_agent(client, "anything", max_iterations=3)
    except RuntimeError as exc:
        assert "3" in str(exc), f"error should name the iteration cap: {exc!r}"
    else:
        raise AssertionError("run_agent should raise RuntimeError when max_iterations is exhausted")
    print("ok  run_agent raises RuntimeError instead of returning '' on max_iterations")


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_schemas_match_type_hints,
        test_registry_is_a_plain_dict_by_name,
        test_tools_call_correctly_on_valid_input,
        test_tools_reject_bad_input,
        test_run_agent_returns_text_on_clean_finish,
        test_run_agent_raises_on_max_iterations,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
