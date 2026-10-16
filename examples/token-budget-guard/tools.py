"""The loop's one client tool: a safe arithmetic calculator, plus its schema.

Copied (not imported) from examples/minimal-agent-loop/agent.py so this example
stays self-contained; the guard is the subject here, not the tool.
"""

from __future__ import annotations

import ast
import operator

_BIN_OPS = {
    ast.Add: operator.add,
    ast.Sub: operator.sub,
    ast.Mult: operator.mul,
    ast.Div: operator.truediv,
    ast.FloorDiv: operator.floordiv,
    ast.Mod: operator.mod,
    ast.Pow: operator.pow,
}
_UNARY_OPS = {ast.UAdd: operator.pos, ast.USub: operator.neg}


def _eval_node(node: ast.AST) -> float | int:
    """Evaluate a whitelisted arithmetic AST node. Raises ValueError on anything else."""
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise ValueError(f"non-numeric constant: {node.value!r}")
        return node.value
    if isinstance(node, ast.BinOp) and type(node.op) in _BIN_OPS:
        return _BIN_OPS[type(node.op)](_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp) and type(node.op) in _UNARY_OPS:
        return _UNARY_OPS[type(node.op)](_eval_node(node.operand))
    raise ValueError(f"disallowed expression element: {type(node).__name__}")


def calculator(expression: str) -> str:
    """Evaluate arithmetic safely; return the result, or an ``"Error: ..."`` string.

    Errors go back to the model as tool_result text by design (the model can
    retry), so this never raises and never executes non-arithmetic input.
    """
    try:
        result = _eval_node(ast.parse(expression, mode="eval"))
    except Exception as exc:  # noqa: BLE001 - surfaced to the model as text
        return f"Error: could not evaluate {expression!r} ({exc})"
    if isinstance(result, float) and result.is_integer():
        result = int(result)
    return str(result)


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

TOOL_FUNCTIONS = {"calculator": lambda inp: calculator(inp["expression"])}
