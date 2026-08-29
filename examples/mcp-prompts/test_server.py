"""Offline self-test for server.py -- no subprocess, no live host, no network.

Drives `server.mcp` through the SDK's own in-memory `Client`, matching the
convention in examples/mcp-hello-world/test_server.py and
examples/mcp-resources-vs-tools/test_server.py: plain `test_*` async
functions, each printing "ok  <description>" on success, collected and run
from `main() -> int`.

Each test asserts one acceptance criterion (AC1-AC7) from the research note's
build proposal (research/2026-08-29-mcp-prompts.md), not an implementation
detail of server.py.

Two tracebacks on **stderr** during the last two tests are expected, not a
failure: with the default `Client`, the dispatcher logs the reason it is
about to sanitize out of the client-visible error. stdout stays clean.

Run: python3 test_server.py
"""

import asyncio

from mcp import Client, MCPError

from server import mcp

# The SDK's JSON-RPC code for an unmapped handler exception. Prompts take this
# path for *both* bad-input cases below, where the MCP spec mandates -32602
# (invalid params); see README, "Spec vs SDK: the error code".
INTERNAL_ERROR = -32603


async def test_list_prompts_returns_exactly_two() -> None:
    async with Client(mcp) as client:
        prompts = (await client.list_prompts()).prompts
    assert len(prompts) == 2, f"expected exactly 2 prompts, got {len(prompts)}"
    names = {p.name for p in prompts}
    assert names == {"review_code", "debug_error"}, f"unexpected prompt names: {names!r}"
    print("ok  list_prompts() returns exactly review_code and debug_error")


async def test_review_code_argument_required_flags() -> None:
    async with Client(mcp) as client:
        prompts = (await client.list_prompts()).prompts
    review = next(p for p in prompts if p.name == "review_code")
    arguments = {a.name: a for a in review.arguments}
    assert set(arguments) == {"code", "language"}, f"unexpected arguments: {set(arguments)!r}"
    assert arguments["code"].required is True, "`code` has no default, so it must publish required=True"
    assert arguments["language"].required is False, "`language` has a default, so it must publish required=False"
    assert arguments["language"].description == "Language the code is written in.", (
        f"Field(description=...) did not reach the wire: {arguments['language'].description!r}"
    )
    print("ok  review_code publishes code as required=True and language as required=False, with its description")


async def test_review_code_default_language_one_user_message() -> None:
    async with Client(mcp) as client:
        result = await client.get_prompt("review_code", {"code": "def f(): pass"})
    assert len(result.messages) == 1, f"a str return must be exactly 1 message, got {len(result.messages)}"
    message = result.messages[0]
    assert message.role == "user", f"a str return must be a user message, got {message.role!r}"
    assert message.content.type == "text", f"expected a text content block, got {message.content.type!r}"
    assert "def f(): pass" in message.content.text, message.content.text
    assert "python" in message.content.text, f"default language not applied: {message.content.text!r}"
    print("ok  get_prompt('review_code') without language -> 1 user text message, default 'python' applied")


async def test_review_code_language_override() -> None:
    async with Client(mcp) as client:
        result = await client.get_prompt("review_code", {"code": "fn f() {}", "language": "rust"})
    text = result.messages[0].content.text
    assert "rust" in text, f"override not applied: {text!r}"
    assert "python" not in text, f"default leaked through despite the override: {text!r}"
    print("ok  get_prompt('review_code', language='rust') overrides the default, 'python' is gone")


async def test_debug_error_seeds_three_messages_last_is_assistant() -> None:
    async with Client(mcp) as client:
        result = await client.get_prompt("debug_error", {"error_text": "TypeError: x"})
    assert len(result.messages) == 3, f"expected exactly 3 messages, got {len(result.messages)}"
    roles = [m.role for m in result.messages]
    assert roles == ["user", "user", "assistant"], f"unexpected roles: {roles!r}"
    assert result.messages[1].content.text == "TypeError: x", (
        f"the argument must be passed through verbatim, got {result.messages[1].content.text!r}"
    )
    assert result.messages[2].content.text == "Let's work through it. What did you expect to happen?", (
        f"pre-filled assistant turn altered: {result.messages[2].content.text!r}"
    )
    print("ok  debug_error seeds 3 messages, roles user/user/assistant (the pre-filled assistant turn)")


async def test_missing_required_argument_raises_mcp_error_code() -> None:
    # Default client: the dispatcher sanitizes the message away entirely.
    async with Client(mcp) as client:
        try:
            await client.get_prompt("review_code", {})
        except MCPError as exc:
            assert exc.code == INTERNAL_ERROR, f"expected code {INTERNAL_ERROR}, got {exc.code}"
            assert str(exc) == "Internal server error", (
                f"expected the sanitized message from the default client, got {str(exc)!r}"
            )
        else:
            raise AssertionError("expected MCPError for a missing required argument, nothing was raised")

    # Opt in to the unsanitized message; the code is unchanged either way.
    async with Client(mcp, raise_exceptions=True) as client:
        try:
            await client.get_prompt("review_code", {})
        except MCPError as exc:
            assert exc.code == INTERNAL_ERROR, f"expected code {INTERNAL_ERROR}, got {exc.code}"
            assert "Missing required arguments" in str(exc), str(exc)
            assert "code" in str(exc), str(exc)
        else:
            raise AssertionError("expected MCPError for a missing required argument, nothing was raised")

    print("ok  missing required argument -> MCPError(code=-32603); message only readable with raise_exceptions=True")


async def test_unknown_prompt_raises_mcp_error_code() -> None:
    async with Client(mcp) as client:
        try:
            await client.get_prompt("no_such_prompt")
        except MCPError as exc:
            assert exc.code == INTERNAL_ERROR, f"expected code {INTERNAL_ERROR}, got {exc.code}"
            assert str(exc) == "Internal server error", (
                f"expected the sanitized message from the default client, got {str(exc)!r}"
            )
        else:
            raise AssertionError("expected MCPError for an unknown prompt name, nothing was raised")

    async with Client(mcp, raise_exceptions=True) as client:
        try:
            await client.get_prompt("no_such_prompt")
        except MCPError as exc:
            assert exc.code == INTERNAL_ERROR, f"expected code {INTERNAL_ERROR}, got {exc.code}"
            assert "Unknown prompt" in str(exc), str(exc)
        else:
            raise AssertionError("expected MCPError for an unknown prompt name, nothing was raised")

    print("ok  unknown prompt name -> MCPError(code=-32603) too, the same path as a missing argument")


async def _run_all() -> None:
    await test_list_prompts_returns_exactly_two()
    await test_review_code_argument_required_flags()
    await test_review_code_default_language_one_user_message()
    await test_review_code_language_override()
    await test_debug_error_seeds_three_messages_last_is_assistant()
    await test_missing_required_argument_raises_mcp_error_code()
    await test_unknown_prompt_raises_mcp_error_code()


def main() -> int:
    asyncio.run(_run_all())
    print("\nAll 7 self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
