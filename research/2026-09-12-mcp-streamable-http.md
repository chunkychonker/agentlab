# MCP Streamable HTTP transport: proving the wire, not just the tool contract

## Question

Every MCP example in this lab runs over stdio, or skips the wire entirely via
the SDK's in-memory `Client`. What does the *other* transport — Streamable
HTTP — actually require from a server and a client at the protocol level
(session IDs, response framing, security), and can it be proven end-to-end
without a real socket or a live network call, the way the rest of this lab's
MCP examples are proven?

## Why this topic, not the three items already at the top of the backlog

`BACKLOG.md`'s Coding-agents/Skills/MCP sections currently contain zero plain
`[ ]` items — every entry is `[done #N]` or `[stranded cycle/<branch>]`. Before
picking a topic I checked what each stranded branch actually holds, because
`knowledge/pipeline-claim-lifecycle.md` documents that a `[stranded ...]`
marker means "a cycle already worked here and got carried off by
`snapshot_dirty_main`," not "nobody has touched this yet":

- `cycle/2026-09-03-unshipped-141731-1` (`strict: true` tool schemas) — the
  branch's tip commit contains a **complete, self-verified build**:
  `examples/strict-tool-schemas/` (agent.py, schema_subset.py, test_agent.py,
  README), `knowledge/strict-tool-use.md`, and a 394-line research note. The
  cycle log (`logs/run-2026-09-03_134704.log`) shows the builder ran 8/8
  offline self-tests, a transcript check, and mutation-tested its own claims —
  the phase that killed the cycle was `review`, which failed with
  `You've hit your session limit`, an infrastructure outage, not a verdict on
  the work.
- `cycle/2026-08-29-unshipped-120828-1` (MCP prompts) — same shape: a complete
  `examples/mcp-prompts/` plus `knowledge/mcp-prompts.md` and a research note,
  stranded by the same kind of unfinished cycle.
- `cycle/2026-09-05-unshipped-133345-1` (MCP resources through the live
  Claude Code host) — same shape again: `examples/mcp-resources-claude-code/`
  plus a research note, stranded.

Re-researching any of these three would duplicate work that already exists,
verified, on disk — it would just be invisible to `git log main` and to
`gh pr list`. That is exactly the gap this cycle's own instructions warn
about (open PRs aren't the only way work can already be "done"), so I flagged
it here rather than silently reusing or silently ignoring it, and picked a
genuinely different, previously-untouched sub-topic instead. Whether to
salvage those three branches is a human call — per
`knowledge/pipeline-claim-lifecycle.md`, the pipeline "reconciles, it does not
salvage."

## Findings

### The SDK version has moved since the last MCP note was written

`knowledge/mcp-python-sdk.md` was written 2026-08-05 against `mcp==2.0.0`.
`pip install mcp` today (2026-09-12, fresh venv) installs **2.2.0**. Its
declared dependencies now include `httpx2` (not `httpx`), `mcp-types`,
`starlette` (1.6.0), and `uvicorn` (`pip show mcp` — verified by installing it
and reading `Requires:` directly, not from docs). `httpx2` is a real,
separate PyPI package ("The next generation HTTP client",
[pypi.org/project/httpx2](https://pypi.org/project/httpx2/)) — the streamable
HTTP client code imports it explicitly (`mcp/client/streamable_http.py:12`).
Anyone extending an MCP HTTP example with their own outbound HTTP calls needs
to know the SDK's *internal* transport now speaks `httpx2`, even though a
tool's own I/O code is free to keep using plain `httpx`.

### Protocol versions: there's a new "modern" era since the last time anyone here checked

Installed and inspected directly (`mcp_types.version`, `mcp==2.2.0`):

```
KNOWN_PROTOCOL_VERSIONS     = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25', '2026-07-28')
HANDSHAKE_PROTOCOL_VERSIONS = ('2024-11-05', '2025-03-26', '2025-06-18', '2025-11-25')
MODERN_PROTOCOL_VERSIONS    = ('2026-07-28',)
LATEST_PROTOCOL_VERSION = LATEST_MODERN_VERSION = '2026-07-28'
```

Confirmed against the primary spec source, not just the SDK, on
[modelcontextprotocol.io/specification/versioning](https://modelcontextprotocol.io/specification/versioning)
(fetched today): **"The current protocol version is 2026-07-28."** That page
also confirms the handshake/modern split independently of the SDK: pre-2026
versions negotiate once at `initialize` ("handshake-based"); 2026-07-28
onward, "every request declares the protocol version it is using" and "the
server accepts or rejects each request independently" — a materially
different negotiation model, not just a version bump.

Inside the SDK this shows up as genuinely different wire behavior for
Streamable HTTP specifically: `mcp/client/streamable_http.py`'s
`_consume_modern_cancellation` describes it in its own docstring — "The 2026
wire defines no client-to-server notifications over streamable HTTP: closing
a request's response stream IS its cancellation signal," versus the older
eras where an explicit `notifications/cancelled` frame is POSTed. This
distinction is real and current, and out of scope for the increment below
(noted so a future cycle doesn't have to rediscover it).

### `MCPServer.streamable_http_app()` returns a plain Starlette ASGI app — this is the offline-testing seam

`MCPServer` (from `mcp.server`) exposes `streamable_http_app(...) -> Starlette`
alongside `run_streamable_http_async()` (which just wraps the app in
`uvicorn.Server(...).serve()`). Every other MCP example in this repo tests
either via the in-memory `Client(mcp)` (bypasses HTTP framing entirely) or,
for `mcp-connect-claude-code`, a real subprocess against a real live host.
Streamable HTTP has a third, cheaper option this lab hasn't used yet: mount
the ASGI app directly with `httpx2.ASGITransport` and drive it with the SDK's
own `streamable_http_client()` + `ClientSession` — no socket, no port, no
subprocess, no live network. This actually exercises the JSON-RPC-over-HTTP
framing (session ID header, POST/DELETE semantics, SSE-vs-JSON response
selection) that the in-memory `Client` skips past by construction.

**Verified by running it**, not just reading the source (spike in `/tmp`,
discarded after confirming — not part of the repo):

```python
import httpx2
from mcp import ClientSession
from mcp.client.streamable_http import streamable_http_client
from server import app, mcp   # app = mcp.streamable_http_app()

async def main():
    async with mcp.session_manager.run():
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url="http://127.0.0.1:9999") as hc:
            async with streamable_http_client("http://127.0.0.1:9999/mcp", http_client=hc) as (r, w):
                async with ClientSession(r, w) as session:
                    await session.initialize()
                    tools = await session.list_tools()          # -> ['add']
                    result = await session.call_tool("add", {"a": 2, "b": 3})
                    # is_error=False, content=[TextContent(text='5')]
```

Output, real run: `tools: ['add']` /
`result: [TextContent(type='text', text='5', ...)] is_error: False`. A real
`Mcp-Session-Id` header round-trips, a real `202 Accepted`/`200 OK`/`DELETE`
sequence happens, and it's the SDK's actual client transport code running end
to end — only the socket is faked.

Two non-obvious things had to be true for that to work, both worth recording
because they'll trip up anyone reaching for this pattern from memory of v1 or
of plain Starlette `TestClient` habits:

1. **The session manager's task group must be entered before any request.**
   Calling the ASGI app directly raises
   `RuntimeError: Task group is not initialized. Make sure to use run().`
   `StreamableHTTPSessionManager.run()` is an async context manager meant to
   sit in a Starlette `lifespan` — but `httpx2.ASGITransport` (like `httpx`'s)
   does not send ASGI lifespan events by default, so for an in-process test
   the fix is to enter `mcp.session_manager.run()` directly around the test
   body instead of relying on lifespan.
2. **DNS-rebinding host validation is on by default for `127.0.0.1`/`localhost`/`::1`, and it's port-shaped, not host-shaped.**
   `Server.streamable_http_app()` auto-builds
   `TransportSecuritySettings(allowed_hosts=["127.0.0.1:*", "localhost:*", "[::1]:*"])`
   whenever `host` is one of those three (the default). The `:*` match
   requires a literal `:` followed by *something* — `base_url="http://127.0.0.1"`
   with no port fails with `421 Misdirected Request` / "Invalid Host header:
   127.0.0.1", even though it's the exact host on the allow-list's base. A
   base URL with any port (`http://127.0.0.1:9999`, no real bind needed since
   ASGITransport never opens a socket) satisfies the check. This is a genuine,
   reproducible footgun for anyone building a same-machine test client and
   omitting the port because "it's not a real connection anyway." General
   DNS-rebinding-on-loopback-HTTP-servers concern corroborated independently
   for other MCP SDKs — Rust's `rmcp` shipped a
   [security advisory for exactly this class of issue](https://github.com/modelcontextprotocol/rust-sdk/security/advisories/GHSA-89vp-x53w-74fx)
   and Ruby's `mcp` gem has a published CVE
   ([GHSA-rjr6-rcgv-9m7m](https://rubysec.com/advisories/GHSA-rjr6-rcgv-9m7m/))
   — not specific to Python, but it explains why the Python SDK defaults this
   protection *on* rather than off for loopback hosts.

### Small, separately-verified API-drift note

`CallToolResult` in `mcp==2.2.0` is a Pydantic model with attribute
`is_error`, not `isError` — my first spike attempt used the camelCase v1
name from memory and got a clean `AttributeError` with a "did you mean"
correction pointing at the right name. `knowledge/mcp-python-sdk.md` already
documents `is_error` correctly for the in-memory `Client` path; this confirms
the same field name holds for `ClientSession` results over Streamable HTTP.

### Sources, with dates

- [modelcontextprotocol.io/specification/versioning](https://modelcontextprotocol.io/specification/versioning) — fetched 2026-09-12, states current version 2026-07-28
- Installed `mcp==2.2.0` source, read directly: `mcp/client/streamable_http.py`, `mcp/server/streamable_http_manager.py`, `mcp/server/transport_security.py`, `mcp/server/lowlevel/server.py`, `mcp_types/version.py` — inspected 2026-09-12 in a scratch venv (`pip install mcp`, no version pin, so this is genuinely today's PyPI release)
- [pypi.org/project/httpx2](https://pypi.org/project/httpx2/) — confirms `httpx2` is real, current, and the described dependency
- [GHSA-89vp-x53w-74fx](https://github.com/modelcontextprotocol/rust-sdk/security/advisories/GHSA-89vp-x53w-74fx) (Rust SDK DNS-rebinding advisory) and [GHSA-rjr6-rcgv-9m7m](https://rubysec.com/advisories/GHSA-rjr6-rcgv-9m7m/) (Ruby `mcp` gem CVE) — corroborating context only, not load-bearing; the Python-specific behavior above is verified against Python SDK source directly, not inferred from these
- `knowledge/mcp-python-sdk.md` (2026-08-05, this lab) and `examples/mcp-hello-world/README.md` (2026-08-05, this lab) — confirmed HTTP transports were explicitly deferred, not previously covered
- `logs/run-2026-09-03_134704.log`, `logs/run-2026-08-29_114701.log`, `git show --stat` on the three stranded branches — primary evidence for the "already built, not already researched fresh" finding above

## Build proposal

**What:** `examples/mcp-streamable-http/` — the Streamable HTTP sibling of
`examples/mcp-hello-world/`: same shape (one server, one arithmetic-ish tool,
an offline self-test, a README), different transport, proven at the wire
level instead of through the in-memory `Client`.

**Explicitly out of scope for this increment** (name them so a future cycle
doesn't have to rediscover the boundary):
- OAuth / bearer auth (`auth`, `token_verifier` params) — a separate, larger
  topic.
- `stateless_http=True` mode, resumability (`event_store`, `Last-Event-ID`
  reconnection) — real features, not needed to prove the base transport.
- The 2026-07-28 "modern" per-request version negotiation and its different
  cancellation semantics vs the 2025-xx "handshake" eras — real and
  interesting (see Findings above) but a distinct, deeper topic.
- Running the server against a real bound socket / real network client — the
  existing `mcp-hello-world` "poke it manually" convention (a documented,
  not-self-tested manual step) covers the manual-liveness-check style; this
  increment can include the same kind of optional manual section
  (`mcp.run(transport="streamable-http")` under `if __name__ == "__main__":`)
  without making it part of the automated self-test.
- Claude Code / any live host connecting over HTTP — that's `mcp-prompts`'s
  and `mcp-resources-claude-code`'s stranded territory (stdio-focused
  today) and a separate topic regardless.

**Interfaces (layer 3, no bodies — for the builder to fill in):**

```python
# server.py
from mcp.server import MCPServer

mcp = MCPServer("streamable-http-demo")

@mcp.tool()
def add(a: int, b: int) -> int:
    """Add two integers."""
    ...

app = mcp.streamable_http_app()   # module-level: importable by the test, no run() call at import time

if __name__ == "__main__":
    ...  # mcp.run(transport="streamable-http", ...) — manual-poke path only, not exercised by the self-test
```

```python
# test_server.py — offline, no socket, no network, no key
async def _serving_client() -> AsyncContextManager[tuple[ReadStream, WriteStream]]:
    """Enter session_manager.run() + an ASGITransport-backed streamable_http_client
    against `server.app`, yielding the same (read, write) pair `ClientSession` wants."""
    ...

def test_list_tools_returns_add() -> None: ...
def test_call_tool_add_returns_sum_no_error() -> None: ...
def test_initialize_issues_a_session_id() -> None: ...
def test_missing_port_in_host_header_is_rejected_421() -> None: ...
def test_correct_host_port_is_accepted() -> None: ...
```

**Behavioral spec / acceptance criteria** ("it works" means, concretely):

1. Fresh venv, `pip install -r requirements.txt` (`mcp>=2.2.0,<3` — floor
   bumped from the repo's existing `<3` convention because the exact
   `streamable_http_app`/`session_manager` surface used here is verified only
   on 2.2.0; note in the README that this should be re-checked before it
   drifts, same discipline `mcp-hello-world/requirements.txt` already uses).
2. `python3 test_server.py` exits 0 and prints one `ok` line per criterion
   below plus a final `All N self-tests passed.` line (matching this lab's
   established self-test report shape).
3. `list_tools()` through the ASGI-mounted `streamable_http_client` returns
   exactly the one registered tool, by name.
4. `call_tool("add", {"a": 2, "b": 3})` returns `is_error=False` and content
   whose text is `"5"`.
5. After `session.initialize()`, the transport has captured a non-empty
   `Mcp-Session-Id` (proving this is genuine HTTP session bookkeeping, not
   the in-memory `Client`'s direct call path).
6. A request whose Host header has no port (e.g. connecting with
   `base_url="http://127.0.0.1"`, the exact mistake made and caught during
   this research) is rejected with HTTP 421, and the identical flow with a
   port present succeeds — this is the DNS-rebinding-protection gotcha above,
   made into a real, checkable test rather than a README anecdote.
7. No test opens a real TCP socket and no test makes a network call — true by
   construction (`httpx2.ASGITransport`), state it plainly in the README the
   way `mcp-hello-world` states its "no subprocess, no live host" property.
8. README documents the two gotchas from Findings (`session_manager.run()`
   requirement; host:port shape of the DNS-rebinding check) inline, not just
   in this research note, since the next person reaching for this pattern
   will be reading the example, not the dated note.

**Failure modes to state, not swallow** (per this repo's CLAUDE.md §4):
`call_tool` on a nonexistent tool or with bad arguments should surface as
`is_error=True` (mirrors `knowledge/mcp-python-sdk.md`'s existing "tool error
vs protocol error" distinction — cite it, don't re-derive it) — worth one
test to confirm that holds identically over this transport, not just over
the in-memory one.

## Open questions

- Whether `json_response=True` (server always returns a bare JSON body) vs
  the default (server may choose SSE for a tool call's response) is
  observably different from the *client's* point of view for a single
  non-streaming tool call — my spike didn't distinguish the two paths at the
  content-type level. Worth one line in the README if the builder checks it,
  but not required for the acceptance criteria above.
- Whether `mcp.session_manager` being created lazily (only after
  `streamable_http_app()` is called once) is documented behavior or an
  implementation detail — it worked in the spike but the attribute isn't in
  the public method's docstring; treat it as verified-by-execution, not
  guaranteed-stable API, and say so in the README if the builder relies on it
  directly rather than through a small wrapper.
- Exact wording/shape of the `MCPError` raised (if any) versus a plain HTTP
  421 body for the Host-header-rejection case — confirmed the status code by
  execution, did not inspect the response body's JSON-RPC shape (if any) in
  detail.
