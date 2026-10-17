"""Safe arithmetic evaluation: the pure core the calculator tool wraps.

Copied (not imported) from ``examples/minimal-agent-loop/agent.py`` so this
example stays self-contained: importing a sibling example's private
``_eval_node`` via ``sys.path`` would couple two examples by a private name.
One deliberate difference: the hand loop's ``calculator`` returns an
``"Error: ..."`` string, whereas ``calculate`` raises ``CalcError``. The SDK
tool wrapper in ``agent.py`` turns that typed failure into ``is_error: True``
instead of string-matching on a prefix.

No SDK import, no I/O.
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
_UNARY_OPS = {
    ast.UAdd: operator.pos,
    ast.USub: operator.neg,
}


class CalcError(ValueError):
    """The expression is malformed, uses a forbidden construct, or cannot be evaluated."""


def _eval_node(node: ast.AST) -> int | float:
    """Evaluate a whitelisted arithmetic AST node.

    Raises:
        CalcError: on any node outside numbers, + - * / // % **, unary +/-.
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body)
    if isinstance(node, ast.Constant):
        if isinstance(node.value, bool) or not isinstance(node.value, (int, float)):
            raise CalcError(f"non-numeric constant: {node.value!r}")
        return node.value
    if isinstance(node, ast.BinOp):
        op = _BIN_OPS.get(type(node.op))
        if op is None:
            raise CalcError(f"operator not allowed: {type(node.op).__name__}")
        return op(_eval_node(node.left), _eval_node(node.right))
    if isinstance(node, ast.UnaryOp):
        op = _UNARY_OPS.get(type(node.op))
        if op is None:
            raise CalcError(f"unary operator not allowed: {type(node.op).__name__}")
        return op(_eval_node(node.operand))
    raise CalcError(f"disallowed expression element: {type(node).__name__}")


def calculate(expression: str) -> int | float:
    """Evaluate a plain arithmetic expression without executing any code.

    Integral float results are returned as ``int`` (``6/3`` -> ``2``).

    Raises:
        CalcError: if the expression does not parse, contains anything other
            than numbers and the whitelisted operators, or fails arithmetically
            (e.g. division by zero, overflow). Never returns a sentinel.

    Known gap (shared with the hand loop): no bound on ``**``, so an input
    like ``9**9**9`` will burn CPU rather than fail.
    """
    try:
        tree = ast.parse(expression, mode="eval")
    except SyntaxError as exc:
        raise CalcError(f"could not parse {expression!r}: {exc.msg}") from exc
    try:
        result = _eval_node(tree)
    except (ZeroDivisionError, OverflowError) as exc:
        raise CalcError(f"could not evaluate {expression!r}: {exc}") from exc
    if isinstance(result, float) and result.is_integer():
        return int(result)
    return result
