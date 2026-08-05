"""Offline self-test for server.py -- no subprocess, no live host, no network.

Drives `server.mcp` through the SDK's own in-memory `Client`, which connects
directly to the server object (no stdio, no port). Convention matches
examples/skill-anatomy/test_validate_skill.py: plain `test_*` async
functions, each printing "ok  <description>" on success, collected and run
from `main() -> int`.

Run: python3 test_server.py
"""

import asyncio

from mcp import Client

from server import mcp


async def test_lists_exactly_one_tool() -> None:
    async with Client(mcp) as client:
        tools = (await client.list_tools()).tools
    assert len(tools) == 1, f"expected exactly 1 tool, got {len(tools)}"
    assert tools[0].name == "count_words", f"unexpected tool name: {tools[0].name!r}"
    print("ok  list_tools() returns exactly one tool named count_words")


async def test_normal_text_returns_correct_count() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("count_words", {"text": "the quick brown fox"})
    assert result.is_error is not True, f"unexpected tool error: {result.content}"
    assert result.structured_content == {"result": 4}, result.structured_content
    print("ok  normal text 'the quick brown fox' -> 4")


async def test_empty_string_returns_zero() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("count_words", {"text": ""})
    assert result.is_error is not True, f"unexpected tool error: {result.content}"
    assert result.structured_content == {"result": 0}, result.structured_content
    print("ok  empty string -> 0, not an error (0 is a valid count)")


async def test_over_length_text_is_tool_error_not_exception() -> None:
    too_long = "a " * 5_001  # 10,001 chars, one over the Field(max_length=10_000) limit
    async with Client(mcp) as client:
        result = await client.call_tool("count_words", {"text": too_long})
    assert result.is_error is True, "expected is_error=True, tool call did not raise but also didn't error"
    assert result.structured_content is None, result.structured_content
    message = "".join(getattr(block, "text", "") for block in result.content)
    assert "10000" in message or "10,000" in message, (
        f"expected the 10,000-char limit referenced in the error message, got: {message!r}"
    )
    print("ok  over-length text (10,001 chars) -> is_error=True, message references the limit, no raise")


async def test_missing_argument_is_tool_error_not_exception() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("count_words", {})
    assert result.is_error is True, "expected is_error=True for missing required argument"
    assert result.structured_content is None, result.structured_content
    print("ok  missing required argument 'text' -> is_error=True, no raise")


async def _run_all() -> None:
    await test_lists_exactly_one_tool()
    await test_normal_text_returns_correct_count()
    await test_empty_string_returns_zero()
    await test_over_length_text_is_tool_error_not_exception()
    await test_missing_argument_is_tool_error_not_exception()


def main() -> int:
    asyncio.run(_run_all())
    print("\nAll 5 self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
