"""A local reference model of the server's regex tool search.

This is a *model* of the documented behaviour, not the server: it lets the
tests pin what a pattern should find, and lets you check a catalog's
descriptions are searchable before spending a request. The server's ranking
is undocumented; this model returns matches in catalog order.

Pure: no I/O, no SDK. See research/2026-11-04-tool-search-deferred-tools.md.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

MAX_PATTERN_CHARS = 200  # documented limit for tool_search_tool_regex
DEFAULT_LIMIT = 5        # documented default result count
MAX_LIMIT = 10_000       # documented upper bound for `limit`
INVALID_TOOL_INPUT = "invalid_tool_input"  # the documented error_code for a bad pattern


@dataclass(frozen=True)
class SearchError:
    """What the server reports as ``tool_search_tool_result_error``."""

    error_code: str
    message: str


def _searchable_fields(tool: dict) -> list[str]:
    """The fields the docs say are searched: name, description, arg names, arg descriptions."""
    fields = [tool["name"], tool.get("description", "")]
    for arg, spec in tool.get("input_schema", {}).get("properties", {}).items():
        fields.append(arg)
        fields.append(spec.get("description", ""))
    return fields


def search(catalog: list[dict], pattern: str, limit: int = DEFAULT_LIMIT) -> list[str] | SearchError:
    """Names of up to ``limit`` tools with a field matching ``pattern``, in catalog order.

    Matching is ``re.search(pattern, field, re.IGNORECASE)`` on each field
    separately, so a pattern cannot match across two fields.

    Returns:
        A list of tool names; ``[]`` when nothing matches (not an error, as on
        the server). A ``SearchError`` with ``error_code="invalid_tool_input"``
        when the pattern is over 200 characters or is not a valid regex:
        returned, not raised, because the server returns it as data in a 200.

    Raises:
        ValueError: if ``limit`` is outside [1, 10000]. The server's behaviour
            for that case is undocumented, so this model treats it as a caller
            bug rather than guessing an error_code.
    """
    if not 1 <= limit <= MAX_LIMIT:
        raise ValueError(f"limit must be in [1, {MAX_LIMIT}], got {limit}")
    if len(pattern) > MAX_PATTERN_CHARS:
        return SearchError(INVALID_TOOL_INPUT, f"pattern is {len(pattern)} chars; max is {MAX_PATTERN_CHARS}")
    try:
        compiled = re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        return SearchError(INVALID_TOOL_INPUT, f"invalid regex {pattern!r}: {exc}")
    hits = [tool["name"] for tool in catalog if any(compiled.search(f) for f in _searchable_fields(tool))]
    return hits[:limit]
