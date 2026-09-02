"""The minimal single-tool agent loop, driven by `messages.stream()` instead of
`messages.create()`.

Same mechanics as `examples/minimal-agent-loop/` (define one tool, let Claude
ask for it, run it, feed the result back, loop) — but each turn now arrives as a
sequence of SSE events, and a `tool_use` block's `input` shows up as
`input_json_delta` string fragments that must be accumulated and parsed before
the tool can be called at all. That accumulation lives in `accumulator.py`, kept
pure so it can be proven correct offline; this file is the imperative shell.

The loop runs with extended thinking enabled, which is the reason the
accumulator has to understand `thinking` blocks at all: the assistant turn is
echoed back verbatim with the tool result, and a thinking block that is dropped
or edited on the way is a 400. Nothing in the dispatch path needed to change —
`message.content` already goes back whole.

The calculator tool is duplicated from `minimal-agent-loop` on purpose: every
example here is self-contained, and nothing in this repo imports across examples.

See the research notes this came from:
    research/2026-08-16-streaming-tool-loop.md
    research/2026-09-02-streaming-thinking-accumulator.md

Run it live (needs a key):
    export ANTHROPIC_API_KEY=sk-ant-...
    python agent.py

Run the offline self-test (no key, no network):
    python test_agent.py
"""

from __future__ import annotations

import ast
import operator
import os

from accumulator import (
    CONTENT_BLOCK_DELTA,
    CONTENT_BLOCK_START,
    CONTENT_BLOCK_STOP,
    TEXT_DELTA,
    THINKING_BLOCK,
    THINKING_DELTA,
    TOOL_USE_BLOCK,
    accumulate,
)

# Model id lives in one constant so switching tiers is a one-line change.
# Cheapest current model; any current id works. See knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"

# Extended thinking, manual mode. THINKING is tied to MODEL being a 4.5-era
# model: {"type": "enabled", "budget_tokens": N} is deprecated on the 4.6 models
# and rejected with a 400 on 4.7 and later, which require {"type": "adaptive"}
# instead. budget_tokens must be >= 1024 and strictly < max_tokens, which is why
# MAX_TOKENS is well above it. See knowledge/thinking-blocks.md.
THINKING_BUDGET_TOKENS = 1024
MAX_TOKENS = 4096
THINKING = {"type": "enabled", "budget_tokens": THINKING_BUDGET_TOKENS}


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #

# Allowed binary operators for the safe arithmetic evaluator.
_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


def _eval_node(node: ast.AST) -> float | int:
    """Recursively evaluate a whitelisted arithmetic AST node.

    Only numeric constants, the operators in _BIN_OPS/_UNARY_OPS, and parentheses
    are permitted. Anything else (names, calls, attributes, ...) raises ValueError.
    This is deliberately NOT ``eval`` — no attribute access, no function calls,
    so ``__import__('os')`` and friends never execute.
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"non-numeric constant: {node.value!r}")
        return node.value
    if isinstance(node, ast.BinOp):
        op_type = type(node.op)
        if op_type not in _BIN_OPS:
            raise ValueError(f"operator not allowed: {op_type.__name__}")
        return _BIN_OPS[op_type](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op_type = type(node.op)
        if op_type not in _UNARY_OPS:
            raise ValueError(f"unary operator not allowed: {op_type.__name__}")
        return _UNARY_OPS[op_type](_eval_node(node.operand))
    raise ValueError(f"disallowed expression element: {type(node).__name__}")


def calculator(expression: str) -> str:
    """Evaluate a plain arithmetic expression safely and return the result as a string.

    Supports + - * / // % ** and parentheses over numbers. On any bad or
    forbidden input, returns an ``"Error: ..."`` string instead of raising or
    executing anything — so the model gets a usable tool_result either way.
    """
    try:
        tree = ast.parse(expression, mode="eval")
        result = _eval_node(tree)
    except Exception as exc:  # noqa: BLE001 - surface any failure to the model as text
        return f"Error: could not evaluate {expression!r} ({exc})"
    # Render integers without a trailing ".0" for clean, checkable output.
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(result)


# Tool schema exactly as the Messages API expects: name, description, input_schema.
TOOLS = [
    {
        "name": "calculator",
        "description": (
            "Evaluate an arithmetic expression and return the numeric result. "
            "Supports + - * / // % ** and parentheses over numbers. Use this for "
            "any arithmetic instead of computing it yourself."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "expression": {
                    "type": "string",
                    "description": "The arithmetic expression, e.g. '4839 * 1284'.",
                }
            },
            "required": ["expression"],
        },
    }
]

# name -> callable registry. One tool today; a plain dict is all the abstraction
# this increment needs.
TOOL_FUNCTIONS = {
    "calculator": lambda inp: calculator(inp["expression"]),
}


# --------------------------------------------------------------------------- #
# The streaming loop
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = (
    "You are a careful assistant. Use the calculator tool for any arithmetic "
    "rather than doing it in your head."
)


def _echo_deltas(events):
    """Yield every event unchanged, printing text and thinking fragments as they arrive.

    This is where the "streaming" is actually visible to a human. It is a
    side-effecting pass-through wrapped *around* the event iterator, which is why
    ``accumulate()`` itself can stay pure — the printing lives in the shell.

    Reasoning is labelled, since ``thinking_delta`` and ``text_delta`` fragments
    otherwise arrive as one undifferentiated run of prose: a thinking block's
    start prints a ``[thinking]`` marker and its stop a newline. Only indices
    opened as thinking blocks are tracked, so a text block's stop prints nothing.
    """
    thinking_indices: set[int] = set()

    for event in events:
        event_type = getattr(event, "type", None)

        if event_type == CONTENT_BLOCK_START:
            if getattr(event.content_block, "type", None) == THINKING_BLOCK:
                thinking_indices.add(event.index)
                print("[thinking] ", end="", flush=True)

        elif event_type == CONTENT_BLOCK_DELTA:
            delta_type = getattr(event.delta, "type", None)
            if delta_type == TEXT_DELTA:
                print(event.delta.text, end="", flush=True)
            elif delta_type == THINKING_DELTA:
                print(event.delta.thinking, end="", flush=True)

        elif event_type == CONTENT_BLOCK_STOP:
            if event.index in thinking_indices:
                thinking_indices.discard(event.index)
                print(flush=True)

        yield event


def run_agent_streaming(
    client, user_message: str, *, max_turns: int = 5, verbose: bool = False
) -> str:
    """Run the hand-written tool-use loop over streamed turns and return the final text.

    Same contract as ``minimal_agent_loop.agent.run_agent``: ``client`` only
    needs ``.messages.stream(...)``, returning a context manager whose entered
    value iterates raw stream events. That is what the real Anthropic client
    provides, and what the test's fake client mimics.

    Failure modes: ``RuntimeError`` if the loop is still asking for tools after
    ``max_turns`` turns; whatever ``accumulate()`` raises on a malformed event
    sequence (``ValueError`` / ``json.JSONDecodeError``) propagates unchanged —
    a half-decoded turn must not be dispatched as if it were complete.
    """
    messages = [{"role": "user", "content": user_message}]

    for _ in range(max_turns):
        with client.messages.stream(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            system=SYSTEM_PROMPT,
            thinking=THINKING,
            tools=TOOLS,
            messages=messages,
        ) as stream:
            events = _echo_deltas(stream) if verbose else stream
            # One turn's worth of events in, one assembled message out. The tool
            # is dispatched only after the block it belongs to has closed.
            message = accumulate(events)
        if verbose:
            print()

        if message.stop_reason != "tool_use":
            # Done: concatenate every text block into the final answer.
            return "".join(
                block["text"] for block in message.content if block["type"] == "text"
            )

        # Echo the assistant's tool_use turn back verbatim, then answer each
        # tool_use block with a matching tool_result block in a single user turn.
        # The accumulator's blocks are already plain dicts in the API's wire
        # shape, so they can go straight back into `messages`.
        #
        # Verbatim is load-bearing with thinking on: `thinking` and
        # `redacted_thinking` blocks must go back complete, unmodified and in
        # order, or the next request is a 400. Appending `message.content` whole
        # (rather than filtering to the blocks this loop cares about) is what
        # makes that true for free.
        messages.append({"role": "assistant", "content": message.content})

        tool_results = []
        for block in message.content:
            if block["type"] != TOOL_USE_BLOCK:
                continue
            fn = TOOL_FUNCTIONS.get(block["name"])
            if fn is None:
                result_text = f"Error: unknown tool {block['name']!r}"
            else:
                result_text = fn(block["input"])
            if verbose:
                print(f"  [tool] {block['name']}({block['input']}) -> {result_text}")
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block["id"],  # MUST match the tool_use id we answer.
                    "content": result_text,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agent did not finish within {max_turns} turns")


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

    import anthropic  # imported lazily so the self-test needs no dependency

    client = anthropic.Anthropic()
    question = "What is 4839 * 1284, and is that more than five million?"
    print(f"> {question}")
    answer = run_agent_streaming(client, question, verbose=True)
    print(f"\nFinal answer: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
