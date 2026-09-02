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

Tests 8-12 cover the parallel fan-out. They use a *second* fake
(`_ConcurrentFakeClient`) that scripts each reply by the user message's
content instead of by call order, because a thread pool appends to any
call-recording list in completion order - so these tests assert on the count
and contents of `create_calls`, never on their positions.

  8. run_specialists_parallel against a fake whose .create blocks on a
     threading.Barrier(3): the calls must genuinely overlap or the barrier
     times out and raises BrokenBarrierError. A serial loop cannot pass it.
  9. run_specialists_parallel with the *last* subtask's call scripted (via two
     threading.Events) to return before the first's still returns outputs in
     plan order - and run_orchestrator_parallel's synthesis prompt lists them
     in plan order too.
 10. run_orchestrator_parallel call accounting for a 3-item plan: exactly 1
     parse + 3 specialist + 1 synthesis create call, each specialist call's
     `system` naming the specialist for its own instructions, and the return
     value is the scripted synthesis text.
 11. run_specialists_parallel(max_workers=0) raises ValueError before making
     any API call.
 12. A specialist raising inside a worker propagates out of
     run_specialists_parallel instead of yielding a partial result list.
"""

from __future__ import annotations

import threading
import time
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
# Fake #2, for the parallel fan-out. Under a thread pool the order of
# `create_calls` is completion order, which is not deterministic - so this fake
# scripts replies by the user message's *content*, keeping each scripted output
# bound to its subtask no matter which thread wins.
# --------------------------------------------------------------------------- #


# Any latch a test waits on gets this bound, so a broken implementation fails
# in seconds with BrokenBarrierError / an AssertionError instead of hanging.
_LATCH_TIMEOUT_SECONDS = 5.0

_SUBTASKS = [
    Subtask(specialist="research", instructions="instructions-0"),
    Subtask(specialist="copywriting", instructions="instructions-1"),
    Subtask(specialist="fact-checking", instructions="instructions-2"),
]
_REPLIES = {
    "instructions-0": "out-0",
    "instructions-1": "out-1",
    "instructions-2": "out-2",
}
_SPECIALIST_OF = {s.instructions: s.specialist for s in _SUBTASKS}


class _ConcurrentFakeMessages:
    """Thread-safe fake for `.parse(...)` / `.create(...)` that picks each
    `create` reply by the user message's content, not by call order.

    `on_specialist_call(instructions)` runs inside the call, just before it
    returns: tests use it to block on a barrier, to sequence returns with
    events, or to raise a worker error. A `create` whose prompt is not a
    scripted specialist instruction is treated as the synthesis call."""

    def __init__(
        self,
        *,
        specialist_replies: dict[str, str],
        synthesis_reply: str | None = None,
        parse_results=None,
        on_specialist_call=None,
    ):
        self._specialist_replies = dict(specialist_replies)
        self._synthesis_reply = synthesis_reply
        self._parse_results = list(parse_results or [])
        self._on_specialist_call = on_specialist_call
        self._lock = threading.Lock()
        self.parse_calls: list[dict] = []
        self.create_calls: list[dict] = []

    def parse(self, **kwargs):
        with self._lock:
            self.parse_calls.append(kwargs)
            return self._parse_results.pop(0)

    def create(self, **kwargs):
        with self._lock:
            self.create_calls.append(kwargs)
        prompt = kwargs["messages"][0]["content"]
        if prompt in self._specialist_replies:
            if self._on_specialist_call is not None:
                self._on_specialist_call(prompt)
            return SimpleNamespace(content=[_text_block(self._specialist_replies[prompt])])
        if self._synthesis_reply is None:
            raise AssertionError(f"unscripted .messages.create call: {prompt!r}")
        return SimpleNamespace(content=[_text_block(self._synthesis_reply)])


class _ConcurrentFakeClient:
    def __init__(self, **kwargs):
        self.messages = _ConcurrentFakeMessages(**kwargs)


def _specialist_calls(client) -> list[dict]:
    """The create calls whose prompt is one of the scripted instructions."""
    return [c for c in client.messages.create_calls if c["messages"][0]["content"] in _REPLIES]


def _synthesis_calls(client) -> list[dict]:
    return [c for c in client.messages.create_calls if c["messages"][0]["content"] not in _REPLIES]


def _returns_last_subtask_first():
    """Callback that makes the LAST subtask's call return before the FIRST
    one's, using two events - so plan-ordered results cannot be an accident of
    completion order."""
    first_started = threading.Event()
    last_returned = threading.Event()

    def sequence(instructions: str) -> None:
        if instructions == "instructions-0":
            first_started.set()
            if not last_returned.wait(timeout=_LATCH_TIMEOUT_SECONDS):
                raise AssertionError(
                    "the last subtask's call never returned - the specialist calls "
                    "did not overlap, so this ran serially"
                )
        elif instructions == "instructions-2":
            if not first_started.wait(timeout=_LATCH_TIMEOUT_SECONDS):
                raise AssertionError(
                    "the first subtask's call never started - the specialist calls "
                    "did not overlap, so this ran serially"
                )
            last_returned.set()

    return sequence


# --------------------------------------------------------------------------- #
# 8. The specialist calls genuinely overlap - a serial loop breaks the barrier.
# --------------------------------------------------------------------------- #


def test_specialists_run_concurrently_not_serially() -> None:
    barrier = threading.Barrier(len(_SUBTASKS), timeout=_LATCH_TIMEOUT_SECONDS)
    clock_lock = threading.Lock()
    entries: list[float] = []
    exits: list[float] = []

    def wait_for_the_others(_instructions: str) -> None:
        with clock_lock:
            entries.append(time.perf_counter())
        # A sequential implementation leaves this barrier one party short:
        # after _LATCH_TIMEOUT_SECONDS it raises threading.BrokenBarrierError,
        # which propagates out of run_specialists_parallel and fails the test.
        barrier.wait()
        with clock_lock:
            exits.append(time.perf_counter())

    client = _ConcurrentFakeClient(
        specialist_replies=_REPLIES, on_specialist_call=wait_for_the_others
    )

    outputs = agent.run_specialists_parallel(client, _SUBTASKS, max_workers=len(_SUBTASKS))

    assert outputs == ["out-0", "out-1", "out-2"], outputs
    assert len(client.messages.create_calls) == 3, client.messages.create_calls
    # Every call had entered before any had left: they were in flight together.
    assert max(entries) < min(exits), (entries, exits)
    print("ok  run_specialists_parallel's calls are in flight together (barrier of 3 releases)")


# --------------------------------------------------------------------------- #
# 9. Plan order survives a reversed completion order.
# --------------------------------------------------------------------------- #


def test_results_are_in_plan_order_regardless_of_finish_order() -> None:
    client = _ConcurrentFakeClient(
        specialist_replies=_REPLIES, on_specialist_call=_returns_last_subtask_first()
    )

    outputs = agent.run_specialists_parallel(client, _SUBTASKS, max_workers=len(_SUBTASKS))

    assert outputs == ["out-0", "out-1", "out-2"], outputs

    # ...and the pairs handed to synthesize() are in plan order too.
    orchestrator = _ConcurrentFakeClient(
        parse_results=[SimpleNamespace(parsed_output=Plan(subtasks=_SUBTASKS))],
        specialist_replies=_REPLIES,
        synthesis_reply="Final synthesized answer.",
        on_specialist_call=_returns_last_subtask_first(),
    )

    result = agent.run_orchestrator_parallel(orchestrator, "the original task")

    assert result == "Final synthesized answer."
    synthesis_calls = _synthesis_calls(orchestrator)
    assert len(synthesis_calls) == 1, synthesis_calls
    prompt = synthesis_calls[0]["messages"][0]["content"]
    assert prompt.index("out-0") < prompt.index("out-1") < prompt.index("out-2"), prompt
    print("ok  results stay in plan order when the last subtask's call returns first")


# --------------------------------------------------------------------------- #
# 10. run_orchestrator_parallel makes exactly the same calls as the serial path.
# --------------------------------------------------------------------------- #


def test_run_orchestrator_parallel_call_accounting() -> None:
    client = _ConcurrentFakeClient(
        parse_results=[SimpleNamespace(parsed_output=Plan(subtasks=_SUBTASKS))],
        specialist_replies=_REPLIES,
        synthesis_reply="Final synthesized answer.",
    )

    result = agent.run_orchestrator_parallel(client, "the original task")

    assert result == "Final synthesized answer."
    assert len(client.messages.parse_calls) == 1
    assert len(client.messages.create_calls) == 4, client.messages.create_calls

    specialist_calls = _specialist_calls(client)
    assert len(specialist_calls) == 3, specialist_calls
    # Order is meaningless here (completion order), so assert on contents: every
    # subtask was dispatched exactly once, with its own specialist persona.
    dispatched = sorted(c["messages"][0]["content"] for c in specialist_calls)
    assert dispatched == sorted(_REPLIES), dispatched
    for call in specialist_calls:
        specialist = _SPECIALIST_OF[call["messages"][0]["content"]]
        assert specialist in call["system"], (specialist, call["system"])

    print("ok  run_orchestrator_parallel makes 1 parse + 3 specialist + 1 synthesis call")


# --------------------------------------------------------------------------- #
# 11 + 12. Failure modes: bad max_workers, and a worker that raises.
# --------------------------------------------------------------------------- #


def test_specialists_parallel_rejects_bad_max_workers() -> None:
    client = _ConcurrentFakeClient(specialist_replies=_REPLIES)
    try:
        agent.run_specialists_parallel(client, _SUBTASKS[:1], max_workers=0)
    except ValueError:
        pass
    else:
        raise AssertionError("run_specialists_parallel should reject max_workers=0")
    assert client.messages.create_calls == [], client.messages.create_calls
    print("ok  run_specialists_parallel(max_workers=0) raises ValueError before any API call")


def test_specialists_parallel_propagates_worker_error() -> None:
    def boom(instructions: str) -> None:
        if instructions == "instructions-1":
            raise RuntimeError("boom")

    client = _ConcurrentFakeClient(specialist_replies=_REPLIES, on_specialist_call=boom)
    try:
        outputs = agent.run_specialists_parallel(client, _SUBTASKS, max_workers=len(_SUBTASKS))
    except RuntimeError as exc:
        assert str(exc) == "boom", exc
    else:
        raise AssertionError(
            f"a failing specialist must propagate, not return partial results: {outputs!r}"
        )
    print("ok  a specialist's exception propagates out of run_specialists_parallel")


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
        test_specialists_run_concurrently_not_serially,
        test_results_are_in_plan_order_regardless_of_finish_order,
        test_run_orchestrator_parallel_call_accounting,
        test_specialists_parallel_rejects_bad_max_workers,
        test_specialists_parallel_propagates_worker_error,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
