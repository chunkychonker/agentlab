"""MCP server wrapping the Hacker News Algolia Search API.

Intent: two tools, `search_stories` and `get_story`, backed by the live HN
Algolia API. This file is the thin imperative shell -- it owns
constructing the real `httpx.AsyncClient` and tool registration only.
`hn_client.py` owns the HTTP call and the API's confirmed error shapes;
`hn_parse.py` owns the pure response-to-summary mapping. See
../../research/2026-08-06-mcp-hn-search.md for the full spec, the API's
confirmed error shapes, and what was deliberately left out of scope
(a `get_user` tool, full nested comment trees, rate-limit/backoff logic).

Do not add a top-level `print()` here or inside a tool body: once
`mcp.run()` starts serving over stdio, stdout *is* the JSON-RPC wire (see
knowledge/mcp-python-sdk.md).
"""

from typing import Annotated, Literal

import httpx
from mcp.server import MCPServer
from pydantic import Field

import hn_client
import hn_parse

mcp = MCPServer("hn-search")

# Matches the SDK's own quickstart pattern (weather-server-python) of a
# bounded per-request timeout rather than an unbounded default.
REQUEST_TIMEOUT_SECONDS = 10.0


@mcp.tool()
async def search_stories(
    query: str,
    hits_per_page: Annotated[int, Field(ge=1, le=50)] = 5,
    sort: Literal["relevance", "date"] = "relevance",
) -> list[dict]:
    """Search Hacker News stories (not comments) by keyword.

    `sort="relevance"` uses the HN Algolia relevance ranking;
    `sort="date"` sorts newest first. Results are fixed to `tags=story` --
    a stated scope decision, not an omission: this tool searches stories,
    not raw comments.

    Failure modes: raises `hn_client.HNApiError` if the HN API returns a
    non-2xx response or the request times out/fails to connect -- the MCP
    SDK turns this into a non-raising, `is_error=True` tool result, not a
    raised exception on the caller. An empty match set is not a failure;
    it returns `[]`.
    """
    path = "/search" if sort == "relevance" else "/search_by_date"
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        raw = await hn_client.fetch(
            path,
            {"query": query, "tags": "story", "hitsPerPage": hits_per_page},
            client,
        )
    return hn_parse.summarize_hits(raw)


@mcp.tool()
async def get_story(story_id: Annotated[int, Field(ge=1)]) -> dict:
    """Fetch a single HN story by id, including up to 5 top-level comments.

    Failure modes: raises `hn_client.HNApiError(status=404, ...)` if
    `story_id` does not exist (confirmed live: the HN API returns a clean
    JSON 404 body for this case), or `HNApiError(status=None, ...)` if the
    request times out/fails to connect.
    """
    async with httpx.AsyncClient(timeout=REQUEST_TIMEOUT_SECONDS) as client:
        raw = await hn_client.fetch(f"/items/{story_id}", {}, client)
    return hn_parse.summarize_item(raw)


if __name__ == "__main__":
    # Defaults to stdio transport. Blocks, waiting on stdin -- silence here
    # is expected and correct (see README).
    mcp.run()
