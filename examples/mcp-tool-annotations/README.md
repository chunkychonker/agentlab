# MCP tool annotations (`readOnlyHint`, `destructiveHint`, ...)

An MCP server with two annotated tools: `read_note` is marked read-only and
`delete_note` is marked destructive and idempotent. An offline self-test reads
those hints back through the SDK's in-memory `Client` and then shows that
nothing enforces them. A small pure function (`policy.py`) shows what a careful
client *could* do with them.

There are no subprocesses and no network calls, and you don't need an API key.

From the research note:
[`research/2026-10-18-mcp-tool-annotations.md`](../../research/2026-10-18-mcp-tool-annotations.md).
Background:
[`knowledge/mcp-tool-annotations.md`](../../knowledge/mcp-tool-annotations.md).

## The four hints (MCP spec 2025-11-25, `ToolAnnotations`)

All of them are optional. When a field is left out, the spec default applies:

| Wire name | Python field (SDK 2.x) | Spec default | Meaning |
|---|---|---|---|
| `readOnlyHint` | `read_only_hint` | `false` | The tool does not modify its environment. |
| `destructiveHint` | `destructive_hint` | `true` | May perform destructive updates. `false` means the tool only adds things. Only meaningful when not read-only. |
| `idempotentHint` | `idempotent_hint` | `false` | Calling again with the same arguments has no additional effect. Only meaningful when not read-only. |
| `openWorldHint` | `open_world_hint` | `true` | Interacts with an open set of external entities (for example web search). `false` means a closed domain. |

There is also an optional `title`.

**Pessimistic defaults.** If a tool has no annotations, the spec defaults
describe it as a destructive, non-idempotent writer that talks to the open
world. To have a tool treated as safe, the server author has to say so
explicitly. This example sets every relevant hint explicitly rather than
relying on the defaults.

**The SDK does not fill in defaults.** In `mcp==2.3.0`, an unset field reaches
the client as `None`, and a tool registered without `annotations=` reaches the
client as `annotations is None`. Any client that wants the spec defaults has to
apply them itself, which is what `policy.needs_confirmation` does.

## Hints, not enforcement

The spec says clients "MUST consider tool annotations to be untrusted unless
they come from trusted servers." It also says there SHOULD be a human in the
loop who can deny tool calls. The Python SDK docs put it as: "hints, not
security. Never rely on a client honouring them." The test
`test_hints_are_not_enforcement` demonstrates this: a client calls the
`destructiveHint=true` tool with no confirmation, and the note is gone.

**Whether Claude Code uses these hints is unconfirmed.** The only evidence
comes from unofficial issue trackers. One open feature request (Aug 2026) says
Claude Code's permission engine treats all MCP calls the same way. Using
`readOnlyHint` to allow a tool in plan mode has only been proposed. See the
research note for links. This example makes no claim about how any particular
client behaves.

## What's here

| File | What it is |
|---|---|
| `server.py` | `build_server(notes)`, which returns an `MCPServer("notes-annotated")` with `read_note` and `delete_note` over a private in-memory copy of `notes`. It also builds a module-level `mcp` from `SEED_NOTES`, and runs that over stdio under `__main__`. |
| `policy.py` | `needs_confirmation(annotations) -> bool`: a pure function that applies the spec defaults. It has no I/O. |
| `test_server.py` | 10 offline self-tests that drive the servers through `mcp.Client` in memory. |
| `requirements.txt` | `mcp>=2.0.0,<3` (verified on 2.3.0). |

Declaring hints with SDK 2.x:

```python
from mcp.types import ToolAnnotations

@server.tool(annotations=ToolAnnotations(
    title="Delete a note",
    read_only_hint=False, destructive_hint=True,
    idempotent_hint=True, open_world_hint=False,
))
def delete_note(note_id: str) -> str: ...
```

On the wire (`tools/list`), these hints appear in camelCase:
`{"title": "Delete a note", "readOnlyHint": false, "destructiveHint": true, "idempotentHint": true, "openWorldHint": false}`.
To get the same shape in Python, use
`annotations.model_dump(by_alias=True, exclude_none=True)`.

## Run the self-test (no API key, no network)

```bash
cd examples/mcp-tool-annotations
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_server.py
```

Expected stdout:

```
ok  list_tools() returns exactly read_note and delete_note
ok  read_note annotations on the wire: {'title': 'Read a note', 'readOnlyHint': True, 'openWorldHint': False}
ok  delete_note annotations on the wire: {'title': 'Delete a note', 'readOnlyHint': False, 'destructiveHint': True, 'idempotentHint': True, 'openWorldHint': False}
ok  unset hints arrive as None (the SDK does not apply spec defaults)
ok  destructive delete_note runs with no confirmation; the note is really gone
ok  second delete of the same id is not an error and changes nothing else
ok  read_note on an unknown id -> is_error=True, no raise
ok  each build_server() owns its store; SEED_NOTES is never mutated
ok  tool with no annotations= -> annotations is None -> treated as destructive
ok  needs_confirmation matches spec defaults on 7 cases + both served tools

All 10 self-tests passed.
```

Two `Tool 'read_note' failed: ...` lines also appear on **stderr**. They are
the SDK's INFO log for the deliberate "note not found" errors the tests
trigger. This is expected and does not indicate a failure.

## Design notes and deviations from the proposal

- **The store is injected through `build_server(notes)`**, not kept as a
  module-level dict as the research note proposed. Each server owns a private
  copy, so a test that deletes a note can't affect the next test. The
  module-level `mcp` that the proposal asked for still exists.
- **"Not found" raises `ToolError`, not `ValueError`.** The research note said
  `ValueError` would surface as `is_error=True`. That's true, but in
  `mcp==2.3.0` a plain `ValueError` is treated as a crash: the client sees only
  `Error executing tool read_note`, and the server logs a traceback at ERROR
  level. `mcp.server.mcpserver.exceptions.ToolError` is the SDK's type for an
  anticipated failure, and it passes the message through to the client.
  `test_read_unknown_note_is_tool_error` checks for the note id in the error
  text, so a regression back to `ValueError` would fail that test.
- **Deleting a note that is already absent succeeds**, and the result says
  `already absent`. That is what `idempotentHint=true` declares: the requested
  end state already holds. The downside is that a typo in the id also
  "succeeds". The returned text tells you which case happened.

## Explicitly out of scope

- A live Claude Code run to see whether it honors the hints. A later cycle
  could do this as a billed run, like `examples/mcp-connect-claude-code/`.
- Any permission or enforcement layer. `policy.py` is a demonstration and is
  not wired into anything.
- Which `title` wins when a tool has both a top-level `title` and
  `annotations.title`.
- Resources, prompts, and HTTP transport.
