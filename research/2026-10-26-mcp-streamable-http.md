# MCP over Streamable HTTP and the Host allowlist

Date: 2026-10-26. Backlog: self-filed (no plain `[ ]` feature item remained; the
open items are 46 stale health findings, see [[backlog-health-finding-starvation]]).
Open PR check: only #62 (pipeline gate) is open; no overlap.

## Question

How do you serve an `MCPServer` over Streamable HTTP in the current Python SDK
(mcp 2.x), and what actually breaks on a first deploy that a stdio-only example never shows?

## Findings

All from the SDK's own docs at `modelcontextprotocol/python-sdk` (repo `pushed_at`
2026-10-25; PyPI latest `mcp` 2.3.0 per `pip index` today). Primary sources, read via `gh api`:
`docs/run/index.md`, `docs/run/asgi.md`, `docs/run/deploy.md`,
`docs/run/legacy-clients.md`, `docs/client/transports.md`.

- Serve: `mcp.run(transport="streamable-http", port=3001)`; endpoint default
  `http://127.0.0.1:8000/mcp` (`host`, `port`, `streamable_http_path` are `run()`
  kwargs, never `MCPServer(...)` kwargs -- else `TypeError ... unexpected keyword 'port'`).
- Or get the ASGI app: `mcp.streamable_http_app(...)` returns a Starlette app (route
  `/mcp`, lifespan that runs `mcp.session_manager`). Same kwargs as `run()` minus `port`.
- **Mounting gotcha:** a mounted sub-app's lifespan never runs; the host app must
  enter `mcp.session_manager.run()` or the first request raises
  `RuntimeError: Task group is not initialized`. `session_manager` exists only after
  `streamable_http_app()` is called. This matters for in-process tests too.
- **Host allowlist (the first-deploy trap):** with no `transport_security=`, the app
  arms DNS-rebinding protection and accepts only `Host` of `127.0.0.1:<port>`,
  `localhost:<port>`, `[::1]:<port>`; `Origin`, if present, must be the http form.
  Otherwise `421 Misdirected Request` ("Invalid Host header") or `403` ("Invalid
  Origin header"), plain text, not JSON-RPC, so the client shows a generic transport
  error and only the server log names the cause. Fix:
  `TransportSecuritySettings(allowed_hosts=["mcp.example.com","mcp.example.com:*"], allowed_origins=[...])`
  from `mcp.server.transport_security`. Passing `host="mcp.example.com"` does NOT
  allowlist it; it merely disarms the localhost default (accepts everything).
- **Two protocol eras, one app:** requests are routed on the `MCP-Protocol-Version`
  header. `2026-07-28` is sessionless (no `initialize`, no `Mcp-Session-Id`, any
  worker). `2025-11-25` and earlier get `Mcp-Session-Id` held in one worker's
  in-process dict (sticky routing needed; wrong worker = 404). `stateless_http=True`
  only affects the legacy leg and costs the server-to-client back-channel
  (`NoBackChannelError`). `json_response=True` also drops request-scoped
  notifications and breaks `ctx.elicit()`. `event_store` is resumability, not a session store.
- Client: `Client("http://host:port/mcp")` picks Streamable HTTP; `Client(...)` is
  inert until `async with`. Docs say `mode="legacy"` selects the handshake era
  (legacy-clients example). The SDK moved from `httpx` to **`httpx2`**
  (`streamable_http_client(url, http_client=httpx2.AsyncClient(...))`; `headers=`/
  `timeout=` kwargs no longer exist). Auth/headers live on the injected client.
  Note: the lab's existing `mcp-hn-search` uses plain `httpx`; do not mix them up.
- Other deploy facts: `uvicorn server:app --workers 4`; behind TLS proxy use
  `--proxy-headers --forwarded-allow-ips`; `@mcp.custom_route()` routes are never
  authenticated; `max_request_body_size` default 4 MiB (413).
- Staleness: most web results (mcpcat, kodekloud, glama mirrors) use v1
  `FastMCP`/`streamablehttp_client` and `stateless_http` advice; they predate the
  2026-07-28 sessionless era and are not trustworthy for v2. Not used.

HN reception: hn-search MCP was unavailable this cycle; no practitioner data gathered.

## Build proposal

**Dir:** `examples/mcp-streamable-http/` (checked: not in `ls examples/`, no open PR/branch).

**Intent.** Show that the lab's MCP server can be served over HTTP, and make the
non-obvious deploy failure (421 on a foreign Host) reproducible and fixed, offline.
Out of scope: OAuth, multi-worker/sticky routing, CORS, a live uvicorn process,
connecting Claude Code to it.

**Files.**
- `server.py`: `build_app(transport_security: TransportSecuritySettings | None) -> Starlette`
  (builds a fresh `MCPServer("Notes")` with one tool `add_note(text: str) -> str`
  and returns `mcp.streamable_http_app(transport_security=...)`); module-level
  `mcp`/`app` for `python server.py` -> `mcp.run(transport="streamable-http", port=3001)`
  under `__main__`. Read nothing from env.
- `test_server.py`: plain-asyncio self-test, no key, no network, no port.
- `requirements.txt`: `mcp>=2.3,<3`, plus whatever the in-process ASGI driver needs
  (builder verifies; see open questions).
- `README.md`: how to run, what each assertion proves, note that the 421 is invisible client-side.

**Behavior / acceptance criteria (each is a test assertion).**
1. Default app: a raw ASGI POST to `/mcp` with `Host: localhost:8000` is NOT 421/403
   (reaches MCP; any 2xx/4xx JSON-RPC response is fine).
2. Default app: same POST with `Host: mcp.example.com` returns exactly 421 with body
   containing `Invalid Host header`.
3. Default app: `Host: localhost:8000` plus `Origin: https://evil.example` returns 403.
4. App built with `TransportSecuritySettings(allowed_hosts=["mcp.example.com","mcp.example.com:*"])`:
   `Host: mcp.example.com` is accepted (not 421), and `Host: other.example.com` is still 421.
5. `build_app(None)` does not leak state between calls (fresh server per call), so tests are order-independent.
6. End-to-end: using the mounted-lifespan rule (test enters `app.router.lifespan_context(app)`
   or `mcp.session_manager.run()` itself), an MCP `Client` over an in-process HTTP
   transport lists `add_note` and calls it, returning `Saved: hello`. If the SDK
   client cannot be pointed at an ASGI transport offline, drop this to a hand-built
   JSON-RPC `initialize`/`tools/call` POST with `MCP-Protocol-Version: 2026-07-28`
   and assert the response has no `Mcp-Session-Id` header (sessionless era).
7. Test exits non-zero on any failed assertion and prints a one-line PASS summary.

**Failure modes to state in docstrings:** `build_app` never validates hostnames;
a missing lifespan raises `RuntimeError: Task group is not initialized` (the test
should have one negative case asserting that, if cheap).

**Not claimed:** the README must say the allowlist protects against DNS rebinding
from browsers, not authentication.

## Open questions

- Offline driving: `httpx2.ASGITransport` (or the Starlette `TestClient`) against the
  app is the likely path; I did not run it. Starlette `TestClient` needs `httpx`
  (check whether mcp 2.x still pulls it in alongside `httpx2`). Builder must confirm
  the exact packages with `pip install` and `mcp>=2.3` before relying on any.
- Exact header sent by the modern client and the precise response shape for a bare
  POST without `initialize` (criterion 1 deliberately tolerates any non-421/403).
- Whether `Client(..., mode="legacy")` is the right kwarg spelling (taken from docs prose, unrun).
