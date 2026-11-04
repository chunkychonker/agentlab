"""Build and check the ``tools`` array for a request that uses server-side tool search.

Pure: no I/O, no SDK. ``build_tools`` produces a valid array by construction;
``validate_tools`` is the boundary check for arrays assembled some other way,
and rejects the toolsets the API documents as a 400.

See research/2026-11-04-tool-search-deferred-tools.md.
"""

from __future__ import annotations

from collections import Counter
from enum import Enum


class SearchVariant(Enum):
    """The two server search tools. Value = (versioned type, fixed tool name)."""

    REGEX = ("tool_search_tool_regex_20251119", "tool_search_tool_regex")
    BM25 = ("tool_search_tool_bm25_20251119", "tool_search_tool_bm25")

    @property
    def tool_type(self) -> str:
        return self.value[0]

    @property
    def tool_name(self) -> str:
        return self.value[1]


_SEARCH_TYPES = frozenset(variant.tool_type for variant in SearchVariant)
DEFER_KEY = "defer_loading"


class ToolsetError(ValueError):
    """The tools array is one the API would reject with a 400."""


def search_tool(variant: SearchVariant) -> dict:
    """The server search tool definition for ``variant``. Never deferred."""
    return {"type": variant.tool_type, "name": variant.tool_name}


def build_tools(catalog: list[dict], hot: frozenset[str], variant: SearchVariant) -> list[dict]:
    """The full tools array: search tool first, ``hot`` tools plain, the rest deferred.

    Catalog order is preserved after the search tool. Input dicts are not
    mutated. Every catalog tool appears exactly once: deferral only hides a
    definition from the model's context, it is still sent on every request.

    Raises:
        ToolsetError: if a name in ``hot`` is not in ``catalog`` (a typo would
            silently defer a tool the caller meant to keep loaded), or if the
            result fails ``validate_tools`` (e.g. duplicate catalog names).
    """
    names = {tool["name"] for tool in catalog}
    unknown = sorted(hot - names)
    if unknown:
        raise ToolsetError(f"hot tools not in catalog: {unknown}")
    tools = [search_tool(variant)]
    for tool in catalog:
        tools.append(dict(tool) if tool["name"] in hot else {**tool, DEFER_KEY: True})
    validate_tools(tools)
    return tools


def validate_tools(tools: list[dict]) -> None:
    """Raise ``ToolsetError`` if the API would reject ``tools``; return None if not.

    Checks, in this order (the first failure wins):
      1. a server search tool carries ``defer_loading: true``;
      2. every tool is deferred (the API needs at least one loaded tool);
      3. a tool carries both ``defer_loading`` and ``cache_control``;
      4. two tools share a name.

    Rule 3 is applied to any tool that carries the ``defer_loading`` key, even
    with value false. That is stricter than the docs need (they describe the
    deferred case) and is an assumption of this module.

    Raises:
        ToolsetError: with a message naming the rule and the offending tool(s).
    """
    for tool in tools:
        if tool.get("type") in _SEARCH_TYPES and tool.get(DEFER_KEY) is True:
            raise ToolsetError(f"search tool {tool['name']!r} must not be deferred")
    if tools and all(tool.get(DEFER_KEY) is True for tool in tools):
        raise ToolsetError("all tools are deferred; at least one must have defer_loading=false")
    for tool in tools:
        if DEFER_KEY in tool and "cache_control" in tool:
            raise ToolsetError(
                f"tool {tool['name']!r} sets both defer_loading and cache_control; "
                "put the cache breakpoint on a non-deferred tool"
            )
    duplicates = sorted(name for name, count in Counter(t["name"] for t in tools).items() if count > 1)
    if duplicates:
        raise ToolsetError(f"duplicate tool names: {duplicates}")
