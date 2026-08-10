"""Offline self-test for server.py -- no subprocess, no live host, no network.

Drives `server.mcp` through the SDK's own in-memory `Client`, matching the
convention in examples/mcp-hello-world/test_server.py: plain `test_*` async
functions, each printing "ok  <description>" on success, collected and run
from `main() -> int`.

Each test asserts one of the acceptance criteria from the research note's
build proposal (research/2026-08-09-mcp-resources-vs-tools.md), not an
implementation detail of server.py.

Run: python3 test_server.py
"""

import asyncio
import json

from mcp import Client, MCPError

import server
from server import mcp


async def test_list_resources_returns_only_the_static_resource() -> None:
    async with Client(mcp) as client:
        resources = (await client.list_resources()).resources
    assert len(resources) == 1, f"expected exactly 1 resource, got {len(resources)}"
    assert resources[0].uri == "notes://index", f"unexpected resource uri: {resources[0].uri!r}"
    print("ok  list_resources() returns exactly notes://index (the template is not in this list)")


async def test_list_resource_templates_returns_only_the_template() -> None:
    async with Client(mcp) as client:
        templates = (await client.list_resource_templates()).resource_templates
    assert len(templates) == 1, f"expected exactly 1 template, got {len(templates)}"
    assert templates[0].uri_template == "notes://{note_id}", (
        f"unexpected template uri: {templates[0].uri_template!r}"
    )
    print("ok  list_resource_templates() returns exactly notes://{note_id}")


async def test_listing_resources_does_not_execute_the_handler_body() -> None:
    before = server.list_notes_index_calls
    async with Client(mcp) as client:
        await client.list_resources()
    after = server.list_notes_index_calls
    assert after == before, (
        f"list_resources() must not call list_notes_index's body: count went {before} -> {after}"
    )
    print("ok  list_resources() does not invoke list_notes_index's body (call counter unchanged)")


async def test_reading_the_index_resource_does_execute_the_handler_body() -> None:
    before = server.list_notes_index_calls
    async with Client(mcp) as client:
        result = await client.read_resource("notes://index")
    after = server.list_notes_index_calls
    assert after == before + 1, f"read_resource('notes://index') should call the body once: {before} -> {after}"
    payload = json.loads(result.contents[0].text)
    assert any(note["id"] == "1" for note in payload), f"seeded note missing from index: {payload!r}"
    print("ok  read_resource('notes://index') executes the handler (counter +1) and returns valid JSON")


async def test_reading_unknown_note_raises_mcp_error_invalid_params() -> None:
    async with Client(mcp) as client:
        try:
            await client.read_resource("notes://does-not-exist")
        except MCPError as exc:
            assert exc.code == -32602, f"expected code -32602 (invalid params), got {exc.code}"
            print("ok  read_resource() of an unknown note_id raises MCPError(code=-32602)")
            return
    raise AssertionError("expected MCPError to be raised for an unknown note_id, nothing was raised")


async def test_create_note_side_effect_is_visible_through_the_resource_path() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("create_note", {"title": "Groceries", "body": "milk, eggs"})
        assert result.is_error is not True, f"unexpected tool error: {result.content}"
        new_id = result.structured_content["result"]

        read_back = await client.read_resource(f"notes://{new_id}")
    payload = json.loads(read_back.contents[0].text)
    assert payload == {"id": new_id, "title": "Groceries", "body": "milk, eggs"}, payload
    print("ok  create_note()'s side effect is visible immediately through notes://{note_id}")


async def test_create_note_with_empty_title_is_tool_error_not_exception() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("create_note", {"title": "", "body": "y"})
    assert result.is_error is True, "expected is_error=True for an empty (min_length=1) title"
    assert result.structured_content is None, result.structured_content
    print(
        "ok  create_note() with an empty title -> is_error=True, no raise "
        "(the direct contrast with the resource-not-found case above)"
    )


async def _run_all() -> None:
    await test_list_resources_returns_only_the_static_resource()
    await test_list_resource_templates_returns_only_the_template()
    await test_listing_resources_does_not_execute_the_handler_body()
    await test_reading_the_index_resource_does_execute_the_handler_body()
    await test_reading_unknown_note_raises_mcp_error_invalid_params()
    await test_create_note_side_effect_is_visible_through_the_resource_path()
    await test_create_note_with_empty_title_is_tool_error_not_exception()


def main() -> int:
    asyncio.run(_run_all())
    print("\nAll 7 self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
