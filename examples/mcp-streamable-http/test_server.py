"""Offline self-test for server.py. No API key, no network, no open port.

Each test builds a fresh app and drives it in process with
`httpx2.ASGITransport`. Some tests send hand-built POSTs to inspect raw
status codes. Others run the SDK's real `Client` over
`streamable_http_client` so the whole Streamable HTTP stack is exercised.
The style matches the other MCP examples: plain `test_*` async functions,
each printing "ok  <description>", run from `main() -> int`.

Run: python3 test_server.py   (exit 0 = all passed)
"""

import asyncio
import json
import logging
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager

import httpx2
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from starlette.applications import Starlette

from server import build_app, build_server, public_host_security

ENDPOINT = "/mcp"
LOCAL_HOST = "localhost:8000"
MODERN = "2026-07-28"
LEGACY = "2025-11-25"
TIMEOUT_S = 15  # a hang is a failure, not a wait

# Minimal headers the transport demands of a POST: anything else is a 400/406
# before the Host check, which would mask what the test is about.
BASE_HEADERS = {
    "content-type": "application/json",
    "accept": "application/json, text/event-stream",
}
TOOLS_LIST = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}})


@asynccontextmanager
async def _running(app: Starlette) -> AsyncIterator[Starlette]:
    """Enter the app's lifespan, which is what uvicorn does at startup."""
    async with app.router.lifespan_context(app):
        yield app


async def _post(app: Starlette, headers: dict[str, str]) -> httpx2.Response:
    """POST a tools/list to /mcp with exactly these extra headers (Host included)."""
    async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url="http://test") as http:
        return await http.post(ENDPOINT, headers={**BASE_HEADERS, **headers}, content=TOOLS_LIST)


def _leaves(exc: BaseException) -> Iterator[BaseException]:
    """Flatten nested ExceptionGroups (from anyio task groups) to the leaf errors."""
    if isinstance(exc, BaseExceptionGroup):
        for inner in exc.exceptions:
            yield from _leaves(inner)
    else:
        yield exc


# --- criteria 1-3: the default (localhost-only) allowlist ---------------------


async def test_default_accepts_localhost_with_port() -> None:
    async with _running(build_app(None)) as app:
        resp = await _post(app, {"host": LOCAL_HOST})
    assert resp.status_code not in (403, 421), resp.status_code
    # It reached the MCP layer: the answer is JSON-RPC, not the plain-text guard.
    assert resp.headers["content-type"].startswith("application/json"), resp.headers
    assert resp.json()["jsonrpc"] == "2.0"
    print(f"ok  default app, Host {LOCAL_HOST} -> {resp.status_code} JSON-RPC ({resp.json()['error']['message']})")


async def test_default_rejects_public_host_with_plain_text_421() -> None:
    async with _running(build_app(None)) as app:
        resp = await _post(app, {"host": "mcp.example.com"})
    assert resp.status_code == 421, resp.status_code
    assert "Invalid Host header" in resp.text, resp.text
    assert not resp.headers.get("content-type", "").startswith("application/json"), resp.headers
    print("ok  default app, Host mcp.example.com -> 421 'Invalid Host header' (plain text, not JSON-RPC)")


async def test_default_rejects_bare_localhost_without_port() -> None:
    # The default patterns are "localhost:*", which require a port. A proxy that
    # forwards "Host: localhost" trips the same 421.
    async with _running(build_app(None)) as app:
        resp = await _post(app, {"host": "localhost"})
    assert resp.status_code == 421, resp.status_code
    print("ok  default app, Host localhost (no port) -> 421 too")


async def test_default_origin_check() -> None:
    async with _running(build_app(None)) as app:
        evil = await _post(app, {"host": LOCAL_HOST, "origin": "https://evil.example"})
        same = await _post(app, {"host": LOCAL_HOST, "origin": f"http://{LOCAL_HOST}"})
    assert evil.status_code == 403, evil.status_code
    assert "Invalid Origin header" in evil.text, evil.text
    assert same.status_code not in (403, 421), same.status_code
    print("ok  default app, Origin https://evil.example -> 403; Origin http://localhost:8000 passes")


# --- criterion 4: the fix, and the non-fix -------------------------------------


async def test_public_allowlist_accepts_host_bare_and_with_port() -> None:
    async with _running(build_app(public_host_security("mcp.example.com"))) as app:
        bare = await _post(app, {"host": "mcp.example.com"})
        ported = await _post(app, {"host": "mcp.example.com:443"})
        other = await _post(app, {"host": "other.example.com"})
        local = await _post(app, {"host": LOCAL_HOST})
    assert bare.status_code not in (403, 421), bare.status_code
    assert ported.status_code not in (403, 421), ported.status_code
    assert other.status_code == 421, other.status_code
    assert local.status_code == 421, local.status_code
    print("ok  allowlist mcp.example.com: bare and :443 accepted; other.example.com and localhost:8000 -> 421")


async def test_host_kwarg_disarms_protection_instead_of_allowlisting() -> None:
    # streamable_http_app(host="mcp.example.com") looks like the fix but is not:
    # a non-localhost host turns the guard off entirely, so any Host is accepted.
    app = build_server().streamable_http_app(host="mcp.example.com")
    async with _running(app):
        resp = await _post(app, {"host": "attacker.example"})
    assert resp.status_code not in (403, 421), resp.status_code
    print("ok  streamable_http_app(host='mcp.example.com') accepts Host attacker.example (guard disarmed)")


# --- criterion 5: no shared state between build_app() calls --------------------


async def test_apps_do_not_share_sessions() -> None:
    # A legacy (2025-11-25) session lives in one app's in-process dict. The same
    # id sent to a second app is a 404. This is the "wrong worker" failure that
    # sticky routing exists to prevent.
    first, second = build_app(None), build_app(None)
    session_ids: list[str] = []

    async def record(resp: httpx2.Response) -> None:
        if sid := resp.headers.get("mcp-session-id"):
            session_ids.append(sid)

    async with _running(first), _running(second):
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=first), event_hooks={"response": [record]})
        async with Client(streamable_http_client(f"http://{LOCAL_HOST}{ENDPOINT}", http_client=http), mode="legacy"):
            assert session_ids, "legacy handshake returned no Mcp-Session-Id"
            headers = {"host": LOCAL_HOST, "mcp-session-id": session_ids[0], "mcp-protocol-version": LEGACY}
            on_first = await _post(first, headers)
            on_second = await _post(second, headers)
    assert on_first.status_code == 200, on_first.status_code
    assert on_second.status_code == 404, on_second.status_code
    assert "Session not found" in on_second.text, on_second.text
    print("ok  legacy session id valid on its own app (200), unknown to a second build_app() (404)")


# --- criterion 6: end-to-end through the real SDK client -----------------------


async def test_client_end_to_end_is_sessionless() -> None:
    seen: list[tuple[str | None, str | None]] = []

    async def record(resp: httpx2.Response) -> None:
        seen.append((resp.request.headers.get("mcp-protocol-version"), resp.headers.get("mcp-session-id")))

    async with _running(build_app(None)) as app:
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), event_hooks={"response": [record]})
        async with Client(streamable_http_client(f"http://{LOCAL_HOST}{ENDPOINT}", http_client=http)) as client:
            tools = await client.list_tools()
            result = await client.call_tool("add_note", {"text": "hello"})
    assert [t.name for t in tools.tools] == ["add_note"], tools.tools
    assert not result.is_error, result
    assert [getattr(b, "text", None) for b in result.content] == ["Saved: hello"], result.content
    assert seen, "no HTTP traffic recorded"
    assert all(pv == MODERN for pv, _ in seen), seen
    assert all(sid is None for _, sid in seen), seen
    print(f"ok  Client over HTTP: lists add_note, returns 'Saved: hello'; {len(seen)} requests, all {MODERN}, no Mcp-Session-Id")


async def test_client_sees_generic_error_on_421() -> None:
    # The server log says "Invalid Host header". The client is told nothing useful.
    async with _running(build_app(None)) as app:
        http = httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app))
        try:
            async with Client(streamable_http_client(f"http://mcp.example.com{ENDPOINT}", http_client=http)):
                pass
        except BaseException as exc:  # anyio wraps the real error in ExceptionGroups
            leaves = list(_leaves(exc))
        else:
            raise AssertionError("client connected through a 421")
    messages = [str(e) for e in leaves]
    assert messages, "no leaf exception"
    assert not any("421" in m or "Host" in m for m in messages), messages
    print(f"ok  client-side view of the 421: {messages[0]!r} (no mention of Host or 421)")


# --- failure mode: the lifespan is mandatory -----------------------------------


async def test_missing_lifespan_raises_before_host_check() -> None:
    app = build_app(None)
    try:
        await _post(app, {"host": "mcp.example.com"})
    except RuntimeError as exc:
        assert "Task group is not initialized" in str(exc), exc
    else:
        raise AssertionError("request without lifespan did not raise")
    print("ok  no lifespan entered -> RuntimeError 'Task group is not initialized' (even for a bad Host)")


TESTS = [
    test_default_accepts_localhost_with_port,
    test_default_rejects_public_host_with_plain_text_421,
    test_default_rejects_bare_localhost_without_port,
    test_default_origin_check,
    test_public_allowlist_accepts_host_bare_and_with_port,
    test_host_kwarg_disarms_protection_instead_of_allowlisting,
    test_apps_do_not_share_sessions,
    test_client_end_to_end_is_sessionless,
    test_client_sees_generic_error_on_421,
    test_missing_lifespan_raises_before_host_check,
]


async def _run_all() -> int:
    failed = 0
    for test in TESTS:
        try:
            async with asyncio.timeout(TIMEOUT_S):
                await test()
        except Exception as exc:  # report every failure, then exit non-zero
            failed += 1
            print(f"FAIL {test.__name__}: {type(exc).__name__}: {exc}")
    if failed:
        print(f"\n{failed} of {len(TESTS)} self-tests FAILED.")
        return 1
    print(f"\nPASS: all {len(TESTS)} self-tests passed.")
    return 0


def main() -> int:
    # The SDK and httpx2 log every request and session at INFO on stderr. Keep
    # the warnings (e.g. "Invalid Host header") so the server-side cause is visible.
    logging.basicConfig(level=logging.WARNING, force=True)
    return asyncio.run(_run_all())


if __name__ == "__main__":
    raise SystemExit(main())
