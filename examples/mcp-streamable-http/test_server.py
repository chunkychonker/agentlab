"""Offline self-test for server.py -- no socket, no network, no API key.

Mounts `server.app` (a plain Starlette ASGI app returned by
`MCPServer.streamable_http_app()`) directly with `httpx2.ASGITransport` and
drives it with the SDK's *own* client transport, `streamable_http_client()` +
`ClientSession`. That exercises the real Streamable HTTP framing -- session-ID
header, POST/DELETE sequence, status codes -- which the in-memory `Client`
used by `examples/mcp-hello-world/test_server.py` skips by construction. Only
the socket is faked, and `test_no_tcp_socket_is_opened` proves that rather
than asserting it.

Convention matches examples/mcp-hello-world/test_server.py: plain `test_*`
async functions, each printing "ok  <description>" on success, collected and
run from `main() -> int`.

Run: python3 test_server.py
"""

import asyncio
import socket
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import AsyncIterator

import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

from server import MAX_ABS_OPERAND, MCP_PATH, app, mcp

# Nothing is ever bound to this port -- `ASGITransport` never opens a socket,
# so the authority is only ever used to build a Host header. The port must
# still be present: see `test_missing_port_in_host_header_is_rejected_421`.
BASE_URL_WITH_PORT = "http://127.0.0.1:9999"

# The same authority with the port omitted: the mistake this example exists
# partly to document. Kept as a named constant so the two host-header tests
# differ in exactly one value and nothing else.
BASE_URL_NO_PORT = "http://127.0.0.1"

SESSION_ID_HEADER = "mcp-session-id"
MISDIRECTED_REQUEST = 421


@dataclass(frozen=True)
class _Exchange:
    """One HTTP request/response pair observed at the transport boundary."""

    method: str
    status_code: int
    session_id: str | None


class _WireLog(httpx2.AsyncBaseTransport):
    """Records every HTTP exchange, then delegates to a real ASGI transport.

    This is the only way to observe the `Mcp-Session-Id` header from a test:
    `streamable_http_client()` in mcp 2.2.0 yields just `(read, write)` and
    exposes no session-id accessor, so the proof that genuine HTTP session
    bookkeeping happened has to be read off the wire itself.

    Failure modes: none of its own. Any exception from the wrapped transport
    propagates unchanged and is deliberately not recorded, so a test can
    never mistake a failed exchange for a successful one.
    """

    def __init__(self, target_app: object) -> None:
        self._inner = httpx2.ASGITransport(app=target_app)
        self.exchanges: list[_Exchange] = []

    async def handle_async_request(self, request: httpx2.Request) -> httpx2.Response:
        response = await self._inner.handle_async_request(request)
        self.exchanges.append(
            _Exchange(
                method=request.method,
                status_code=int(response.status_code),
                session_id=response.headers.get(SESSION_ID_HEADER),
            )
        )
        return response

    async def aclose(self) -> None:
        await self._inner.aclose()


@asynccontextmanager
async def _running_server() -> AsyncIterator[None]:
    """Enter the session manager's task group for the whole test run.

    Gotcha 1 (verified by execution, see README): the task group must be
    entered before any request reaches the app, or the first one dies with
    `RuntimeError: Task group is not initialized. Make sure to use run().`
    `StreamableHTTPSessionManager.run()` is designed to sit in a Starlette
    `lifespan` handler, but `httpx2.ASGITransport` never emits ASGI lifespan
    events, so an in-process test has to enter it directly.

    Gotcha 1b (found while building this, not in the research note): `run()`
    raises `RuntimeError: ... can only be called once per instance` on a
    second entry. So this wraps the entire run once -- the same lifetime a
    real server process has -- rather than being entered per test. Entering
    it inside `_serving_client` fails on the second test.

    This is the one place in the example that touches `mcp.session_manager`,
    which is verified-by-execution, not documented public API -- so a future
    SDK version that moves the attribute breaks exactly this function.

    Failure modes: raises `RuntimeError` if entered twice on one
    `MCPServer` instance. Swallows nothing.
    """
    # `mcp.session_manager` exists because importing `server` already called
    # `streamable_http_app()`, which creates it lazily.
    async with mcp.session_manager.run():
        yield


@asynccontextmanager
async def _serving_client(base_url: str, wire: _WireLog) -> AsyncIterator[ClientSession]:
    """Yield an initialized `ClientSession` talking to `server.app` over `wire`.

    Requires `_running_server()` to be active. `wire` is passed in rather
    than created here so a caller can inspect the recorded exchanges even
    when the flow raises partway through -- exactly what the 421 test needs.

    Gotcha 2: `base_url` must carry a port, or the server's DNS-rebinding
    protection rejects the request with HTTP 421 (see README).

    Failure modes: raises (wrapped in an `ExceptionGroup` by the SDK's
    internal task groups) if the server rejects the request -- notably on a
    portless `base_url`. Does not swallow anything.
    """
    async with httpx2.AsyncClient(transport=wire, base_url=base_url) as http_client:
        async with streamable_http_client(f"{base_url}{MCP_PATH}", http_client=http_client) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                yield session


def _text_of(result: object) -> str:
    """Join the text of every content block on a `CallToolResult`."""
    return "".join(getattr(block, "text", "") for block in getattr(result, "content", []))


async def test_list_tools_returns_add() -> None:
    wire = _WireLog(app)
    async with _serving_client(BASE_URL_WITH_PORT, wire) as session:
        tools = (await session.list_tools()).tools
    assert len(tools) == 1, f"expected exactly 1 tool, got {len(tools)}"
    assert tools[0].name == "add", f"unexpected tool name: {tools[0].name!r}"
    print("ok  list_tools() over Streamable HTTP returns exactly one tool named add")


async def test_call_tool_add_returns_sum_no_error() -> None:
    wire = _WireLog(app)
    async with _serving_client(BASE_URL_WITH_PORT, wire) as session:
        result = await session.call_tool("add", {"a": 2, "b": 3})
    assert result.is_error is not True, f"unexpected tool error: {result.content}"
    assert _text_of(result) == "5", f"expected text '5', got {_text_of(result)!r}"
    assert result.structured_content == {"result": 5}, result.structured_content
    print("ok  call_tool('add', a=2, b=3) -> is_error=False, content text '5'")


async def test_initialize_issues_a_session_id() -> None:
    wire = _WireLog(app)
    async with _serving_client(BASE_URL_WITH_PORT, wire) as session:
        assert session is not None
    assert wire.exchanges, "no HTTP exchange was recorded at all"
    initialize_exchange = wire.exchanges[0]
    session_id = initialize_exchange.session_id
    assert initialize_exchange.method == "POST", initialize_exchange
    assert session_id, f"initialize returned no Mcp-Session-Id header: {initialize_exchange}"
    # Genuine HTTP session bookkeeping: the same id is echoed on every later
    # exchange, including the terminating DELETE.
    assert all(e.session_id == session_id for e in wire.exchanges), wire.exchanges
    assert any(e.method == "DELETE" for e in wire.exchanges), "session was never terminated with a DELETE"
    print(f"ok  initialize issued a non-empty Mcp-Session-Id ({session_id[:8]}...), reused through the DELETE")


async def test_missing_port_in_host_header_is_rejected_421() -> None:
    wire = _WireLog(app)
    raised: BaseException | None = None
    try:
        async with _serving_client(BASE_URL_NO_PORT, wire) as session:
            await session.list_tools()
    except BaseException as exc:  # noqa: BLE001 -- the rejection is the assertion
        raised = exc
    assert raised is not None, "portless base URL unexpectedly succeeded; DNS-rebinding check did not fire"
    assert wire.exchanges, "no HTTP exchange was recorded at all"
    first = wire.exchanges[0]
    assert first.status_code == MISDIRECTED_REQUEST, f"expected HTTP 421, got {first.status_code} ({first})"
    print("ok  portless Host header (http://127.0.0.1) rejected with HTTP 421 by DNS-rebinding protection")


async def test_correct_host_port_is_accepted() -> None:
    wire = _WireLog(app)
    async with _serving_client(BASE_URL_WITH_PORT, wire) as session:
        tools = (await session.list_tools()).tools
    assert wire.exchanges, "no HTTP exchange was recorded at all"
    first = wire.exchanges[0]
    assert first.status_code == 200, f"expected HTTP 200, got {first.status_code} ({first})"
    assert [t.name for t in tools] == ["add"], tools
    # The only difference from the test above is the ":9999" -- same app, same
    # client, same flow, same host.
    assert BASE_URL_WITH_PORT == f"{BASE_URL_NO_PORT}:9999", "the two host tests must differ only by the port"
    print("ok  identical flow with a port (http://127.0.0.1:9999) is accepted with HTTP 200")


async def test_unknown_tool_is_tool_error_not_exception() -> None:
    wire = _WireLog(app)
    async with _serving_client(BASE_URL_WITH_PORT, wire) as session:
        result = await session.call_tool("no_such_tool", {})
    # Same tool-error-vs-protocol-error split knowledge/mcp-python-sdk.md
    # documents for the in-memory Client: a tool error does NOT raise on the
    # client, it comes back as a normal result with is_error=True.
    assert result.is_error is True, "expected is_error=True for an unknown tool"
    assert "no_such_tool" in _text_of(result), f"error should name the tool, got: {_text_of(result)!r}"
    assert all(e.status_code in (200, 202) for e in wire.exchanges), (
        f"a tool error must stay an HTTP 200-level response, not an HTTP error: {wire.exchanges}"
    )
    print("ok  unknown tool -> is_error=True over HTTP, no client-side raise, still HTTP 200")


async def test_out_of_range_argument_is_tool_error_not_exception() -> None:
    wire = _WireLog(app)
    async with _serving_client(BASE_URL_WITH_PORT, wire) as session:
        result = await session.call_tool("add", {"a": MAX_ABS_OPERAND + 1, "b": 1})
    assert result.is_error is True, "expected is_error=True for an out-of-range operand"
    assert result.structured_content is None, result.structured_content
    assert str(MAX_ABS_OPERAND) in _text_of(result), (
        f"expected the {MAX_ABS_OPERAND} bound referenced in the message, got: {_text_of(result)!r}"
    )
    print(f"ok  operand over {MAX_ABS_OPERAND} -> is_error=True, message names the bound, no raise")


async def test_no_tcp_socket_is_opened() -> None:
    """Criterion 7 as a check, not a claim: poison connect() and run the flow."""
    attempted: list[object] = []
    original_connect = socket.socket.connect

    def _refuse(self: socket.socket, address: object) -> None:
        attempted.append(address)
        raise AssertionError(f"a real TCP connection was attempted to {address!r}")

    socket.socket.connect = _refuse  # type: ignore[method-assign]
    try:
        wire = _WireLog(app)
        async with _serving_client(BASE_URL_WITH_PORT, wire) as session:
            result = await session.call_tool("add", {"a": 20, "b": 22})
    finally:
        socket.socket.connect = original_connect  # type: ignore[method-assign]
    assert not attempted, f"socket.connect was called: {attempted}"
    assert _text_of(result) == "42", f"flow did not actually run: {_text_of(result)!r}"
    print("ok  full initialize+call_tool flow completes with socket.connect poisoned (no TCP, no network)")


async def _run_all() -> None:
    await test_list_tools_returns_add()
    await test_call_tool_add_returns_sum_no_error()
    await test_initialize_issues_a_session_id()
    await test_missing_port_in_host_header_is_rejected_421()
    await test_correct_host_port_is_accepted()
    await test_unknown_tool_is_tool_error_not_exception()
    await test_out_of_range_argument_is_tool_error_not_exception()
    await test_no_tcp_socket_is_opened()


def main() -> int:
    asyncio.run(_run_all())
    print("\nAll 8 self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
