"""Offline self-test for the orchestrator/subagents example. No API key, no
network.

Run:
    python test_agent.py

Like typed-tool-registry's test, this imports the real `pydantic` (via
agent.py) to exercise the real Plan/Subtask models - the max_length=3
constraint is genuinely enforced *client-side*, not just asserted about
(it is only a description hint on the wire - see README.md and agent.py's
module docstring). The Messages-API seam (`.messages.parse` /
`.messages.create`) is scripted with a small FakeClient, same
SimpleNamespace-block style as minimal-agent-loop's test.

Covers:
  1. Plan(subtasks=[...4 items...]) raises pydantic.ValidationError - the
     max_length=3 constraint is real client-side, not just a prompt hint.
  2. plan_task against a fake .messages.parse returning
     SimpleNamespace(parsed_output=Plan(...)) returns that exact Plan.
  3. plan_task against a fake .messages.parse that *raises*
     pydantic_core.ValidationError (built from real validation of a 4-item
     Plan, the actual failure the real SDK's `parse_text` raises
     uncaught) - asserts plan_task catches it and re-raises RuntimeError.
  4. plan_task against a fake .messages.parse returning
     SimpleNamespace(parsed_output=None) raises RuntimeError - the distinct
     no-text-block case, not silently swallowed.
  5. run_specialist against a fake .messages.create returns the scripted
     text, and the call's `system` kwarg contains the subtask's specialist
     string - specialization actually reached the API call.
  6. synthesize against a fake .messages.create returns the scripted text,
     and the call's `messages` contain every specialist's output string.
  7. run_orchestrator end-to-end against a FakeClient scripted for a 2-item
     plan: exactly 1 parse call + 2 specialist create calls + 1 synthesis
     create call (4 total), each specialist call's `system` used the right
     specialist, and the returned value is the scripted synthesis text.
"""

from __future__ import annotations

from types import SimpleNamespace

import pydantic

import agent
from agent import Plan, Subtask


# --------------------------------------------------------------------------- #
# Fakes for the client seam. Only the surface agent.py touches:
#   client.messages.parse(...)  -> object with .parsed_output
#   client.messages.create(...) -> object with .content (list of text blocks)
# --------------------------------------------------------------------------- #


def _text_block(text: str):
    return SimpleNamespace(type="text", text=text)


class _FakeMessages:
    """Returns scripted responses for .parse(...) and .create(...) calls, in
    the order each method is invoked, and records every call's kwargs."""

    def __init__(self, *, parse_results=None, create_results=None):
        self._parse_results = list(parse_results or [])
        self._create_results = list(create_results or [])
        self.parse_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def parse(self, **kwargs):
        self.parse_calls.append(kwargs)
        result = self._parse_results.pop(0)
        if isinstance(result, BaseException):
            raise result
        return result

    def create(self, **kwargs):
        self.create_calls.append(kwargs)
        return self._create_results.pop(0)


class _FakeClient:
    def __init__(self, **kwargs):
        self.messages = _FakeMessages(**kwargs)


# --------------------------------------------------------------------------- #
# 1. Schema constraint is real, local, pure - no client involved.
# --------------------------------------------------------------------------- #


def test_plan_max_length_is_enforced() -> None:
    too_many = [Subtask(specialist="a", instructions="x") for _ in range(4)]
    try:
        Plan(subtasks=too_many)
    except pydantic.ValidationError:
        pass
    else:
        raise AssertionError("Plan should reject more than 3 subtasks")
    print("ok  Plan(subtasks=[...4 items...]) raises pydantic.ValidationError")


# --------------------------------------------------------------------------- #
# 2 + 3. plan_task's two paths against a fake .messages.parse
# --------------------------------------------------------------------------- #


def test_plan_task_returns_parsed_output() -> None:
    plan = Plan(subtasks=[Subtask(specialist="research", instructions="Find X")])
    client = _FakeClient(parse_results=[SimpleNamespace(parsed_output=plan)])

    result = agent.plan_task(client, "some task")

    assert result is plan
    print("ok  plan_task returns the parsed Plan from a successful .messages.parse call")


def test_plan_task_raises_on_validation_error() -> None:
    """Exercises the REAL failure path: the real SDK's `client.messages.parse`
    calls pydantic's `TypeAdapter(...).validate_json(...)` with no try/except,
    so a model response with too many subtasks raises
    `pydantic_core.ValidationError` *out of* `.messages.parse` itself, never
    reaching a `parsed_output is None` check. We get a genuine
    ValidationError by actually running real pydantic validation on a
    4-subtask payload (not hand-building a fake exception), then scripting
    the fake `.messages.parse` to raise that exact error, and assert
    plan_task catches it and re-raises RuntimeError."""
    too_many = [{"specialist": "a", "instructions": "x"} for _ in range(4)]
    real_validation_error: pydantic.ValidationError | None = None
    try:
        Plan(subtasks=too_many)
        raise AssertionError("expected Plan(subtasks=[...4 items...]) to raise")
    except pydantic.ValidationError as exc:
        real_validation_error = exc

    client = _FakeClient(parse_results=[real_validation_error])
    try:
        agent.plan_task(client, "some task")
    except RuntimeError as exc:
        assert isinstance(exc.__cause__, pydantic.ValidationError)
    else:
        raise AssertionError(
            "plan_task should catch pydantic_core.ValidationError from "
            ".messages.parse and re-raise RuntimeError"
        )
    print(
        "ok  plan_task catches pydantic_core.ValidationError from .messages.parse "
        "and re-raises RuntimeError"
    )


def test_plan_task_raises_on_none_parsed_output() -> None:
    """The distinct, real "no text block at all" case: `.messages.parse`
    returns successfully but `response.parsed_output` is None."""
    client = _FakeClient(parse_results=[SimpleNamespace(parsed_output=None)])
    try:
        agent.plan_task(client, "some task")
    except RuntimeError:
        pass
    else:
        raise AssertionError("plan_task should raise RuntimeError when parsed_output is None")
    print("ok  plan_task raises RuntimeError when parsed_output is None (no text block at all)")


# --------------------------------------------------------------------------- #
# 4. run_specialist reaches the API with the right system prompt.
# --------------------------------------------------------------------------- #


def test_run_specialist_uses_specialist_system_prompt() -> None:
    subtask = Subtask(specialist="fact-checking", instructions="Verify the date")
    client = _FakeClient(create_results=[SimpleNamespace(content=[_text_block("Verified: 2026.")])])

    output = agent.run_specialist(client, subtask)

    assert output == "Verified: 2026."
    call = client.messages.create_calls[0]
    assert "fact-checking" in call["system"], call["system"]
    assert call["messages"] == [{"role": "user", "content": "Verify the date"}]
    print("ok  run_specialist returns scripted text and its system prompt names the specialist")


# --------------------------------------------------------------------------- #
# 5. synthesize combines every specialist's output into its prompt.
# --------------------------------------------------------------------------- #


def test_synthesize_includes_every_specialist_output() -> None:
    results = [
        (Subtask(specialist="research", instructions="Find X"), "X is true."),
        (Subtask(specialist="copywriting", instructions="Write a tagline"), "Ship faster."),
    ]
    client = _FakeClient(create_results=[SimpleNamespace(content=[_text_block("Final: X is true, ship faster.")])])

    output = agent.synthesize(client, "the original task", results)

    assert output == "Final: X is true, ship faster."
    call = client.messages.create_calls[0]
    prompt = call["messages"][0]["content"]
    assert "X is true." in prompt
    assert "Ship faster." in prompt
    assert "the original task" in prompt
    print("ok  synthesize's prompt contains every specialist's output and the original task")


# --------------------------------------------------------------------------- #
# 6. run_orchestrator end-to-end: exactly 4 calls, right shape, right order.
# --------------------------------------------------------------------------- #


def test_run_orchestrator_makes_exactly_four_calls_in_order() -> None:
    plan = Plan(
        subtasks=[
            Subtask(specialist="research", instructions="Find X"),
            Subtask(specialist="copywriting", instructions="Write a tagline"),
        ]
    )
    client = _FakeClient(
        parse_results=[SimpleNamespace(parsed_output=plan)],
        create_results=[
            SimpleNamespace(content=[_text_block("X is true.")]),  # specialist 1
            SimpleNamespace(content=[_text_block("Ship faster.")]),  # specialist 2
            SimpleNamespace(content=[_text_block("Final synthesized answer.")]),  # synthesis
        ],
    )

    result = agent.run_orchestrator(client, "the original task")

    assert result == "Final synthesized answer."
    assert len(client.messages.parse_calls) == 1
    assert len(client.messages.create_calls) == 3, client.messages.create_calls

    specialist_call_1, specialist_call_2, synthesis_call = client.messages.create_calls
    assert "research" in specialist_call_1["system"]
    assert "copywriting" in specialist_call_2["system"]
    # The synthesis call's prompt carries both specialists' outputs.
    synthesis_prompt = synthesis_call["messages"][0]["content"]
    assert "X is true." in synthesis_prompt
    assert "Ship faster." in synthesis_prompt

    print("ok  run_orchestrator makes exactly 1 parse + 2 specialist + 1 synthesis call, in order")


# --------------------------------------------------------------------------- #


def main() -> int:
    tests = [
        test_plan_max_length_is_enforced,
        test_plan_task_returns_parsed_output,
        test_plan_task_raises_on_validation_error,
        test_plan_task_raises_on_none_parsed_output,
        test_run_specialist_uses_specialist_system_prompt,
        test_synthesize_includes_every_specialist_output,
        test_run_orchestrator_makes_exactly_four_calls_in_order,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
