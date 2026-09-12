# MCP Streamable HTTP transport: wire-level facts and the in-process testing trick

Everything in [[mcp-python-sdk]] tests a server through the SDK's in-memory
`Client(mcp)`, which connects straight to the server object and never touches
HTTP framing at all. Streamable HTTP is the protocol's other transport
(alongside stdio) and has its own shapes and gotchas, verified 2026-09-12
against `mcp==2.2.0` installed fresh (no version pin) — re-check before
trusting on a further version bump.

## The offline-testing seam: `MCPServer.streamable_http_app()` is a plain ASGI app

`mcp.streamable_http_app(...) -> Starlette` (alongside
`run_streamable_http_async()`, which just hands that same app to
`uvicorn.Server(...).serve()`). That means the wire protocol — session ID
issuance, POST/DELETE semantics, SSE-vs-JSON response framing — can be
exercised **without a real socket**: mount the Starlette app with
`httpx2.ASGITransport` and drive it with the SDK's own
`streamable_http_client()` + `ClientSession`, the same pair a real client
uses. No subprocess, no port, no live network, no API key — verified by
running it, not just reading the source:

```python
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client

async def call_add(app, mcp) -> None:
    async with mcp.session_manager.run():                       # (1)
        transport = httpx2.ASGITransport(app=app)
        base = "http://127.0.0.1:9999"                           # (2) — port required
        async with httpx2.AsyncClient(transport=transport, base_url=base) as hc:
            async with streamable_http_client(f"{base}/mcp", http_client=hc) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    await session.list_tools()
                    await session.call_tool("add", {"a": 2, "b": 3})
```

Two things had to be true, both non-obvious from memory of v1 or of plain
Starlette `TestClient` habits:

**(1) The session manager's task group must be entered manually.** Calling
the ASGI app directly without this raises `RuntimeError: Task group is not
initialized. Make sure to use run().` `StreamableHTTPSessionManager.run()`
is an async context manager meant to sit inside a Starlette `lifespan`
handler — but `httpx2.ASGITransport` (like plain `httpx`'s) never sends ASGI
lifespan events on its own. For an in-process test, enter
`mcp.session_manager.run()` directly around the test body instead.

**(2) DNS-rebinding host validation is on by default for loopback hosts, and it's port-shaped, not host-shaped.**
`Server.streamable_http_app()` auto-builds
`TransportSecuritySettings(allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"])`
whenever `host` is `127.0.0.1`/`localhost`/`::1` (the default). The `:*`
suffix match requires a literal `:` followed by *something*
(`transport_security.py`'s `_validate_host`: `host.startswith(base_host +
":")`). `base_url="http://127.0.0.1"` with **no port** fails — `421
Misdirected Request`, "Invalid Host header: 127.0.0.1" — even though
`127.0.0.1` is the exact string on the allow-list's base. Any port at all
(`http://127.0.0.1:9999` — nothing is actually bound; `ASGITransport` never
opens a socket) satisfies the check. Reproducible footgun: it's easy to
assume a fake/test base URL doesn't need a port "because it's not a real
connection anyway."

This isn't Python-specific paranoia — DNS rebinding against loopback MCP
servers is a live concern across SDKs: Rust's `rmcp` shipped a
[security advisory for missing Host validation](https://github.com/modelcontextprotocol/rust-sdk/security/advisories/GHSA-89vp-x53w-74fx)
and Ruby's `mcp` gem has a
[published CVE for the same class of issue](https://rubysec.com/advisories/GHSA-rjr6-rcgv-9m7m/).
That's why the Python SDK defaults the protection *on* for loopback hosts
specifically, rather than off.

## `CallToolResult.is_error`, not `isError`, over this transport too

`ClientSession.call_tool(...)` returns the same Pydantic `CallToolResult`
shape [[mcp-python-sdk]] already documents for the in-memory `Client` path —
`is_error` (snake_case), never the v1 `isError`. Confirmed by execution: using
the wrong name raises a plain `AttributeError` with a "did you mean"
correction, not a silent `None`.

## Protocol versions: a new "modern" era since the last MCP note here was written

Installed and inspected directly (`mcp_types.version`, `mcp==2.2.0`), and
independently confirmed against the primary spec
([modelcontextprotocol.io/specification/versioning](https://modelcontextprotocol.io/specification/versioning),
fetched 2026-09-12, "The current protocol version is **2026-07-28**"):

```
KNOWN_PROTOCOL_VERSIONS     = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25', '2026-07-28')
HANDSHAKE_PROTOCOL_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
MODERN_PROTOCOL_VERSIONS    = ('2026-07-28',)
```

Pre-2026 versions negotiate once at `initialize` ("handshake-based"). From
2026-07-28 on, per the spec itself, "every request declares the protocol
version it is using" via `_meta`, and the server "accepts or rejects each
request independently" — not just a version-number bump but a different
negotiation model. It changes Streamable HTTP's own semantics too: the SDK's
`_consume_modern_cancellation` docstring states that at 2026-07-28, "closing
a request's response stream IS its cancellation signal" — no
`notifications/cancelled` frame goes over the wire at all, unlike every
earlier era. Neither the modern-era negotiation nor its cancellation
semantics are covered by any example in this lab yet; noted here so the next
cycle that reaches for this doesn't have to re-derive it from source.

## Related

[[mcp-python-sdk]] — everything that's stdio/in-memory-`Client`-shaped;
version-drift note there points here for the HTTP-specific half.
[[claude-code-mcp-connection]] — the *other* kind of "beyond the in-memory
Client" proof: a real live host over stdio, not a real wire protocol over a
fake socket. [[mcp-resources]] — resources are untouched by anything in this
note; nothing here changes their opposite (raise-on-client) failure shape.

Research note: [2026-09-12-mcp-streamable-http](../research/2026-09-12-mcp-streamable-http.md).
