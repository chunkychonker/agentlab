# MCP Streamable HTTP (Python SDK 2.x)

Source-read 2026-10-26 from the SDK's `docs/run/*` and `docs/client/transports.md`
(unrun; see `research/2026-10-26-mcp-streamable-http.md`). Extends [[mcp-python-sdk]].

- `mcp.run(transport="streamable-http", port=..)` or `mcp.streamable_http_app(..)` (Starlette). Endpoint `/mcp`, default port 8000. Options go to `run()`, not `MCPServer(...)`.
- **Default Host allowlist is localhost only**: a foreign `Host` gets plain-text `421 Invalid Host header` (bad `Origin` -> 403) before any MCP code runs; the client only sees a generic transport error. Fix with `transport_security=TransportSecuritySettings(allowed_hosts=[...,"x:*"], allowed_origins=[...])`. `host=` alone does not allowlist.
- Mounted sub-apps do not run their lifespan: host must enter `mcp.session_manager.run()`.
- Spec era `2026-07-28` is sessionless (no `Mcp-Session-Id`); legacy `2025-11-25` sessions live in one worker's memory (sticky routing). `stateless_http=True` is legacy-only and removes the server-to-client back-channel. `event_store` is resumability, not a session store.
- SDK client moved to `httpx2`; `streamable_http_client` takes `http_client=`, not `headers=`/`timeout=`.
- Most web tutorials still show v1 `FastMCP`/`streamablehttp_client`; ignore them.
