"""Read tool-search blocks out of an assistant turn and build the reply turn.

Pure: no I/O, no SDK. ``content`` is always a list of plain dicts (the shell
converts SDK objects to dicts once, at the boundary).

A tool-search turn mixes two kinds of tool call:
  - ``server_tool_use`` (id ``srvtoolu_...``): the server already ran it and
    put the answer in a ``tool_search_tool_result`` block. The client must
    NOT send a ``tool_result`` for it (the API returns a 400).
  - ``tool_use`` (id ``toolu_...``): a normal client tool. The client runs it
    and answers with a ``tool_result``.
The block ``type`` is the discriminator here, not the id prefix.

See research/2026-11-04-tool-search-deferred-tools.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

SEARCH_RESULT_BLOCK = "tool_search_tool_result"
SEARCH_HITS = "tool_search_tool_search_result"
SEARCH_ERROR = "tool_search_tool_result_error"
CLIENT_TOOL_USE = "tool_use"


@dataclass(frozen=True)
class SearchFailure:
    """One failed server search: which call, the documented error_code, and any message."""

    tool_use_id: str
    error_code: str
    message: str | None


def discovered_tools(content: list[dict]) -> list[str]:
    """Tool names returned by successful searches in this turn, in order, repeats kept."""
    names = []
    for block in content:
        if block.get("type") == SEARCH_RESULT_BLOCK and block["content"]["type"] == SEARCH_HITS:
            names.extend(ref["tool_name"] for ref in block["content"]["tool_references"])
    return names


def search_failures(content: list[dict]) -> list[SearchFailure]:
    """Every search in this turn that came back as ``tool_search_tool_result_error``.

    An empty match is NOT a failure: it is a hit list with no references.
    """
    return [
        SearchFailure(block["tool_use_id"], block["content"]["error_code"], block["content"].get("error_message"))
        for block in content
        if block.get("type") == SEARCH_RESULT_BLOCK and block["content"]["type"] == SEARCH_ERROR
    ]


def pending_client_tool_uses(content: list[dict]) -> list[dict]:
    """The ``tool_use`` blocks the client must run. Never includes ``server_tool_use``."""
    return [block for block in content if block.get("type") == CLIENT_TOOL_USE]


def followup_user_turn(content: list[dict], outputs: Mapping[str, str]) -> dict:
    """The user message answering this turn's client tool calls, in call order.

    ``outputs`` maps each pending ``tool_use`` id to its result text. Requiring
    the key sets to match exactly means a reply that answers a server search id
    (a 400) or leaves a client call unanswered (also a 400) cannot be built.

    Raises:
        ValueError: if ``outputs`` is missing an id from
            ``pending_client_tool_uses(content)``, has an id not in it (for
            example a ``srvtoolu_`` id), or there are no pending calls at all.
            Nothing is built in that case.
    """
    pending = [block["id"] for block in pending_client_tool_uses(content)]
    if not pending:
        raise ValueError("no client tool_use blocks to answer")
    missing = sorted(set(pending) - set(outputs))
    extra = sorted(set(outputs) - set(pending))
    if missing or extra:
        raise ValueError(f"outputs must answer exactly the client tool_use ids; missing={missing} extra={extra}")
    return {
        "role": "user",
        "content": [{"type": "tool_result", "tool_use_id": tid, "content": outputs[tid]} for tid in pending],
    }
