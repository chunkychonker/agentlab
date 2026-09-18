"""Offline self-test for server.py -- no subprocess, no live host, no network.

Drives `server.mcp` through the SDK's own in-memory `Client`, matching the
convention in examples/mcp-hello-world/test_server.py and
examples/mcp-resources-vs-tools/test_server.py: plain `test_*` async
functions, each printing "ok  <description>" on success, collected and run
from `main() -> int`.

Each test asserts one of the acceptance criteria from the research note's
build proposal (research/2026-09-18-mcp-prompts.md), not an implementation
detail of server.py.

The three error cases each make the SDK log a server-side traceback to
*stderr* ("Missing required arguments: {'code'}" / "Unknown prompt: ...").
That noise is expected and is itself the point of tests 5-7: the detailed
reason goes to the server's log, while the client sees a sanitized message.

Run: python3 test_server.py
"""

import asyncio

from mcp import Client, MCPError

from server import mcp

# JSON-RPC code the SDK actually uses for both a missing required prompt
# argument and an unknown prompt name: "Internal error". The spec assigns
# both to -32602 ("Invalid params") instead; asserting the number here is
# what makes that documented gap a checkable fact rather than a citation.
SDK_PROMPT_ERROR_CODE = -32603


async def test_lists_exactly_two_prompts() -> None:
    async with Client(mcp) as client:
        prompts = (await client.list_prompts()).prompts
    assert len(prompts) == 2, f"expected exactly 2 prompts, got {len(prompts)}"
    by_name = {prompt.name: prompt for prompt in prompts}
    assert set(by_name) == {"review_code", "debug_error"}, f"unexpected prompt names: {sorted(by_name)}"

    required = {argument.name: argument.required for argument in by_name["review_code"].arguments}
    assert required == {"code": True, "language": False}, (
        f"review_code's arguments should be code=required, language=optional, got {required}"
    )
    print("ok  list_prompts() returns review_code + debug_error; code is required, language is not")


async def test_review_code_default_language() -> None:
    async with Client(mcp) as client:
        result = await client.get_prompt("review_code", {"code": "print(1)"})
    assert len(result.messages) == 1, f"a str return should render as 1 message, got {len(result.messages)}"
    message = result.messages[0]
    assert message.role == "user", f"a str return should render as a user message, got {message.role!r}"
    assert "print(1)" in message.content.text, message.content.text
    assert "python" in message.content.text, message.content.text
    print("ok  get_prompt('review_code') with language omitted -> 1 user message using the 'python' default")


async def test_review_code_explicit_language() -> None:
    async with Client(mcp) as client:
        result = await client.get_prompt("review_code", {"code": "fn main() {}", "language": "rust"})
    assert len(result.messages) == 1, f"expected 1 message, got {len(result.messages)}"
    message = result.messages[0]
    assert message.role == "user", f"expected a user message, got {message.role!r}"
    assert "fn main() {}" in message.content.text, message.content.text
    assert "rust" in message.content.text, message.content.text
    assert "python" not in message.content.text, (
        f"the explicit language should replace the default, not sit beside it: {message.content.text!r}"
    )
    print("ok  get_prompt('review_code', language='rust') -> the override replaces the default")


async def test_debug_error_returns_seeded_conversation() -> None:
    async with Client(mcp) as client:
        result = await client.get_prompt("debug_error", {"error": "NullPointerException"})
    roles = [message.role for message in result.messages]
    assert roles == ["user", "assistant"], f"expected a user-then-assistant conversation, got {roles}"
    assert "NullPointerException" in result.messages[0].content.text, result.messages[0].content.text
    assert result.messages[1].content.text.strip() != "", "the pre-filled assistant turn must not be empty"
    print("ok  get_prompt('debug_error') -> 2 messages, roles ['user', 'assistant'], error text in the first")


async def test_missing_required_argument_raises_dash_32603() -> None:
    async with Client(mcp) as client:
        try:
            await client.get_prompt("review_code", {})
        except MCPError as exc:
            assert exc.code == SDK_PROMPT_ERROR_CODE, (
                f"expected code {SDK_PROMPT_ERROR_CODE} (internal error), got {exc.code}"
            )
            # Deliberately NOT asserted: the point of this case is to show
            # what the default Client hands back, not to freeze it.
            print(
                f"ok  get_prompt('review_code', {{}}) raises MCPError(code={exc.code}) "
                f"-- observed str(exc) at the default raise_exceptions=False: {str(exc)!r}"
            )
            return
    raise AssertionError("expected MCPError for a missing required argument, nothing was raised")


async def test_missing_required_argument_message_with_raise_exceptions_true() -> None:
    async with Client(mcp, raise_exceptions=True) as client:
        try:
            await client.get_prompt("review_code", {})
        except MCPError as exc:
            assert exc.code == SDK_PROMPT_ERROR_CODE, (
                f"raise_exceptions=True should not change the code, got {exc.code}"
            )
            assert "Missing required arguments" in str(exc), (
                f"raise_exceptions=True should surface the original ValueError text, got {str(exc)!r}"
            )
            print(
                "ok  same call under Client(raise_exceptions=True) -> same code, but str(exc) "
                f"carries the real reason: {str(exc)!r}"
            )
            return
    raise AssertionError("expected MCPError for a missing required argument, nothing was raised")


async def test_unknown_prompt_name_raises_dash_32603() -> None:
    async with Client(mcp) as client:
        try:
            await client.get_prompt("no_such_prompt", {})
        except MCPError as exc:
            assert exc.code == SDK_PROMPT_ERROR_CODE, (
                f"expected code {SDK_PROMPT_ERROR_CODE} (internal error), got {exc.code}"
            )
            print(
                f"ok  get_prompt('no_such_prompt') raises MCPError(code={exc.code}) too -- the same "
                "generic bucket, where an unknown *resource* id gets the spec-correct -32602"
            )
            return
    raise AssertionError("expected MCPError for an unknown prompt name, nothing was raised")


async def _run_all() -> None:
    await test_lists_exactly_two_prompts()
    await test_review_code_default_language()
    await test_review_code_explicit_language()
    await test_debug_error_returns_seeded_conversation()
    await test_missing_required_argument_raises_dash_32603()
    await test_missing_required_argument_message_with_raise_exceptions_true()
    await test_unknown_prompt_name_raises_dash_32603()


def main() -> int:
    asyncio.run(_run_all())
    print("\nAll 7 self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
