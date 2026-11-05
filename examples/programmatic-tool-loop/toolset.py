"""Check a ``tools`` array against the documented programmatic-calling constraints.

Pure. Runs once, before the first request, so a toolset the API would reject
with a 400 (or one the loop could not serve) fails fast and locally.
"""

from __future__ import annotations

from collections.abc import Sequence

CODE_EXECUTION_TOOL_TYPES = frozenset({"code_execution_20260120", "code_execution_20260521"})
_CUSTOM = "custom"
_DIRECT = "direct"


class ToolsetError(ValueError):
    """The tools array cannot be used for programmatic tool calling with these handlers."""


def validate_tools(tools: Sequence[dict], handler_names: frozenset[str]) -> None:
    """Return None if ``tools`` is usable. Otherwise raise ``ToolsetError`` naming the rule.

    Rules:
      - exactly one code-execution tool (type in ``CODE_EXECUTION_TOOL_TYPES``),
        because programmatic calls cannot resume without it
      - the custom tool names equal ``handler_names`` exactly, so every tool the
        model could call has a handler and there is no handler without a tool
      - no custom tool combines ``strict: true`` with a non-direct
        ``allowed_callers`` entry, which the docs list as incompatible
    """
    code_tools = [tool for tool in tools if tool.get("type") in CODE_EXECUTION_TOOL_TYPES]
    if len(code_tools) != 1:
        raise ToolsetError(f"need exactly one code execution tool ({sorted(CODE_EXECUTION_TOOL_TYPES)}), "
                           f"found {len(code_tools)}")

    custom = [tool for tool in tools if tool.get("type", _CUSTOM) == _CUSTOM]
    names = {tool["name"] for tool in custom}
    if names != handler_names:
        raise ToolsetError(f"custom tools and handlers differ: tools without handler "
                           f"{sorted(names - handler_names)}, handlers without tool {sorted(handler_names - names)}")

    for tool in custom:
        callers = set(tool.get("allowed_callers", [_DIRECT]))
        if tool.get("strict") is True and callers - {_DIRECT}:
            raise ToolsetError(f"tool {tool['name']!r}: strict: true is not supported with programmatic callers")
