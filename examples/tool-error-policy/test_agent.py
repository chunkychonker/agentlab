"""Offline self-test for the tool-error policy loop. No API key, no network,
no wall-clock sleeping.

Run:
    python test_agent.py

Each test maps to an acceptance criterion from the research note
(research/2026-08-10-tool-error-handling-retries.md):

  1. transient x2 then success  -> 3 invocations, run completes, no is_error
  2. always transient           -> MAX_ATTEMPTS invocations, one is_error report
                                   naming the attempt count
  3. fatal                      -> 1 invocation, AgentAborted
  4. unknown tool name          -> is_error report listing every real tool,
                                   loop continues
  5. backoff_delay              -> documented sequence, capped, jitter applied
  6. whole transcript           -> tool_result ids == preceding tool_use ids
  7. model stuck in a loop      -> AgentAborted via the repeat guard, before
                                   max_turns
  8. this file                  -> exits 0 with no key and no network

Plus unit tests for the pure policy functions and their boundary validation.
"""

from __future__ import annotations

import time
from types import SimpleNamespace

import agent
import policy

# --------------------------------------------------------------------------- #
# Test doubles
# --------------------------------------------------------------------------- #


def _block(**kwargs):
    """A message content block with attribute access, like the SDK's blocks."""
    return SimpleNamespace(**kwargs)


def _tool_use(block_id: str, name: str, tool_input: dict):
    return _block(type="tool_use", id=block_id, name=name, input=tool_input)


class FakeMessages:
    """Replays a scripted response per create() call and records the transcript
    it was handed each time."""

    def __init__(self, scripted):
        self._scripted = list(scripted)
        self.calls = []  # each entry: the messages list passed to create()

    def create(self, **kwargs):
        self.calls.append(list(kwargs["messages"]))
        if not self._scripted:
            raise AssertionError("fake client ran out of scripted responses")
        return self._scripted.pop(0)


class FakeClient:
    def __init__(self, scripted):
        self.messages = FakeMessages(scripted)


class LoopingMessages:
    """Always demands the same tool call - a model that never corrects itself."""

    def __init__(self, response_factory):
        self._factory = response_factory
        self.calls = []

    def create(self, **kwargs):
        self.calls.append(list(kwargs["messages"]))
        return self._factory()


class SleepRecorder:
    """Stands in for time.sleep: records the delays instead of waiting."""

    def __init__(self):
        self.delays: list[float] = []

    def __call__(self, seconds: float) -> None:
        self.delays.append(seconds)


def _no_jitter() -> float:
    """A random.random() stand-in whose 0.0 draw maps to a jitter factor of 1.0."""
    return 0.0


class CountingTool:
    """Wraps a tool function and counts invocations."""

    def __init__(self, fn):
        self._fn = fn
        self.invocations = 0

    def __call__(self, tool_input):
        self.invocations += 1
        return self._fn(tool_input)


def _tool_from(fn, *, name: str = "fetch_metric") -> tuple[agent.Tool, CountingTool]:
    counter = CountingTool(fn)
    tool = agent.Tool(
        name=name,
        description="test tool",
        input_schema={"type": "object", "properties": {}},
        run=counter,
    )
    return tool, counter


def _end_turn(text: str):
    return SimpleNamespace(stop_reason="end_turn", content=[_block(type="text", text=text)])


# --------------------------------------------------------------------------- #
# Structural invariant checker (acceptance criterion 6)
# --------------------------------------------------------------------------- #


def assert_paired_tool_results(messages) -> int:
    """Assert every tool_result turn answers exactly the preceding assistant
    turn's tool_use blocks, and that tool_result blocks come first.

    Returns the number of tool_result turns checked, so callers can prove the
    check was not vacuous.
    """
    checked = 0
    for i, turn in enumerate(messages):
        content = turn.get("content")
        if turn["role"] != "user" or not isinstance(content, list):
            continue
        if not any(b.get("type") == "tool_result" for b in content):
            continue

        # tool_result blocks must come first in the content array; here the turn
        # is nothing but tool_results, which satisfies that by construction.
        first_non_result = next(
            (n for n, b in enumerate(content) if b.get("type") != "tool_result"),
            len(content),
        )
        assert first_non_result == len(content), (
            f"non-tool_result block at index {first_non_result} of a tool_result turn"
        )

        assert i > 0, "tool_result turn with no preceding assistant turn"
        prev = messages[i - 1]
        assert prev["role"] == "assistant", f"tool_result turn preceded by {prev['role']}"

        requested = {b.id for b in prev["content"] if getattr(b, "type", None) == "tool_use"}
        answered = [b["tool_use_id"] for b in content]
        assert len(answered) == len(set(answered)), f"duplicate tool_use_id in {answered}"
        assert set(answered) == requested, (
            f"tool_result ids {set(answered)} != tool_use ids {requested}"
        )
        checked += 1
    return checked


# --------------------------------------------------------------------------- #
# 1. Transient twice, then success
# --------------------------------------------------------------------------- #


def test_transient_then_success() -> None:
    tool, counter = _tool_from(agent.make_metrics_tool(transient_failures=2).run)
    scripted = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use("toolu_1", "fetch_metric", {"name": "flaky"})],
        ),
        _end_turn("The flaky metric is 42."),
    ]
    client = FakeClient(scripted)
    sleeper = SleepRecorder()

    answer = agent.run_agent(
        client,
        "what is the flaky metric?",
        tools=[tool],
        sleep=sleeper,
        jitter=_no_jitter,
    )

    assert answer == "The flaky metric is 42."
    assert counter.invocations == 3, counter.invocations

    result_turn = client.messages.calls[-1][-1]
    block = result_turn["content"][0]
    assert block["content"] == "flaky metric recovered: 42"
    # Invariant 2: is_error is never set on a success, not even to False.
    assert "is_error" not in block, block

    # Two retries happened, with the documented backoff and no real waiting.
    assert sleeper.delays == [0.5, 1.0], sleeper.delays
    assert assert_paired_tool_results(client.messages.calls[-1]) == 1
    print("ok  transient failures are retried locally and never reach the model")


# --------------------------------------------------------------------------- #
# 2. Always transient -> exhausts the budget, degrades to a report
# --------------------------------------------------------------------------- #


def test_transient_exhaustion_reports_to_model() -> None:
    def always_transient(_tool_input):
        raise policy.TransientToolError("connection reset by the metrics backend")

    tool, counter = _tool_from(always_transient)
    scripted = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use("toolu_1", "fetch_metric", {"name": "flaky"})],
        ),
        _end_turn("The metrics backend is down; I can't get that value."),
    ]
    client = FakeClient(scripted)
    sleeper = SleepRecorder()

    answer = agent.run_agent(
        client,
        "what is the flaky metric?",
        tools=[tool],
        sleep=sleeper,
        jitter=_no_jitter,
    )

    assert counter.invocations == agent.MAX_ATTEMPTS, counter.invocations
    assert "down" in answer

    block = client.messages.calls[-1][-1]["content"][0]
    assert block["is_error"] is True, block
    assert str(agent.MAX_ATTEMPTS) in block["content"], block["content"]
    # The message must tell the model what to do next, not just that it failed.
    assert "different tool" in block["content"], block["content"]
    # Only the retries slept: MAX_ATTEMPTS invocations means MAX_ATTEMPTS - 1
    # waits, and the final failure is reported rather than slept on.
    assert len(sleeper.delays) == agent.MAX_ATTEMPTS - 1, sleeper.delays
    assert assert_paired_tool_results(client.messages.calls[-1]) == 1
    print("ok  exhausted transient retries degrade to one is_error report")


# --------------------------------------------------------------------------- #
# 3. Fatal -> abort, no retries
# --------------------------------------------------------------------------- #


def test_fatal_aborts_immediately() -> None:
    tool, counter = _tool_from(agent.make_metrics_tool().run)
    scripted = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use("toolu_1", "fetch_metric", {"name": "forbidden"})],
        ),
        _end_turn("unreachable"),
    ]
    client = FakeClient(scripted)
    sleeper = SleepRecorder()

    try:
        agent.run_agent(
            client,
            "what is the forbidden metric?",
            tools=[tool],
            sleep=sleeper,
            jitter=_no_jitter,
        )
    except agent.AgentAborted as exc:
        assert "terminal" in str(exc), exc
        assert "403" in str(exc), exc
    else:
        raise AssertionError("expected AgentAborted on a FatalToolError")

    assert counter.invocations == 1, counter.invocations
    assert sleeper.delays == [], sleeper.delays
    # The run stopped on the first turn instead of spending another one.
    assert len(client.messages.calls) == 1, client.messages.calls
    print("ok  a fatal tool error aborts the run with zero retries")


# --------------------------------------------------------------------------- #
# 4. Unknown tool name -> report listing the real tools, loop continues
# --------------------------------------------------------------------------- #


def test_unknown_tool_reports_and_continues() -> None:
    tool_a, counter_a = _tool_from(lambda _i: "182", name="fetch_metric")
    tool_b, _ = _tool_from(lambda _i: "ok", name="list_services")
    scripted = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use("toolu_1", "fetch_metrics", {"name": "latency_p95_ms"})],
        ),
        _end_turn("Sorry, I used the wrong tool name; the p95 latency is 182ms."),
    ]
    client = FakeClient(scripted)
    sleeper = SleepRecorder()

    answer = agent.run_agent(
        client,
        "what is p95 latency?",
        tools=[tool_a, tool_b],
        sleep=sleeper,
        jitter=_no_jitter,
    )

    # The loop continued to a second turn rather than aborting.
    assert len(client.messages.calls) == 2, client.messages.calls
    assert "182ms" in answer
    # A tool that was never named must not have been invoked.
    assert counter_a.invocations == 0

    block = client.messages.calls[-1][-1]["content"][0]
    assert block["is_error"] is True, block
    for name in ("fetch_metric", "list_services"):
        assert name in block["content"], block["content"]
    assert assert_paired_tool_results(client.messages.calls[-1]) == 1
    print("ok  an unknown tool name is reported with the real tool names")


# --------------------------------------------------------------------------- #
# 5. Backoff sequence
# --------------------------------------------------------------------------- #


def test_backoff_delay_sequence_and_cap() -> None:
    full = [
        policy.backoff_delay(n, base=0.5, cap=8.0, jitter=1.0) for n in range(1, 7)
    ]
    assert full == [0.5, 1.0, 2.0, 4.0, 8.0, 8.0], full

    jittered = [
        policy.backoff_delay(n, base=0.5, cap=8.0, jitter=0.75) for n in range(1, 7)
    ]
    assert jittered == [x * 0.75 for x in full], jittered
    # Jitter only ever shortens: the cap is a true ceiling.
    assert all(d <= 8.0 for d in full + jittered)

    # A runaway attempt counter stays finite and capped rather than overflowing.
    assert policy.backoff_delay(10_000, base=0.5, cap=8.0, jitter=1.0) == 8.0

    for bad in [
        dict(attempt=0, base=0.5, cap=8.0, jitter=1.0),
        dict(attempt=1, base=0.0, cap=8.0, jitter=1.0),
        dict(attempt=1, base=0.5, cap=0.1, jitter=1.0),
        dict(attempt=1, base=0.5, cap=8.0, jitter=0.0),
        dict(attempt=1, base=0.5, cap=8.0, jitter=1.5),
    ]:
        kwargs = dict(bad)
        attempt = kwargs.pop("attempt")
        try:
            policy.backoff_delay(attempt, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")

    print("ok  backoff follows the documented sequence, capped, jitter applied")


# --------------------------------------------------------------------------- #
# 6. Transcript pairing, with two tool_use blocks in one turn
# --------------------------------------------------------------------------- #


def test_parallel_tool_uses_are_each_answered_once() -> None:
    tool, _ = _tool_from(agent.make_metrics_tool().run)
    scripted = [
        SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _block(type="text", text="Let me look both up."),
                _tool_use("toolu_a", "fetch_metric", {"name": "latency_p95_ms"}),
                _tool_use("toolu_b", "no_such_tool", {"name": "error_rate"}),
            ],
        ),
        _end_turn("p95 latency is 182ms."),
    ]
    client = FakeClient(scripted)

    agent.run_agent(
        client,
        "give me p95 latency and error rate",
        tools=[tool],
        sleep=SleepRecorder(),
        jitter=_no_jitter,
    )

    transcript = client.messages.calls[-1]
    assert assert_paired_tool_results(transcript) == 1

    blocks = transcript[-1]["content"]
    assert len(blocks) == 2, blocks
    by_id = {b["tool_use_id"]: b for b in blocks}
    # Invariant 2 in one turn: is_error iff the disposition was Report.
    assert by_id["toolu_a"]["content"] == "182"
    assert "is_error" not in by_id["toolu_a"]
    assert by_id["toolu_b"]["is_error"] is True
    print("ok  every tool_use in a turn gets exactly one matching tool_result")


# --------------------------------------------------------------------------- #
# 7. Repeat guard fires before max_turns
# --------------------------------------------------------------------------- #


def test_repeat_guard_aborts_before_max_turns() -> None:
    def always_bad(_tool_input):
        raise ValueError("no metric named 'nope'")

    tool, counter = _tool_from(always_bad)

    def same_call_forever():
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[_tool_use("toolu_loop", "fetch_metric", {"name": "nope"})],
        )

    client = SimpleNamespace(messages=LoopingMessages(same_call_forever))
    max_turns = 6

    try:
        agent.run_agent(
            client,
            "fetch the nope metric",
            tools=[tool],
            sleep=SleepRecorder(),
            jitter=_no_jitter,
            max_turns=max_turns,
            repeat_limit=2,
        )
    except agent.AgentAborted as exc:
        assert "re-issued the same failing call" in str(exc), exc
    else:
        raise AssertionError("expected AgentAborted from the repeat guard")

    # It aborted on the 3rd identical report - strictly before the turn budget.
    assert len(client.messages.calls) == 3, client.messages.calls
    assert len(client.messages.calls) < max_turns
    assert counter.invocations == 3, counter.invocations
    print("ok  a model repeating one failing call is aborted before max_turns")


def test_max_turns_raises_instead_of_returning_text() -> None:
    """Invariant 5: a truncated run must not look like a finished one."""
    tool, _ = _tool_from(lambda _i: "182")
    counter = {"n": 0}

    def new_call_each_turn():
        counter["n"] += 1
        return SimpleNamespace(
            stop_reason="tool_use",
            content=[
                _tool_use(f"toolu_{counter['n']}", "fetch_metric", {"name": f"m{counter['n']}"})
            ],
        )

    client = SimpleNamespace(messages=LoopingMessages(new_call_each_turn))

    try:
        agent.run_agent(
            client,
            "keep going",
            tools=[tool],
            sleep=SleepRecorder(),
            jitter=_no_jitter,
            max_turns=3,
        )
    except agent.AgentAborted as exc:
        assert "after 3 turns" in str(exc), exc
    else:
        raise AssertionError("expected AgentAborted when max_turns is exhausted")

    assert len(client.messages.calls) == 3, client.messages.calls
    print("ok  exhausting max_turns raises rather than returning a partial answer")


# --------------------------------------------------------------------------- #
# Pure policy unit tests
# --------------------------------------------------------------------------- #


def test_classify_maps_each_error_class() -> None:
    transient = policy.TransientToolError("reset")
    assert policy.classify(transient, attempt=1, max_attempts=3) == policy.Retry()
    assert policy.classify(transient, attempt=2, max_attempts=3) == policy.Retry()

    final = policy.classify(transient, attempt=3, max_attempts=3)
    assert isinstance(final, policy.Report)
    assert "3 times" in final.message

    fatal = policy.classify(policy.FatalToolError("403"), attempt=1, max_attempts=3)
    assert isinstance(fatal, policy.Abort)

    # Unknown is not the same as safe to retry: it reports, it does not retry.
    for exc in [ValueError("bad arg"), KeyError("name"), RuntimeError("?")]:
        decision = policy.classify(exc, attempt=1, max_attempts=3)
        assert isinstance(decision, policy.Report), (exc, decision)
        assert type(exc).__name__ in decision.message

    # A single-attempt budget must never yield Retry.
    assert isinstance(
        policy.classify(transient, attempt=1, max_attempts=1), policy.Report
    )

    for kwargs in [dict(attempt=0, max_attempts=3), dict(attempt=4, max_attempts=3),
                   dict(attempt=1, max_attempts=0)]:
        try:
            policy.classify(transient, **kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")

    print("ok  classify maps transient/fatal/unknown to retry/abort/report")


def test_jitter_factor_bounds() -> None:
    assert policy.jitter_factor(0.0) == 1.0
    assert policy.jitter_factor(1.0) == policy.JITTER_FLOOR
    for raw in [0.0, 0.1, 0.5, 0.9, 1.0]:
        assert policy.JITTER_FLOOR <= policy.jitter_factor(raw) <= 1.0
    for bad in [-0.01, 1.01]:
        try:
            policy.jitter_factor(bad)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {bad}")
    print("ok  jitter factor stays in [0.75, 1.0] and only ever shortens delays")


def test_call_key_and_repeat_guard() -> None:
    assert policy.call_key("t", {"a": 1, "b": 2}) == policy.call_key("t", {"b": 2, "a": 1})
    assert policy.call_key("t", {"a": 1}) != policy.call_key("t", {"a": 2})
    assert policy.call_key("t", {"a": 1}) != policy.call_key("u", {"a": 1})

    assert policy.is_repeat_exhausted(1, repeat_limit=2) is False
    assert policy.is_repeat_exhausted(2, repeat_limit=2) is False
    assert policy.is_repeat_exhausted(3, repeat_limit=2) is True

    for kwargs in [dict(report_count=0, repeat_limit=2), dict(report_count=1, repeat_limit=0)]:
        try:
            policy.is_repeat_exhausted(**kwargs)
        except ValueError:
            pass
        else:
            raise AssertionError(f"expected ValueError for {kwargs}")
    print("ok  call keys are order-insensitive and the repeat guard trips on time")


def test_unknown_tool_message_requires_tools() -> None:
    msg = policy.unknown_tool_message("nope", ["a", "b"])
    assert "nope" in msg and "a, b" in msg
    try:
        policy.unknown_tool_message("nope", [])
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError when no tools are registered")
    print("ok  unknown_tool_message names the alternatives and rejects an empty set")


def test_run_agent_rejects_an_empty_toolset() -> None:
    try:
        agent.run_agent(
            FakeClient([]),
            "hi",
            tools=[],
            sleep=SleepRecorder(),
            jitter=_no_jitter,
        )
    except ValueError:
        pass
    else:
        raise AssertionError("expected ValueError for an empty toolset")
    print("ok  run_agent refuses to start with no tools")


def test_call_tool_with_retry_rejects_a_nonsense_budget() -> None:
    """A retry budget below 1 is rejected at the boundary, before the tool runs.

    Regression: the docstring promised a ValueError, but `range(1, 0 + 1)` is
    empty, so the loop body never ran, `policy.classify` was never called, and
    the caller got `AssertionError: policy.classify returned Retry on the final
    attempt (0)` - an accusation against a function that had not executed.
    """
    tool, counter = _tool_from(agent.make_metrics_tool().run)

    for budget in (0, -1):
        try:
            agent.call_tool_with_retry(
                tool,
                {"name": "latency_p95_ms"},
                max_attempts=budget,
                sleep=SleepRecorder(),
                jitter=_no_jitter,
            )
        except ValueError as exc:
            assert str(budget) in str(exc), f"the bad value must be named: {exc}"
        else:
            raise AssertionError(f"expected ValueError for max_attempts={budget}")

    # Rejected at the boundary means the tool was never invoked at all.
    assert counter.invocations == 0, counter.invocations
    print("ok  a max_attempts below 1 is rejected before the tool is ever called")


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_transient_then_success,
        test_transient_exhaustion_reports_to_model,
        test_fatal_aborts_immediately,
        test_unknown_tool_reports_and_continues,
        test_backoff_delay_sequence_and_cap,
        test_parallel_tool_uses_are_each_answered_once,
        test_repeat_guard_aborts_before_max_turns,
        test_max_turns_raises_instead_of_returning_text,
        test_classify_maps_each_error_class,
        test_jitter_factor_bounds,
        test_call_key_and_repeat_guard,
        test_unknown_tool_message_requires_tools,
        test_run_agent_rejects_an_empty_toolset,
        test_call_tool_with_retry_rejects_a_nonsense_budget,
    ]
    started = time.monotonic()
    for t in tests:
        t()
    elapsed = time.monotonic() - started

    # Acceptance criterion 8: offline, keyless, and fast because nothing slept.
    assert elapsed < 1.0, f"self-test took {elapsed:.3f}s; something really slept"
    print(f"\nAll {len(tests)} self-tests passed in {elapsed*1000:.0f}ms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
