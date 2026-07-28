"""Multi-tool agent driven by the SDK's typed tool registry (`@beta_tool` +
`client.beta.messages.tool_runner`), instead of the hand-rolled loop in
`examples/minimal-agent-loop/`.

Each tool is a plain typed Python function. `@beta_tool` derives its JSON
Schema from the function's type hints and Google-style docstring, and
validates model-supplied input with Pydantic before the tool body ever runs.
`TOOL_REGISTRY` is still just a `{name: tool}` dict - the SDK doesn't force a
heavier abstraction than that.

See the research note this came from:
    research/2026-07-28-typed-tool-registry.md

Run it live (needs a key):
    export ANTHROPIC_API_KEY=sk-ant-...
    python agent.py

Run the offline self-test (no key, no network):
    python test_agent.py
"""

from __future__ import annotations

import os

from anthropic import beta_tool

# Model id lives in one constant so switching tiers is a one-line change.
# Cheapest current model; any current id works. See knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"


# --------------------------------------------------------------------------- #
# The tools - plain typed functions, schema generated from the type hints.
# --------------------------------------------------------------------------- #


@beta_tool
def word_count(text: str) -> str:
    """Count the words in a string.

    Args:
        text: The text to count words in.
    """
    return str(len(text.split()))


@beta_tool
def reverse_text(text: str) -> str:
    """Reverse a string character by character.

    Args:
        text: The text to reverse.
    """
    return text[::-1]


@beta_tool
def is_prime(n: int) -> str:
    """Determine whether an integer is prime.

    Args:
        n: The integer to test.
    """
    if n < 2:
        return "false"
    if n in (2, 3):
        return "true"
    if n % 2 == 0:
        return "false"
    i = 3
    while i * i <= n:
        if n % i == 0:
            return "false"
        i += 2
    return "true"


# List used to hand tools to the runner, plus an explicit name -> tool dict.
# A plain dict is the whole "registry" - no new abstraction beyond it.
TOOLS = [word_count, reverse_text, is_prime]
TOOL_REGISTRY = {t.name: t for t in TOOLS}


# --------------------------------------------------------------------------- #
# The SDK-driven loop
# --------------------------------------------------------------------------- #


def run_agent(client, user_message: str, *, max_iterations: int = 8) -> str:
    """Run the multi-tool agent via `client.beta.messages.tool_runner` and
    return Claude's final text answer.

    `max_iterations` is always passed explicitly: the runner's own default is
    `None` (unbounded), which is the same runaway-loop failure mode the
    hand-rolled loop's `max_turns` guards against.
    """
    runner = client.beta.messages.tool_runner(
        model=MODEL,
        max_tokens=1024,
        tools=TOOLS,
        messages=[{"role": "user", "content": user_message}],
        max_iterations=max_iterations,
    )
    final_message = runner.until_done()
    return "".join(
        block.text for block in final_message.content if getattr(block, "type", None) == "text"
    )


# --------------------------------------------------------------------------- #
# Live entry point
# --------------------------------------------------------------------------- #


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set - skipping the live run.\n"
            "Set it and re-run for a real call, or run 'python test_agent.py' "
            "for the offline self-test."
        )
        return 0

    import anthropic  # imported lazily so the self-test needs no live client

    client = anthropic.Anthropic()
    question = (
        "Is 91 prime? Also reverse the word 'agent' and count the words in "
        "'the quick brown fox'."
    )
    print(f"> {question}")
    answer = run_agent(client, question)
    print(f"\nFinal answer: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
