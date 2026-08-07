"""Offline self-test for server.py -- proves tool registration and schema
only, via the SDK's in-memory Client. Deliberately does NOT call the
tools: their bodies perform real outbound HTTP to the live HN API, so
exercising them here would make this self-test network-dependent and
flaky. Correctness of the HTTP-handling logic (the 404-JSON, 500-HTML, and
timeout gotchas) is proven instead by test_hn_client.py's injected-mock
coverage -- a stated design boundary, not an oversight.

Run: python3 test_server.py
"""

import asyncio

from mcp import Client

from server import mcp


async def test_lists_exactly_two_tools() -> None:
    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools
    names = sorted(tool.name for tool in tools)
    assert names == ["get_story", "search_stories"], names
    print("ok  list_tools() returns exactly search_stories and get_story")


async def test_search_stories_schema() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    schema = tools["search_stories"].input_schema
    assert schema["required"] == ["query"], schema
    assert "hits_per_page" in schema["properties"], schema
    assert "sort" in schema["properties"], schema
    print("ok  search_stories schema: query required, hits_per_page/sort optional")


async def test_get_story_schema() -> None:
    async with Client(mcp) as client:
        tools = {tool.name: tool for tool in (await client.list_tools()).tools}
    schema = tools["get_story"].input_schema
    assert schema["required"] == ["story_id"], schema
    print("ok  get_story schema: story_id required")


async def _run_all() -> None:
    await test_lists_exactly_two_tools()
    await test_search_stories_schema()
    await test_get_story_schema()


def main() -> int:
    asyncio.run(_run_all())
    print("\nAll 3 self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
