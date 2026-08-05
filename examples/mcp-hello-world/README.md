# MCP hello world (`count_words`)

The smallest genuine [MCP](https://modelcontextprotocol.io) server: one
`MCPServer`, one tool, stdio transport (the default) — plus an offline
self-test that drives it through the SDK's own in-memory `Client`. No
subprocess, no live host (Claude Desktop/Code), no network, no API key.

From the research note:
[`research/2026-08-05-mcp-stdio-hello-world.md`](../../research/2026-08-05-mcp-stdio-hello-world.md).
Background also collected in
[`knowledge/mcp-python-sdk.md`](../../knowledge/mcp-python-sdk.md).

## Why this is worth building right now

The `mcp` Python SDK went through a breaking v1→v2 change one week before
this was built (v2.0.0 GA'd 2026-07-28). `FastMCP` is gone; the server class
is now `from mcp.server import MCPServer`. Nearly every search-engine
snippet and blog post as of 2026-08 still shows the removed v1 import and
will not run against the currently-installed package. This example is
verified against the installed `mcp==2.0.0`, not against memory or search
snippets.

## What's here

| File | What it is |
|------|-----------|
| `server.py` | The server: `MCPServer("hello-world")` with one tool, `count_words`. Pure function body — no I/O, no globals. |
| `test_server.py` | Offline self-test: drives `server.mcp` through `mcp.Client`'s in-memory transport, no subprocess or stdio involved. |
| `requirements.txt` | `mcp>=2.0.0,<3` — the only runtime dependency (pydantic comes in transitively). |

## Run the self-test (no API key, no network)

```bash
cd examples/mcp-hello-world
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_server.py
```

Expected output:

```
ok  list_tools() returns exactly one tool named count_words
ok  normal text 'the quick brown fox' -> 4
ok  empty string -> 0, not an error (0 is a valid count)
ok  over-length text (10,001 chars) -> is_error=True, message references the limit, no raise
ok  missing required argument 'text' -> is_error=True, no raise

All 5 self-tests passed.
```

## The tool

```python
@mcp.tool()
def count_words(text: Annotated[str, Field(max_length=10_000)]) -> int:
    """Count whitespace-separated words in `text`."""
    return len(text.split())
```

`@mcp.tool()` derives the tool's name, description, and input JSON Schema
from the function name, docstring, and type hints — nothing is
hand-written. The `Field(max_length=10_000)` constraint is the schema itself
enforcing the length limit, so an over-long string is an illegal state that
never reaches the function body at all (rather than a hand-written `if
len(text) > 10_000: raise ...` inside it).

### Failure-mode shape (verified, not assumed)

Both a schema violation (over-length `text`) and a missing required
argument are rejected **before `count_words` runs**, and surface to the
caller as a normal, non-raising `CallToolResult`:

- `result.is_error is True`
- `result.structured_content is None`
- the validation message is in `result.content`

Nothing raises on the client for either case. The research note flagged
that the *exact wording* of the `Field(max_length=...)` violation message
hadn't been verified by execution — it has been now: for a 10,001-character
string, `mcp==2.0.0` returns a message containing the literal `10000`
(Pydantic's `String should have at most 10000 characters`), which is what
`test_server.py` asserts on.

## Poke it manually (optional, not part of the self-test)

Confirm the stdio transport is actually live — silence, not a crash or a
banner, is the proof:

```bash
.venv/bin/python server.py
```

This blocks with **zero output**, waiting on stdin. That's correct: once
`mcp.run()` starts serving, stdout is the JSON-RPC wire, so a running stdio
server prints nothing by design. Press Ctrl-C to stop it. (Verified during
this build with stdin held open via a FIFO: the process stayed alive and
silent on both stdout and stderr for the duration.)

To interact with it through the [MCP
Inspector](https://github.com/modelcontextprotocol/inspector) instead of
raw stdin:

```bash
.venv/bin/pip install "mcp[cli]>=2.0.0,<3"
.venv/bin/mcp dev server.py   # requires npx on PATH
```

## Explicitly out of scope

Resources, prompts, HTTP transports (`streamable-http`, `sse`), `mcp
install`/Claude Desktop registration, and the MCP Inspector's UI itself —
each is a separate, already-listed `BACKLOG.md` item ("MCP server wrapping
a public REST API", "Connecting a custom MCP server to Claude Code", "MCP
resources vs tools"). This example only proves the transport is live and
the tool contract behaves as documented; it does not test the JSON-RPC
framing over stdio directly, since that's the SDK's own responsibility, not
this example's.
