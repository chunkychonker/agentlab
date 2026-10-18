"""Offline self-test for server.py and policy.py -- no subprocess, no network, no key.

Drives fresh servers through the SDK's in-memory `Client`. Convention matches
examples/mcp-hello-world/test_server.py: plain `test_*` async functions, each
printing "ok  <description>", collected and run from `main() -> int`.

Run: python3 test_server.py
"""

import asyncio

from mcp import Client
from mcp.server import MCPServer
from mcp.types import CallToolResult, Tool, ToolAnnotations

from policy import needs_confirmation
from server import SEED_NOTES, build_server, mcp


def _wire(annotations: ToolAnnotations | None) -> dict[str, object]:
    """Annotations as they appear on the JSON-RPC wire (camelCase, unset omitted)."""
    assert annotations is not None, "tool has no annotations"
    return annotations.model_dump(by_alias=True, exclude_none=True)


def _text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


async def _tools(server: MCPServer) -> dict[str, Tool]:
    async with Client(server) as client:
        return {t.name: t for t in (await client.list_tools()).tools}


async def test_lists_exactly_two_tools() -> None:
    tools = await _tools(mcp)
    assert set(tools) == {"read_note", "delete_note"}, sorted(tools)
    print("ok  list_tools() returns exactly read_note and delete_note")


async def test_read_note_annotations_on_the_wire() -> None:
    tools = await _tools(mcp)
    got = _wire(tools["read_note"].annotations)
    want = {"title": "Read a note", "readOnlyHint": True, "openWorldHint": False}
    assert got == want, got
    print(f"ok  read_note annotations on the wire: {got}")


async def test_delete_note_annotations_on_the_wire() -> None:
    tools = await _tools(mcp)
    got = _wire(tools["delete_note"].annotations)
    want = {
        "title": "Delete a note",
        "readOnlyHint": False,
        "destructiveHint": True,
        "idempotentHint": True,
        "openWorldHint": False,
    }
    assert got == want, got
    print(f"ok  delete_note annotations on the wire: {got}")


async def test_unset_hints_arrive_as_none_not_spec_defaults() -> None:
    a = (await _tools(mcp))["read_note"].annotations
    assert a is not None
    # Spec default for destructiveHint is True and idempotentHint is False,
    # but the SDK does not fill them in: the client sees None.
    assert a.destructive_hint is None and a.idempotent_hint is None, a
    print("ok  unset hints arrive as None (the SDK does not apply spec defaults)")


async def test_hints_are_not_enforcement() -> None:
    server = build_server(SEED_NOTES)
    async with Client(server) as client:
        before = await client.call_tool("read_note", {"note_id": "todo"})
        assert before.is_error is not True, _text(before)
        # destructiveHint=True, yet nothing stands between the client and the call.
        deleted = await client.call_tool("delete_note", {"note_id": "todo"})
        assert deleted.is_error is not True, _text(deleted)
        assert "deleted" in _text(deleted), _text(deleted)
        after = await client.call_tool("read_note", {"note_id": "todo"})
    assert after.is_error is True, "note still readable after delete"
    print("ok  destructive delete_note runs with no confirmation; the note is really gone")


async def test_delete_is_idempotent_as_declared() -> None:
    server = build_server(SEED_NOTES)
    async with Client(server) as client:
        first = await client.call_tool("delete_note", {"note_id": "groceries"})
        second = await client.call_tool("delete_note", {"note_id": "groceries"})
        other = await client.call_tool("read_note", {"note_id": "todo"})
    assert first.is_error is not True and second.is_error is not True
    assert "already absent" in _text(second), _text(second)
    assert other.is_error is not True and _text(other) == SEED_NOTES["todo"], _text(other)
    print("ok  second delete of the same id is not an error and changes nothing else")


async def test_read_unknown_note_is_tool_error() -> None:
    async with Client(build_server(SEED_NOTES)) as client:
        result = await client.call_tool("read_note", {"note_id": "nope"})
    assert result.is_error is True, _text(result)
    assert "nope" in _text(result), _text(result)
    print("ok  read_note on an unknown id -> is_error=True, no raise")


async def test_servers_do_not_share_state() -> None:
    async with Client(build_server(SEED_NOTES)) as client:
        await client.call_tool("delete_note", {"note_id": "todo"})
    async with Client(build_server(SEED_NOTES)) as client:
        result = await client.call_tool("read_note", {"note_id": "todo"})
    assert result.is_error is not True, _text(result)
    assert dict(SEED_NOTES)["todo"] == "finish the annotations example"
    print("ok  each build_server() owns its store; SEED_NOTES is never mutated")


async def test_unannotated_tool_has_no_annotations() -> None:
    bare = MCPServer("bare")

    @bare.tool()
    def echo(text: str) -> str:
        """Return `text` unchanged."""
        return text

    tools = await _tools(bare)
    assert tools["echo"].annotations is None, tools["echo"].annotations
    assert needs_confirmation(tools["echo"].annotations) is True
    print("ok  tool with no annotations= -> annotations is None -> treated as destructive")


async def test_policy_table() -> None:
    cases: list[tuple[str, ToolAnnotations | None, bool]] = [
        ("no annotations", None, True),
        ("read-only", ToolAnnotations(read_only_hint=True), False),
        ("read-only beats destructive", ToolAnnotations(read_only_hint=True, destructive_hint=True), False),
        ("destructive", ToolAnnotations(read_only_hint=False, destructive_hint=True), True),
        ("additive writer", ToolAnnotations(read_only_hint=False, destructive_hint=False), False),
        ("writer, destructive unset", ToolAnnotations(read_only_hint=False), True),
        ("title only", ToolAnnotations(title="x"), True),
    ]
    for label, annotations, want in cases:
        got = needs_confirmation(annotations)
        assert got is want, f"{label}: needs_confirmation -> {got}, want {want}"
    tools = await _tools(mcp)
    assert needs_confirmation(tools["read_note"].annotations) is False
    assert needs_confirmation(tools["delete_note"].annotations) is True
    print(f"ok  needs_confirmation matches spec defaults on {len(cases)} cases + both served tools")


async def _run_all() -> int:
    tests = [
        test_lists_exactly_two_tools,
        test_read_note_annotations_on_the_wire,
        test_delete_note_annotations_on_the_wire,
        test_unset_hints_arrive_as_none_not_spec_defaults,
        test_hints_are_not_enforcement,
        test_delete_is_idempotent_as_declared,
        test_read_unknown_note_is_tool_error,
        test_servers_do_not_share_state,
        test_unannotated_tool_has_no_annotations,
        test_policy_table,
    ]
    for test in tests:
        await test()
    return len(tests)


def main() -> int:
    count = asyncio.run(_run_all())
    print(f"\nAll {count} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
