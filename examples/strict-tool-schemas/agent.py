"""The same two typed tools registered twice - once with `strict=True`, once
without - so the only difference on the wire is the `strict` flag.

`examples/typed-tool-registry/` shows that `@beta_tool` validates model-supplied
input with Pydantic and raises `ValueError` on a bad enum value or a stringified
integer. That is the *cure*: the bad input already exists by then.
`"strict": true` is the *prevention*: the same `input_schema` is compiled into a
sampling grammar, so the model cannot emit that input in the first place.

The point of registering both is that the schemas are byte-identical.
`STRICT_TOOLS[i].input_schema == LOOSE_TOOLS[i].input_schema` and
`STRICT_TOOLS[i].func is LOOSE_TOOLS[i].func`; the strict payload just carries
one extra top-level key. The schema didn't change, the enforcement did.

No `anthropic-beta` header: structured outputs / strict tool use went GA on
2026-01-29 and the header is no longer required.

See the research note this came from:
    research/2026-09-03-strict-tool-schemas.md

Run it live (needs a key, ~2 billed Haiku calls):
    export ANTHROPIC_API_KEY=sk-ant-...
    python agent.py

Run the offline self-test (no key, no network):
    python test_agent.py
"""

from __future__ import annotations

import os
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from typing import Literal

from anthropic import beta_tool
from anthropic.lib.tools import BetaFunctionTool

# Model id lives in one constant so switching tiers is a one-line change.
# Haiku 4.5 is on the published structured-outputs support list (2025-12-04),
# so the cheap default is eligible here. See knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"

MAX_TOKENS = 1024

# The runner's own default is None (unbounded); always pass a cap.
DEFAULT_MAX_ITERATIONS = 6

# One prompt, run once per registry. It needs both tools and it dangles two
# traps: "top priority" is not one of the enum's three values, and "three
# teammates" is a word, not a JSON integer.
LIVE_PROMPT = (
    "Mark the task 'submit tax forms' as top priority, then schedule a kickoff "
    "titled 'Q3 Planning' on 2026-10-05 for me plus three teammates."
)


# --------------------------------------------------------------------------- #
# The tools - defined once, as plain typed functions.
# --------------------------------------------------------------------------- #

Priority = Literal["low", "medium", "high"]


@beta_tool(strict=True)
def set_priority(task: str, level: Priority) -> str:
    """Set a task's priority level.

    Args:
        task: The task to prioritise.
        level: The priority to assign.
    """
    return f"{task!r} priority set to {level}"


@beta_tool(strict=True)
def schedule_event(title: str, date: str, attendees: int) -> str:
    """Schedule a calendar event.

    Args:
        title: The event title.
        date: The event date, as YYYY-MM-DD.
        attendees: How many people will attend.
    """
    return f"{title!r} scheduled for {date} with {attendees} attendee(s)"


# --------------------------------------------------------------------------- #
# Two registries over one set of functions.
# --------------------------------------------------------------------------- #

STRICT_TOOLS: list[BetaFunctionTool] = [set_priority, schedule_event]

# Same underlying functions, re-wrapped with no `strict`. Deriving the loose
# list from the strict one is what makes "identical schemas" true by
# construction instead of by careful copy-paste.
LOOSE_TOOLS: list[BetaFunctionTool] = [beta_tool(t.func) for t in STRICT_TOOLS]

STRICT_REGISTRY: dict[str, BetaFunctionTool] = {t.name: t for t in STRICT_TOOLS}
LOOSE_REGISTRY: dict[str, BetaFunctionTool] = {t.name: t for t in LOOSE_TOOLS}


# --------------------------------------------------------------------------- #
# What the model actually emitted
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class ToolCall:
    """One `tool_use` block the model produced: which tool, and the raw input."""

    name: str
    input: Mapping[str, object]


def tool_calls_in(message: object) -> list[ToolCall]:
    """Every `tool_use` block in one assistant message, in order.

    Pure. A message with no tool calls yields `[]`. Blocks without a `type` of
    `"tool_use"` are skipped, so text and thinking blocks pass through
    harmlessly.
    """
    return [
        ToolCall(name=block.name, input=block.input)
        for block in getattr(message, "content", []) or []
        if getattr(block, "type", None) == "tool_use"
    ]


def final_text(message: object, max_iterations: int) -> str:
    """The joined text blocks of a finished run.

    Raises RuntimeError if `message.stop_reason` is `"tool_use"`: the runner
    stopped because `max_iterations` was reached while the model still wanted a
    tool, so there is no real answer. Raising beats returning `""`, which is
    indistinguishable from a genuinely empty answer.
    """
    if getattr(message, "stop_reason", None) == "tool_use":
        raise RuntimeError(f"Agent did not finish within {max_iterations} iterations")
    return "".join(
        block.text for block in message.content if getattr(block, "type", None) == "text"
    )


# --------------------------------------------------------------------------- #
# The SDK-driven loop
# --------------------------------------------------------------------------- #


def run_agent(
    client,
    user_message: str,
    tools: Sequence[BetaFunctionTool],
    *,
    max_iterations: int = DEFAULT_MAX_ITERATIONS,
    observer: Callable[[ToolCall], None] | None = None,
) -> str:
    """Run the agent over `tools` via `client.beta.messages.tool_runner` and
    return Claude's final text answer.

    `tools` is explicit so the same function can be pointed at either registry.
    `observer`, if given, is called once per `tool_use` block as each assistant
    turn arrives - the only way to see the inputs the model produced, since the
    final message alone does not contain them. It must not raise; an exception
    from it aborts the run mid-conversation.

    Raises RuntimeError if the run hits `max_iterations` mid-tool-call. API
    errors from the SDK propagate untouched.
    """
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=MAX_TOKENS,
        tools=list(tools),
        messages=[{"role": "user", "content": user_message}],
        max_iterations=max_iterations,
    )

    for message in runner:
        if observer is not None:
            for call in tool_calls_in(message):
                observer(call)

    return final_text(runner.until_done(), max_iterations)


# --------------------------------------------------------------------------- #
# Live entry point
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CallOutcome:
    """What one registry made of one observed input.

    `accepted` and `detail` come from a single evaluation, so the printed detail
    can never disagree with the verdict it is counted under.
    """

    call: ToolCall
    accepted: bool
    detail: str


def evaluate(call: ToolCall, registry: Mapping[str, BetaFunctionTool]) -> CallOutcome:
    """Put one observed input through the registry's own Pydantic validation.

    Runs the tool body on success; both tools here are deterministic string
    formatters with no side effects. Never raises: a `ValueError` from
    validation and an unknown tool name both come back as a rejected outcome,
    because a survey of what the model emitted should report a bad input, not
    die on it.
    """
    tool = registry.get(call.name)
    if tool is None:
        return CallOutcome(call, False, f"no tool named {call.name!r} in this registry")
    try:
        return CallOutcome(call, True, tool.call(dict(call.input)))
    except ValueError as exc:
        return CallOutcome(call, False, f"ValueError: {str(exc).splitlines()[0]}")


def report_lines(outcome: CallOutcome) -> list[str]:
    """Render one outcome: the raw input, each value's Python type, the result.

    Pure - the evaluation already happened.
    """
    call = outcome.call
    return [
        f"  {call.name}({dict(call.input)!r})",
        *(f"      {key}: {type(value).__name__}" for key, value in call.input.items()),
        f"    .call -> {outcome.detail}",
    ]


def _run_side(
    client,
    label: str,
    tools: Sequence[BetaFunctionTool],
    registry: Mapping[str, BetaFunctionTool],
) -> None:
    """Run LIVE_PROMPT through one registry and print what the model emitted."""
    print(f"\n--- {label} ---")
    observed: list[ToolCall] = []
    answer = run_agent(client, LIVE_PROMPT, tools, observer=observed.append)

    outcomes = [evaluate(call, registry) for call in observed]
    for outcome in outcomes:
        for line in report_lines(outcome):
            print(line)

    rejected = [o for o in outcomes if not o.accepted]
    if not outcomes:
        print("  verdict: the model called no tools on this run")
    elif rejected:
        print(f"  verdict: {len(rejected)} of {len(outcomes)} observed inputs failed validation")
    else:
        print(f"  verdict: all {len(outcomes)} observed inputs validated")
    print(f"  final answer: {answer}")


def main() -> int:
    """Print the loose-vs-strict comparison for one live prompt.

    Without ANTHROPIC_API_KEY set, prints a one-line note and returns 0 rather
    than failing - the offline self-test is the thing to run in that case.
    """
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set - skipping the live run. "
            "Run 'python test_agent.py' for the offline self-test."
        )
        return 0

    import anthropic  # imported lazily so the self-test needs no live client

    client = anthropic.Anthropic()
    print(f"> {LIVE_PROMPT}")

    # Loose first: the strict side is the claim, so run the control before it.
    _run_side(client, "loose registry (no strict flag)", LOOSE_TOOLS, LOOSE_REGISTRY)
    _run_side(client, 'strict registry ("strict": true)', STRICT_TOOLS, STRICT_REGISTRY)

    print(
        "\nThe two registries sent identical input_schemas. Only the strict side "
        "sent a top-level \"strict\": true."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
