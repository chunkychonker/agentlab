"""Docs-shaped assistant turns, as plain JSON dicts, for the offline checks.

Block shapes follow the example response in the tool search docs
(https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool,
read 2026-11-04). The ids and the tool input are adapted to this catalog.
These are recorded *shapes*, not captured live traffic.

Each function returns fresh dicts so a caller cannot corrupt another's copy.
"""

from __future__ import annotations

SERVER_ID = "srvtoolu_01ABC123"
CLIENT_ID = "toolu_01XYZ789"


def search_then_call() -> list[dict]:
    """Turn 1: one regex search finds get_weather, then a normal call to it."""
    return [
        {"type": "text", "text": "I'll search for a tool that can look up the weather."},
        {"type": "server_tool_use", "id": SERVER_ID, "name": "tool_search_tool_regex",
         "input": {"pattern": "weather"}},
        {"type": "tool_search_tool_result", "tool_use_id": SERVER_ID,
         "content": {"type": "tool_search_tool_search_result",
                     "tool_references": [{"type": "tool_reference", "tool_name": "get_weather"}]}},
        {"type": "text", "text": "I found a weather tool. Let me check Madison."},
        {"type": "tool_use", "id": CLIENT_ID, "name": "get_weather", "input": {"location": "Madison, WI"}},
    ]


def final_answer() -> list[dict]:
    """Turn 2: the model answers from the tool result."""
    return [{"type": "text", "text": "It is 41F and overcast in Madison."}]


def search_error(error_code: str = "invalid_tool_input") -> list[dict]:
    """A turn whose search failed (a 200 carrying an error block, not an HTTP error)."""
    return [
        {"type": "server_tool_use", "id": SERVER_ID, "name": "tool_search_tool_regex",
         "input": {"pattern": "("}},
        {"type": "tool_search_tool_result", "tool_use_id": SERVER_ID,
         "content": {"type": "tool_search_tool_result_error", "error_code": error_code}},
    ]


def empty_search_then_answer() -> list[dict]:
    """A search that matched nothing (empty reference list), then a plain answer."""
    return [
        {"type": "server_tool_use", "id": SERVER_ID, "name": "tool_search_tool_regex",
         "input": {"pattern": "teleport"}},
        {"type": "tool_search_tool_result", "tool_use_id": SERVER_ID,
         "content": {"type": "tool_search_tool_search_result", "tool_references": []}},
        {"type": "text", "text": "I have no tool for that."},
    ]
