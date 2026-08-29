# MCP Python SDK

Package `mcp` on [PyPI](https://pypi.org/project/mcp/). As of 2026-08-05,
latest is **2.0.0** (GA, published 2026-07-28 — check before trusting this,
it was about a week old at time of writing), requires Python **3.10+**.
`pip install mcp` installs 2.x today; pin `mcp>=1.28,<2` if not yet migrated
from v1.

## v1 → v2: the import path changed, and search engines don't know it yet

`FastMCP` is gone. v1's `from mcp.server.fastmcp import FastMCP` no longer
exists. v2 is:

```python
from mcp.server import MCPServer   # server half
from mcp import Client             # client half — no `from mcp import MCPServer`
```

Most web-search results and blog walkthroughs (as of 2026-08) still show the
v1 import and will not run against the currently-installed package. Verify
against the SDK's own `docs_src/` (via `gh api repos/modelcontextprotocol/python-sdk/contents/docs_src/...`),
not against search snippets or memory.

## Minimal stdio server

```python
from mcp.server import MCPServer

mcp = MCPServer("Bookshop")

@mcp.tool()
def search_books(query: str) -> str:
    """Search the catalog by title or author."""
    return f"Found 3 books matching {query!r}."

if __name__ == "__main__":
    mcp.run()
```

- `@mcp.tool()` derives name/description/schema from the function name,
  docstring, and type hints — type hints *are* the JSON Schema, no manual
  schema-writing.
- `mcp.run()` with no argument defaults to **stdio**. Other transports are
  named explicitly: `mcp.run(transport="streamable-http", port=3001)`.
  Transport options (`host`, `port`, ...) are arguments to `run()`, never to
  `MCPServer(...)` (that constructor is for `name`, `version`,
  `instructions`, `log_level`, `debug`).
- The `if __name__ == "__main__":` guard isn't stylistic: `mcp dev`, `mcp run`,
  and any test module all *import* the file to get the server object; without
  the guard, importing it for a test would also start the blocking server.

## Gotcha: stdout is the wire during stdio serving

Once `mcp.run()` (stdio) starts serving, stdout carries the JSON-RPC stream.
The SDK diverts *flushed* stdout writes to stderr while serving so a stray
`print()` inside a tool doesn't corrupt the stream — but only for output
flushed **after** serving begins. Import-time prints, or anything buffered
until interpreter exit, still land on the wire uncaught. Use `logging`
(flushes per-record to stderr), never `print`, for diagnostics in a stdio
server.

## Testing without a host: in-memory `Client`

```python
import asyncio
from mcp import Client
from server import mcp

async def main() -> None:
    async with Client(mcp) as client:
        result = await client.call_tool("add", {"a": 1, "b": 2})
        print(result.structured_content)  # {'result': 3}

asyncio.run(main())
```

`Client(mcp)` connects directly to the server object, no subprocess, no port.
Works fine driven by plain `asyncio.run()` — pytest/anyio is the SDK's own
doc-test tooling choice, not a requirement of `Client`.

## Failure-mode mechanics (matters for writing tests)

Two different failure channels, easy to conflate:

- **Tool error** (the common, correct case): a schema violation (wrong type,
  or a `Field` constraint like `max_length`/`ge`/`le`) *or* an ordinary
  exception raised inside the tool body — both surface identically as
  `result.is_error is True`, the message in `result.content`,
  `result.structured_content is None`. **Does not raise on the client.** The
  model reads the message and can retry with better arguments. Never `return`
  an error string instead of raising — a returned string has `is_error=False`
  and looks like a success to every caller.
- **Protocol error**: raising `MCPError` inside a tool *does* propagate and
  raise on the client (`mcp.shared.exceptions.MCPError`), the way a request
  for a nonexistent capability would. Reserve it for "no retry from the model
  fixes this," not for normal bad-input handling.

## Capabilities are declared for you, not selectively

A server that registers only a tool still declares `tools`, `resources`, and
`prompts` capabilities (empty resource/prompt lists) — `MCPServer` always
declares all three. Don't write a test asserting "only tools capability is
present"; that isn't how the SDK behaves.

## I/O-doing tools: `async def`, and how to test them offline

Per the SDK's own docs (`docs/servers/tools.md`): "If a tool does I/O (calls
an API, reads a file, queries a database), declare it `async def` and `await`
inside it. The SDK awaits it." A plain `def` tool instead runs in a thread
pool — use `async def` whenever the tool body makes a network call.

For a tool that wraps an external HTTP API (see
[[hn-algolia-api]] for a worked example), the in-memory `Client(mcp)` shown
above is *not* enough to test the tool body deterministically — invoking it
would make a real outbound HTTP call. The fix is a dependency-injection seam
at the function boundary: the I/O function takes an `httpx.AsyncClient` as an
explicit **required** parameter (never constructed internally with no seam),
so the caller (the tool, in production) passes a real client and a test
passes an `httpx.MockTransport`-backed one:

```python
def handler(request: httpx.Request) -> httpx.Response:
    if "notfound" in str(request.url):
        return httpx.Response(404, json={"error": "Not Found"})
    raise httpx.TimeoutException("simulated", request=request)  # also works

transport = httpx.MockTransport(handler)
async with httpx.AsyncClient(transport=transport) as client:
    ...  # call the function under test with this client
```

Verified by direct execution (not just reading the docs): a `MockTransport`
handler can return an arbitrary status/body (including a non-JSON body, e.g.
HTML) or raise `httpx.TimeoutException`/`httpx.ConnectError` directly, and
both propagate through `await client.get(...)` exactly like the real failure
would. This means the two failure shapes real APIs actually produce — a
non-2xx response and a timeout — are both reproducible offline with zero
network access, as long as the client is injected rather than constructed
inline. Split the test surface in two: pure parsing/mapping logic (no
`httpx` import at all) gets plain fixture-based unit tests; the HTTP-calling
function gets `MockTransport`-based tests; the in-memory `Client(mcp)` is
reserved for proving tool *registration and schema* only (tool names,
parameter shapes), not for exercising the I/O path.

## Stale-docs gotcha extends to the SDK's own quickstart

The official `quickstart-resources/weather-server-python/weather.py`
tutorial file (fetched 2026-08-06) itself still imports
`from mcp.server.fastmcp import FastMCP` — the removed v1 path (see above).
Even Anthropic/MCP-adjacent quickstart repos haven't all been updated for the
v2 import change; verify the import line yourself against the installed
package version rather than trusting any single doc page, official or not.

Sources: [python-sdk README](https://github.com/modelcontextprotocol/python-sdk/blob/main/README.md), [docs/get-started/first-steps.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/first-steps.md), [docs/get-started/testing.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/testing.md), [docs/run/index.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/index.md), [docs/servers/tools.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/tools.md), [docs/servers/handling-errors.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/handling-errors.md), [PyPI](https://pypi.org/project/mcp/) — all fetched 2026-08-05, repo `pushed_at` same day.

Research note: [2026-08-05-mcp-stdio-hello-world](../research/2026-08-05-mcp-stdio-hello-world.md).

Related: [[agent-skills]] (progressive disclosure is a similar "declare
metadata, load body/resources on demand" shape to MCP's tools/resources/prompts
split), [[tool-use-loop]] (the hand-written Anthropic tool-use loop this
protocol-level tool contract rhymes with), [[hn-algolia-api]] (a worked
example of wrapping a real external REST API as an MCP tool, including the
offline-testing pattern above), [[claude-code-mcp-connection]] (everything
above tests the server object via the SDK's in-memory `Client` — for
connecting the same server to the real Claude Code host and proving it gets
called end-to-end, see that note instead), [[mcp-resources]] (this note
covers tools only — resources are a separate primitive with the opposite
failure shape: they raise on the client instead of returning `is_error=True`),
[[mcp-prompts]] (the third primitive — `@mcp.prompt()` mirrors `@mcp.tool()`
but returns `str`/`list[Message]`, is user-triggered, and *raises* `MCPError`
on a missing required argument rather than returning a tool-style error).
