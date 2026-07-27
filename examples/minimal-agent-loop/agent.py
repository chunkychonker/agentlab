"""Minimal single-tool agent loop, written by hand against the Anthropic Messages API.

No framework, no SDK Tool Runner: we define one tool (a safe calculator), let
Claude decide to call it, run it ourselves, feed the result back, and loop until
Claude stops asking for tools.

See the research note this came from:
    research/2026-07-27-minimal-agent-loop.md

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

# Model id lives in one constant so switching tiers is a one-line change.
# Cheapest current model; any current id works. See knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"


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
# The manual loop
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = (
    "You are a careful assistant. Use the calculator tool for any arithmetic "
    "rather than doing it in your head."
)


def run_agent(client, user_message: str, *, max_turns: int = 5, verbose: bool = False) -> str:
    """Run the hand-written tool-use loop and return Claude's final text answer.

    ``client`` only needs a ``.messages.create(...)`` method returning an object
    with ``.stop_reason`` and ``.content`` (a list of blocks). That is what the
    real Anthropic client provides, and what the test's fake client mimics.
    """
    messages = [{"role": "user", "content": user_message}]

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            # Done: concatenate every text block into the final answer.
            return "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )

        # Echo the assistant's tool_use turn back verbatim, then answer each
        # tool_use block with a matching tool_result block in a single user turn.
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            fn = TOOL_FUNCTIONS.get(block.name)
            if fn is None:
                result_text = f"Error: unknown tool {block.name!r}"
            else:
                result_text = fn(block.input)
            if verbose:
                print(f"  [tool] {block.name}({block.input}) -> {result_text}")
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,  # MUST match the tool_use id we answer.
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
    answer = run_agent(client, question, verbose=True)
    print(f"\nFinal answer: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
