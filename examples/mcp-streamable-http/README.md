# MCP over Streamable HTTP, and the Host allowlist that 421s a first deploy

Every other MCP example in this lab uses stdio or the in-memory `Client`. This
one serves a one-tool `MCPServer` (`add_note`) over **Streamable HTTP** with
the 2.x Python SDK. It also reproduces, without a network, the failure most
first HTTP deploys run into:

> The server answers `421 Misdirected Request` with the plain-text body
> `Invalid Host header`. The client reports only
> `MCPError(-32603, 'Server returned an error response')`. The real cause shows
> up only in the **server** log.

From the research note:
[`research/2026-10-26-mcp-streamable-http.md`](../../research/2026-10-26-mcp-streamable-http.md).
Background: [`knowledge/mcp-streamable-http.md`](../../knowledge/mcp-streamable-http.md).

## Why it 421s

`mcp.streamable_http_app()` (and `mcp.run(transport="streamable-http")`) turns
on DNS-rebinding protection by default whenever `transport_security` is not
given and `host` is localhost, which is also the default. The allowlist is then
`127.0.0.1:*`, `localhost:*`, and `[::1]:*`. A deploy behind
`mcp.example.com` sends `Host: mcp.example.com`, which is not on that list, so
the server returns 421. If an `Origin` header is present, it must be the
matching `http://` localhost origin, or the server returns 403.

The fix is an explicit allowlist:

```python
from mcp.server.transport_security import TransportSecuritySettings

app = mcp.streamable_http_app(transport_security=TransportSecuritySettings(
    allowed_hosts=["mcp.example.com", "mcp.example.com:*"],  # bare AND with port
    allowed_origins=[],  # browsers (which send Origin) get 403
))
```

`server.public_host_security("mcp.example.com")` builds exactly that.

Three things that are easy to get wrong, each covered by a test:

- **`localhost:*` requires a port.** A proxy that forwards `Host: localhost`
  (no port) gets 421 even under the default.
- **Your list replaces the default.** After you allowlist `mcp.example.com`,
  `localhost:8000` gets 421.
- **`host="mcp.example.com"` is not the fix.** Passing a non-localhost `host=`
  to `streamable_http_app()`/`run()` does not add that host to any allowlist.
  It just skips arming the guard, so *any* Host is accepted.

**What the allowlist does and does not protect.** It defends against DNS
rebinding: a malicious web page in a browser reaching your local server through
a hostname it controls. **It is not authentication.** Any non-browser client
can send whatever `Host` header it likes. Use real auth (OAuth, out of scope
here) for anything public.

## Files

| File | What it is |
|---|---|
| `server.py` | `build_server()` returns a fresh `MCPServer("Notes")` with `add_note`. `build_app(transport_security)` returns its Starlette app (route `/mcp`). `public_host_security(hostname)` builds the allowlist fix. `__main__` serves on `127.0.0.1:3001`. Nothing in this module reads the environment. |
| `test_server.py` | 10 offline self-tests that drive the app in process through `httpx2.ASGITransport`. |
| `requirements.txt` | `mcp>=2.3,<3` (verified on 2.3.0). It pulls in starlette, uvicorn, and **httpx2**. |

## Run the self-test (no API key, no network, no port)

```bash
cd examples/mcp-streamable-http
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_server.py
```

Expected stdout (exit code 0):

```
ok  default app, Host localhost:8000 -> 400 JSON-RPC (Bad Request: Missing session ID)
ok  default app, Host mcp.example.com -> 421 'Invalid Host header' (plain text, not JSON-RPC)
ok  default app, Host localhost (no port) -> 421 too
ok  default app, Origin https://evil.example -> 403; Origin http://localhost:8000 passes
ok  allowlist mcp.example.com: bare and :443 accepted; other.example.com and localhost:8000 -> 421
ok  streamable_http_app(host='mcp.example.com') accepts Host attacker.example (guard disarmed)
ok  legacy session id valid on its own app (200), unknown to a second build_app() (404)
ok  Client over HTTP: lists add_note, returns 'Saved: hello'; 3 requests, all 2026-07-28, no Mcp-Session-Id
ok  client-side view of the 421: 'Server returned an error response' (no mention of Host or 421)
ok  no lifespan entered -> RuntimeError 'Task group is not initialized' (even for a bad Host)

PASS: all 10 self-tests passed.
```

You will also see `WARNING:mcp.server.transport_security:Invalid Host header: ...`
lines on **stderr**. Those come from the deliberately rejected requests, and
they are the only place the cause of a 421 is named.

What each test proves, mapped to the research note's acceptance criteria:

| # | Test | Shows |
|---|---|---|
| 1 | `default_accepts_localhost_with_port` | A localhost Host reaches the MCP layer. The 400 is a JSON-RPC error because a bare legacy POST has no session, not the guard. |
| 2 | `default_rejects_public_host_with_plain_text_421` | A foreign Host gets 421 with the plain-text body `Invalid Host header`. |
| 2b | `default_rejects_bare_localhost_without_port` | A port-less `localhost` also gets 421. |
| 3 | `default_origin_check` | A foreign `Origin` gets 403. The matching `http://localhost:8000` origin passes. |
| 4 | `public_allowlist_accepts_host_bare_and_with_port` | The fix works for both Host forms, and it replaces the localhost default. |
| 4b | `host_kwarg_disarms_protection_instead_of_allowlisting` | `host=` turns the guard off. It does not allowlist the host. |
| 5 | `apps_do_not_share_sessions` | Each `build_app()` call is independent. A legacy `Mcp-Session-Id` from app A is a 404 on app B, which is the "wrong worker" failure that sticky routing prevents. |
| 6 | `client_end_to_end_is_sessionless` | The real SDK `Client` over `streamable_http_client` lists and calls `add_note`. In the default `auto` mode every request carries `MCP-Protocol-Version: 2026-07-28` and no response has an `Mcp-Session-Id`. |
| 6b | `client_sees_generic_error_on_421` | What the client sees on a 421 says nothing about Host. |
| - | `missing_lifespan_raises_before_host_check` | Without the lifespan, every request raises `RuntimeError`, even one with a bad Host, because the task-group check runs before the Host check. |
| 7 | (runner) | Exits non-zero on any failure, prints a one-line `PASS` summary, and has a 15 s per-test timeout so a hang counts as a failure. |

## Run it for real (optional)

```bash
.venv/bin/python server.py            # serves http://127.0.0.1:3001/mcp
# in another shell:
curl -s -w ' %{http_code}\n' -H 'content-type: application/json' \
  -H 'accept: application/json, text/event-stream' -H 'Host: mcp.example.com' \
  -d '{"jsonrpc":"2.0","id":1,"method":"tools/list","params":{}}' http://127.0.0.1:3001/mcp
# -> Invalid Host header 421
```

The builder ran this once when building the example (localhost got 400, a
foreign Host got 421, and the server log showed `Invalid Host header:
mcp.example.com`). It is not part of the self-test, which never opens a port.
For production, use `uvicorn` with `--proxy-headers --forwarded-allow-ips`
behind a TLS proxy (see the research note).

## Design notes

- **Intent / spec / interfaces** are the research note's build proposal. The
  interfaces that were added or changed are listed here.
- **`build_server()` was added** alongside the proposed `build_app()`. Test
  4b has to call `streamable_http_app(host=...)` on a fresh server, and
  `__main__` needs an `MCPServer` to call `.run()` on. There is **no
  module-level `mcp`/`app`**, contrary to the proposal. A module-level app would
  be a second, unused server instance at import time, and `run()` builds its own
  app anyway.
- **`public_host_security()` was added** so the fix lives in one tested place
  and doesn't get retyped in every caller. It does not validate its input
  (stated in its docstring).
- **Offline driver:** `httpx2.ASGITransport`. mcp 2.3.0 depends on `httpx2`,
  not `httpx`, so Starlette's `TestClient` (which needs `httpx`) is not
  available without adding a dependency. The SDK `Client` takes
  `streamable_http_client(url, http_client=httpx2.AsyncClient(transport=ASGITransport(app)))`
  as a transport. That resolves the research note's open question, so test 6
  uses the real client rather than the hand-built JSON-RPC fallback.
- **`mode="legacy"`** is the correct spelling (the other open question). It
  forces the `initialize` handshake, and the server then sends
  `Mcp-Session-Id` on every response.
- **Lifespan in tests** is entered with `app.router.lifespan_context(app)`,
  the same thing uvicorn does.

## Not covered

OAuth or any authentication. Multi-worker deploys and sticky routing (test 5
only demonstrates why they matter). CORS. `stateless_http=` and
`json_response=` trade-offs. Resumability (`event_store`). Mounting under
another Starlette app (the docstring states the rule, but no test exercises
it). Connecting Claude Code to an HTTP server.
