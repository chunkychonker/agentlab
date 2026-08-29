# MCP prompts (`prompts-demo`)

One [MCP](https://modelcontextprotocol.io) server exposing the **third
primitive**: prompts — named, user-invoked message templates.
[`examples/mcp-resources-vs-tools/`](../mcp-resources-vs-tools/) sorts
model-driven (tools) from application-driven (resources) and stops at two;
this completes the trilogy with the user-driven one. Offline self-test, no
subprocess, no live host, no network, no API key.

From the research note:
[`research/2026-08-29-mcp-prompts.md`](../../research/2026-08-29-mcp-prompts.md).

## What's here

| File | What it is |
|------|-----------|
| `server.py` | `MCPServer("prompts-demo")`: `review_code` (required + optional argument, returns a `str` → one user message) and `debug_error` (returns a `list[Message]` → a seeded 3-turn conversation ending in a pre-filled assistant turn). |
| `test_server.py` | Offline self-test: drives `server.mcp` through `mcp.Client`'s in-memory transport, asserting the seven claims below. |
| `requirements.txt` | `mcp>=2.0.0,<3` — the only runtime dependency (pydantic comes in transitively). Verified against **mcp 2.1.1**. |

## Run the self-test (no API key, no network)

```bash
cd examples/mcp-prompts
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_server.py
```

Expected output:

```
ok  list_prompts() returns exactly review_code and debug_error
ok  review_code publishes code as required=True and language as required=False, with its description
ok  get_prompt('review_code') without language -> 1 user text message, default 'python' applied
ok  get_prompt('review_code', language='rust') overrides the default, 'python' is gone
ok  debug_error seeds 3 messages, roles user/user/assistant (the pre-filled assistant turn)
ok  missing required argument -> MCPError(code=-32603); message only readable with raise_exceptions=True
ok  unknown prompt name -> MCPError(code=-32603) too, the same path as a missing argument

All 7 self-tests passed.
```

**Two tracebacks on stderr are expected.** The last two tests deliberately
trigger handler failures, and the dispatcher logs (`request handler raised`)
the reason it is about to sanitize out of the client-visible error. That is
the mechanism under test, not a failure — stdout stays clean and the exit
code is 0.

## Prompts are user-controlled

The three primitives are split by **who decides when the thing is used**:

| Primitive | Driven by | Trigger |
|---|---|---|
| Tool | the model | the model decides to call it mid-turn |
| Resource | the application/host | the host attaches it (Claude Code's `@`-mention) |
| **Prompt** | **the user** | **a person picks it from a menu and fills in arguments** |

"This refers to who decides *when* the prompt is used, not who authors its
content" ([spec, prompts, protocol version
`2026-07-28`](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)).
The rendered messages are injected into the conversation as if typed.

Two consequences visible in `server.py`:

- **Arguments are a flat list of named strings** — a form a person fills in,
  not a JSON Schema payload a model constructs. There is no schema on a
  prompt argument, only `{name, description?, required}`. Contrast the tool
  in `mcp-hello-world`, whose `Field(max_length=10_000)` becomes a real
  JSON Schema constraint.
- **A prompt can pre-fill an assistant turn.** `debug_error` returns
  `[UserMessage, UserMessage, AssistantMessage]`; the trailing assistant
  message is the spec's documented way to steer the model's *next* reply,
  which no tool result can express.

The SDK's two return shapes are the whole API surface: a bare `str` becomes
exactly one `user` message; a `list[Message]` seeds a multi-turn exchange.
Tests 3 and 5 assert exactly that (1 message vs. 3, with roles).

Required vs. optional is inferred from the Python signature, with no separate
declaration: `code` has no default → published as `required=True`;
`language: str = "python"` → `required=False`, and the body never sees a
missing value. Test 2 asserts both flags off the wire, plus that
`Field(description=...)` actually reaches the client.

## Spec vs SDK: the error code (both bad-input cases diverge)

The spec says a bad `prompts/get` is `-32602` (invalid params) — for an
unknown prompt name *and* for missing required arguments. **The SDK returns
`-32603` (internal error) for both**, as measured here against mcp 2.1.1.

The mechanism, traced through the installed source:

- `Prompt.render()` raises `ValueError("Missing required arguments: {'code'}")`
  (`prompts/base.py:177`); an unknown name raises
  `ValueError(f"Unknown prompt: {name}")` (`mcpserver/server.py:1329`).
- Both are re-wrapped as plain `ValueError` (`server.py:1343`) and fall into
  the dispatcher's generic `except Exception` →
  `MCPError(code=INTERNAL_ERROR)` (`direct_dispatcher.py:278`).

Now the contrast that makes this a real inconsistency rather than a quirk —
**the same SDK gives resources the spec-correct `-32602`**:

```python
# mcp/server/mcpserver/server.py:459-460  (the resources path)
code = INVALID_PARAMS if isinstance(err, ResourceNotFoundError) else INTERNAL_ERROR
raise MCPError(code=code, message=str(err), data={"uri": str(params.uri)})
```

The resources path raises `MCPError` **itself**, so it picks its own code and
its message survives; the prompts path raises a bare `ValueError` and gets
whatever the generic boundary decides. Whoever raises `MCPError` first
controls both fields. `mcp-resources-vs-tools`'s self-test asserts that
`-32602` on the resource side; this one asserts `-32603` on the prompt side.
Same SDK, same kind of "bad input identifier", two different codes — so
**do not write a client that keys off the code to tell these apart.**

### The message is sanitized by default

The SDK's docs say the client sees a generic `"Internal server error"` and
the real reason is log-only. The research note predicted this applied only to
the wire path, and that the in-memory `Client` would show the real message
because `create_direct_dispatcher_pair` defaults to
`raise_handler_exceptions=True`. **Measured, that is wrong**:
`Client.raise_exceptions` defaults to `False` (`mcp/client/client.py:300`)
and is passed straight through, overriding the dispatcher's own default. So:

| Client | `exc.code` | `str(exc)` |
|---|---|---|
| `Client(mcp)` | `-32603` | `'Internal server error'` |
| `Client(mcp, raise_exceptions=True)` | `-32603` | `"Missing required arguments: {'code'}"` / `'Unknown prompt: no_such_prompt'` |

The code is identical either way; only the message differs, and the original
`ValueError` is chained as `__cause__` under the opt-in. Tests 6 and 7 assert
**both** columns, so the sanitization is pinned rather than assumed. The
research note has been corrected to match.

## How Claude Code actually surfaces this

**From [`code.claude.com/docs/en/mcp`](https://code.claude.com/docs/en/mcp)
("Use MCP prompts as commands"), fetched 2026-08-29 — documented, not
live-tested here.** This example is offline by design; verifying it against a
real host is [`mcp-connect-claude-code`](../mcp-connect-claude-code/)'s job
and costs a billed run.

- Type `/` and each MCP prompt appears as **`/servername:promptname (MCP)`**.
  The `/mcp__servername__promptname` form also works. (`/mcp` itself
  connects and manages servers; the prompts land in the normal `/` menu.)
- No-argument prompt: `/mcp__github__list_prs`.
- **Arguments are positional, space-separated, one whitespace-split token
  each**: `/mcp__jira__create_issue login-bug high`.
- Server names are sanitized: any character outside `A-Za-z0-9_-` → `_`. The
  prompt name is used as declared.

So for this server, `review_code` would surface as
`/prompts-demo:review_code`, invoked as
`/mcp__prompts-demo__review_code <code> <language>`.

That mapping is the load-bearing gap: the protocol models arguments as a
**named** form (`{"language": "rust"}`), and Claude Code flattens them to
**ordered positional tokens**. Declaration order therefore becomes part of
your public interface in that host, and an argument whose value contains a
space cannot be passed at all. Same spirit as the resource `@`-mention
finding in `knowledge/mcp-resources.md`: the protocol says one thing, the
host's ergonomics say another.

Worth knowing: Claude Code has merged custom commands into skills, so MCP
prompts and local skills now share one `/` namespace, and a synced skill
whose name collides with an MCP prompt is skipped in favor of the other
command.

## Poke it manually (optional, not part of the self-test)

```bash
.venv/bin/python server.py
```

Blocks with zero output, waiting on stdin — that's correct: once `mcp.run()`
starts serving, stdout is the JSON-RPC wire. (Hence no `print()` anywhere in
`server.py`.) Ctrl-C to stop. To click through it in the [MCP
Inspector](https://github.com/modelcontextprotocol/inspector) instead:

```bash
.venv/bin/pip install "mcp[cli]>=2.0.0,<3"
.venv/bin/mcp dev server.py   # requires npx on PATH
```

## Explicitly out of scope

- `prompts/list_changed` notifications and the `listChanged` capability.
- `InputRequiredResult` — the multi-round-trip argument-collection flow
  `prompts/get` can return instead of messages.
- Non-text message content: `image`, `audio`, `resource_link`, `resource`
  blocks. Every message here is a `TextContent` block.
- Server-side argument autocompletion (the `completions` capability), which
  is what would make the positional-token UX above tolerable.
- Pagination of `prompts/list` (two prompts fit in one page).
- Wiring into a live Claude Code host. The surfacing section above is from
  primary-source docs, not a run; a billed end-to-end check of
  `/mcp__prompts-demo__review_code` is a reasonable follow-up and is
  deliberately not in this increment.
- Modifying [`mcp-resources-vs-tools`](../mcp-resources-vs-tools/). It is
  cross-linked, not touched.
