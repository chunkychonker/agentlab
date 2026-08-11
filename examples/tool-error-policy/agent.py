"""A hand-written tool-use loop whose response to a failing tool call is decided
by the pure policy in `policy.py`: retry locally, report to the model, or abort.

This is the imperative shell. It owns the Messages API calls, the sleeping, and
the randomness; every decision it makes comes from `policy.py`, which owns none
of those. `sleep` and `jitter` are required parameters of the loop and are bound
to `time.sleep` / `random.random` only in `main()`, so the self-test physically
cannot wait on a wall clock.

See the research note this came from:
    research/2026-08-10-tool-error-handling-retries.md

Run it live (needs a key):
    export ANTHROPIC_API_KEY=sk-ant-...
    python agent.py

Run the offline self-test (no key, no network):
    python test_agent.py
"""

from __future__ import annotations

import dataclasses
import os
import random
import time
from collections.abc import Callable, Mapping, Sequence

import policy

# Model id lives in one constant so switching tiers is a one-line change.
# Cheapest current model; any current id works. See knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"

# Local retry budget for one tool call, including the first attempt.
MAX_ATTEMPTS = 3
# Model turns before the run is abandoned.
MAX_TURNS = 6
# How many times one identical failing call may be reported before aborting.
REPEAT_LIMIT = 2
# Both mirror the Anthropic SDK's own INITIAL_RETRY_DELAY / MAX_RETRY_DELAY.
BACKOFF_BASE = 0.5
BACKOFF_CAP = 8.0

# The SDK already retries the *transport* (408/409/429/5xx, 2 attempts by
# default). That is a different layer from this file, which retries the *tool*.
# Set explicitly at the edge so the two are never confused.
API_MAX_RETRIES = 2

SYSTEM_PROMPT = (
    "You are an on-call assistant. Use the fetch_metric tool to look up service "
    "metrics rather than guessing values. If a tool result reports an error, "
    "follow its instructions."
)


class AgentAborted(RuntimeError):
    """The run was stopped deliberately: a terminal tool failure, a model stuck
    repeating a failing call, or the turn budget running out.

    Raised rather than returning the last text, because a truncated run whose
    "answer" is not an answer must not be indistinguishable from a finished one.
    """


# --------------------------------------------------------------------------- #
# Tools
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class Tool:
    """A dispatchable tool: its wire schema and its implementation together.

    Holding the name once means the advertised schema and the dispatch table
    cannot drift apart.
    """

    name: str
    description: str
    input_schema: dict
    run: Callable[[Mapping[str, object]], str]

    def wire_schema(self) -> dict:
        """The tool definition exactly as the Messages API expects it."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.input_schema,
        }


_METRICS = {
    "latency_p95_ms": "182",
    "error_rate": "0.4%",
    "requests_per_sec": "1240",
}

_FLAKY_METRIC = "flaky"
_FORBIDDEN_METRIC = "forbidden"


def make_metrics_tool(*, transient_failures: int = 2) -> Tool:
    """Build the demo `fetch_metric` tool, one instance per run.

    A factory rather than a module-level function so the "flaky" counter is
    per-tool state owned by the caller, not a module global that leaks between
    runs and between tests.

    The single tool exercises all four paths on purpose:
      * ``flaky``     -> ``TransientToolError`` for the first ``transient_failures``
                         calls, then succeeds (the loop retries it locally).
      * ``forbidden`` -> ``FatalToolError`` (the loop aborts).
      * a known name  -> the value.
      * anything else -> ``ValueError`` naming the valid metrics (the model
                         corrects itself).
    """
    remaining_failures = transient_failures

    def run(tool_input: Mapping[str, object]) -> str:
        nonlocal remaining_failures

        name = tool_input.get("name")
        if not isinstance(name, str):
            raise ValueError(
                "fetch_metric requires a string 'name' argument, "
                'e.g. {"name": "latency_p95_ms"}'
            )

        if name == _FLAKY_METRIC:
            if remaining_failures > 0:
                remaining_failures -= 1
                raise policy.TransientToolError(
                    "connection reset by the metrics backend (HTTP 503)"
                )
            return "flaky metric recovered: 42"

        if name == _FORBIDDEN_METRIC:
            raise policy.FatalToolError(
                "the metrics backend rejected the API credentials (HTTP 403)"
            )

        if name in _METRICS:
            return _METRICS[name]

        known = ", ".join(sorted(_METRICS))
        raise ValueError(f"no metric named {name!r}; known metrics are: {known}")

    return Tool(
        name="fetch_metric",
        description=(
            "Look up the current value of a named service metric. Use this "
            "instead of guessing any metric value."
        ),
        input_schema={
            "type": "object",
            "properties": {
                "name": {
                    "type": "string",
                    "description": "The metric name, e.g. 'latency_p95_ms'.",
                }
            },
            "required": ["name"],
        },
        run=run,
    )


# --------------------------------------------------------------------------- #
# Calling one tool, with local retries
# --------------------------------------------------------------------------- #


@dataclasses.dataclass(frozen=True)
class Succeeded:
    """A tool call that returned. ``content`` goes back with no ``is_error``."""

    content: str


# Either the tool produced content, or the failure is the model's to fix.
# There is no third case here: an Abort leaves via an exception, because the
# caller has nothing sensible to put in a tool_result for it.
ToolOutcome = Succeeded | policy.Report


def call_tool_with_retry(
    tool: Tool,
    tool_input: Mapping[str, object],
    *,
    max_attempts: int,
    sleep: Callable[[float], None],
    jitter: Callable[[], float],
    base: float = BACKOFF_BASE,
    cap: float = BACKOFF_CAP,
) -> ToolOutcome:
    """Invoke ``tool`` and apply the failure policy, retrying locally on
    transient errors.

    ``sleep`` and ``jitter`` are injected (``time.sleep`` / ``random.random`` in
    production) so this function is deterministic and instant under test.

    Failure modes: raises ``AgentAborted`` on a terminal tool failure, having
    made exactly one invocation. Returns ``policy.Report`` for everything the
    model could plausibly fix, including a transient error that survived all
    ``max_attempts`` invocations. Never returns a partial or empty success.
    Raises ``ValueError`` here, at the boundary, if ``max_attempts < 1``: a
    budget that permits no invocation is not a retry policy, and letting it
    through means ``range(1, 1)`` skips the loop entirely and the caller is told
    ``policy.classify`` misbehaved when it was never called.
    """
    if max_attempts < 1:
        raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")

    for attempt in range(1, max_attempts + 1):
        try:
            return Succeeded(tool.run(tool_input))
        except Exception as exc:  # noqa: BLE001 - classified, never swallowed
            decision = policy.classify(exc, attempt=attempt, max_attempts=max_attempts)

            if isinstance(decision, policy.Abort):
                raise AgentAborted(decision.reason) from exc
            if isinstance(decision, policy.Report):
                return decision

            delay = policy.backoff_delay(
                attempt, base=base, cap=cap, jitter=policy.jitter_factor(jitter())
            )
            sleep(delay)

    # classify() is contractually required to stop returning Retry on the final
    # attempt, so this is unreachable. Assert rather than invent a return value.
    raise AssertionError(
        f"policy.classify returned Retry on the final attempt ({max_attempts})"
    )


# --------------------------------------------------------------------------- #
# The loop
# --------------------------------------------------------------------------- #


def run_agent(
    client,
    user_message: str,
    *,
    tools: Sequence[Tool],
    sleep: Callable[[float], None],
    jitter: Callable[[], float],
    max_turns: int = MAX_TURNS,
    max_attempts: int = MAX_ATTEMPTS,
    repeat_limit: int = REPEAT_LIMIT,
    verbose: bool = False,
) -> str:
    """Run the tool-use loop and return Claude's final text answer.

    ``client`` only needs ``.messages.create(...)`` returning an object with
    ``.stop_reason`` and ``.content``. ``sleep`` and ``jitter`` have no defaults
    on purpose: a caller must state where its clock and randomness come from.

    Invariants held here:
      * every ``tool_use`` block is answered by exactly one ``tool_result`` with
        the matching ``tool_use_id``, in the immediately following user turn;
      * the user turn contains tool_result blocks and nothing else, so they are
        necessarily first (text before them is a 400 from the API);
      * ``is_error: true`` is set if and only if the disposition was Report.

    Failure modes: raises ``AgentAborted`` on a terminal tool failure, on the
    model re-issuing one identical failing call past ``repeat_limit``, or on
    exhausting ``max_turns``. It never returns the last message text as if it
    were an answer - that is the SDK tool runner's silent-truncation shape and
    the thing this example exists to avoid. Raises ``ValueError`` if ``tools``
    is empty.
    """
    if not tools:
        raise ValueError("run_agent needs at least one tool")

    registry = {tool.name: tool for tool in tools}
    available = sorted(registry)
    wire_tools = [tool.wire_schema() for tool in tools]

    messages: list[dict] = [{"role": "user", "content": user_message}]
    # call_key -> how many times that exact failing call has been reported.
    reports_by_call: dict[str, int] = {}

    for _turn in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=wire_tools,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            return "".join(
                block.text
                for block in response.content
                if getattr(block, "type", None) == "text"
            )

        messages.append({"role": "assistant", "content": response.content})

        tool_results: list[dict] = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue

            tool = registry.get(block.name)
            if tool is None:
                # Unknown tool name is recoverable-by-model, not terminal: name
                # the real tools and let it pick again.
                outcome: ToolOutcome = policy.Report(
                    policy.unknown_tool_message(block.name, available)
                )
            else:
                outcome = call_tool_with_retry(
                    tool,
                    block.input,
                    max_attempts=max_attempts,
                    sleep=sleep,
                    jitter=jitter,
                )

            if isinstance(outcome, Succeeded):
                if verbose:
                    print(f"  [tool] {block.name}({block.input}) -> {outcome.content}")
                tool_results.append(
                    {
                        "type": "tool_result",
                        "tool_use_id": block.id,
                        "content": outcome.content,
                    }
                )
                continue

            key = policy.call_key(block.name, block.input)
            count = reports_by_call[key] = reports_by_call.get(key, 0) + 1
            if policy.is_repeat_exhausted(count, repeat_limit=repeat_limit):
                raise AgentAborted(policy.repeat_abort_message(block.name, count))

            if verbose:
                print(f"  [tool] {block.name}({block.input}) !! {outcome.message}")
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,
                    "content": outcome.message,
                    "is_error": True,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    raise AgentAborted(
        f"the agent still wanted to call tools after {max_turns} turns; "
        f"no answer was produced"
    )


# --------------------------------------------------------------------------- #
# Live entry point - the only place that reads env, sleeps, or draws randomness
# --------------------------------------------------------------------------- #


def _live_demo(client, question: str) -> None:
    """Run one live question and print the outcome, including a deliberate abort."""
    print(f"\n> {question}")
    try:
        answer = run_agent(
            client,
            question,
            tools=[make_metrics_tool()],
            sleep=time.sleep,
            jitter=random.random,
            verbose=True,
        )
    except AgentAborted as exc:
        print(f"\nRun aborted (this is the correct outcome here): {exc}")
        return
    print(f"\nFinal answer: {answer}")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set - skipping the live run.\n"
            "Set it and re-run for a real call, or run 'python test_agent.py' "
            "for the offline self-test."
        )
        return 0

    import anthropic  # imported lazily so the self-test needs no live client

    client = anthropic.Anthropic(max_retries=API_MAX_RETRIES)

    # 1. The 'flaky' metric fails twice, is retried locally, and recovers - the
    #    model never sees an error at all.
    _live_demo(client, "What is the value of the 'flaky' metric?")
    # 2. The 'forbidden' metric is a terminal failure: the run aborts loudly
    #    instead of handing the model an error it cannot fix.
    _live_demo(client, "What is the value of the 'forbidden' metric?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
