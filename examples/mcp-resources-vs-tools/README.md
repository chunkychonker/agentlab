# MCP resources vs tools (`notes`)

One [MCP](https://modelcontextprotocol.io) server, one tiny in-memory notes
store, exposed through **both** primitives at once — a static resource, a
resource template, and a tool — so the difference between them is checkable
in code, not just described. Offline self-test, no subprocess, no live host
(Claude Desktop/Code), no network, no API key.

From the research note:
[`research/2026-08-09-mcp-resources-vs-tools.md`](../../research/2026-08-09-mcp-resources-vs-tools.md).

## What's here

| File | What it is |
|------|-----------|
| `server.py` | `MCPServer("notes")`: a static resource (`notes://index`), a resource template (`notes://{note_id}`), and a tool (`create_note`), all backed by one `NoteStore`. |
| `test_server.py` | Offline self-test: drives `server.mcp` through `mcp.Client`'s in-memory transport, asserting the seven claims below. |
| `requirements.txt` | `mcp>=2.0.0,<3` — the only runtime dependency (pydantic comes in transitively). |

## Run the self-test (no API key, no network)

```bash
cd examples/mcp-resources-vs-tools
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_server.py
```

Expected output:

```
ok  list_resources() returns exactly notes://index (the template is not in this list)
ok  list_resource_templates() returns exactly notes://{note_id}
ok  list_resources() does not invoke list_notes_index's body (call counter unchanged)
ok  read_resource('notes://index') executes the handler (counter +1) and returns valid JSON
ok  read_resource() of an unknown note_id raises MCPError(code=-32602)
ok  create_note()'s side effect is visible immediately through notes://{note_id}
ok  create_note() with an empty title -> is_error=True, no raise (the direct contrast with the resource-not-found case above)

All 7 self-tests passed.
```

## The two claims this makes checkable, not just cited

### 1. Listing resources never runs a resource's function; reading one does

`server.py` keeps a module-level counter, `list_notes_index_calls`,
incremented at the top of the `notes://index` handler. The test asserts:

- After `client.list_resources()`, the counter is **unchanged** — the SDK's
  `list_resources()` maps registered `Resource` objects straight to their
  declared metadata (uri, name, description) with no `.read()` call
  anywhere in that path.
- After `client.read_resource("notes://index")`, the counter is
  **exactly one higher** — only `read_resource()` calls `await
  resource.read()`.

This is the real, load-bearing cost asymmetry from the research note:
listing 1,000 resources costs nothing per-resource; only the ones a
user/host actually opens run code.

### 2. Resource errors raise on the client; tool errors don't

Same file, side by side, same kind of "bad input" for both primitives:

- `read_resource("notes://does-not-exist")` — `read_note`'s handler raises
  `ResourceNotFoundError`, which the SDK propagates to the client as a
  **raised** `mcp.MCPError` with `code == -32602` (JSON-RPC "invalid
  params"). The test catches it with `try`/`except`.
- `call_tool("create_note", {"title": "", "body": "y"})` — the empty title
  violates `Field(min_length=1)`. This surfaces as an ordinary, **non-raising**
  `CallToolResult` with `is_error=True`; nothing is thrown on the client.

Anyone testing both primitives with the same try/except pattern will get it
wrong for one of them — this test file exercises both patterns explicitly so
the contrast is visible in one place.

## The three handlers

```python
@mcp.resource("notes://index")
def list_notes_index() -> str:
    """JSON list of {id, title} for every note. No arguments."""

@mcp.resource("notes://{note_id}")
def read_note(note_id: str) -> str:
    """Full JSON (id, title, body) of one note.
    Unknown note_id -> raises ResourceNotFoundError, not an error string."""

@mcp.tool()
def create_note(title: ..., body: ...) -> str:
    """The only way to add a note — a side effect with model-chosen
    content. Returns the new note's id."""
```

`create_note`'s side effect is immediately visible through the resource
path: `test_create_note_side_effect_is_visible_through_the_resource_path`
calls the tool, then reads the new note back via
`notes://{that_id}` — the same data, two different primitives.

## Poke it manually (optional, not part of the self-test)

```bash
.venv/bin/python server.py
```

Blocks with zero output, waiting on stdin — that's correct: once
`mcp.run()` starts serving, stdout is the JSON-RPC wire. Press Ctrl-C to
stop it. To interact with it through the [MCP
Inspector](https://github.com/modelcontextprotocol/inspector) instead:

```bash
.venv/bin/pip install "mcp[cli]>=2.0.0,<3"
.venv/bin/mcp dev server.py   # requires npx on PATH
```

## Explicitly out of scope

- `resources/list_changed` notifications and subscriptions.
- Wiring this server into a live host's `@`-mention flow (Claude Code's
  synthetic `ListMcpResourcesTool`/`ReadMcpResourceTool`, or Claude
  Desktop's resource picker) — that's a real end-to-end question, but it's
  PR #19's territory (`mcp-connect-claude-code`), not this one. This
  example is about the protocol/SDK-level contract, testable with the SDK's
  own in-memory `Client` the way every other MCP example in this repo
  already is.
- Persistence across process restarts: `NoteStore` is a plain in-memory
  dict, reset every run — a stated limitation, not an oversight.
- Concurrency: the stdio transport handles one request at a time, so
  `NoteStore` is not thread-safe by design, not by accident.
