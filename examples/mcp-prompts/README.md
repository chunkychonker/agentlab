# MCP prompts, the third primitive (`prompts-demo`)

One [MCP](https://modelcontextprotocol.io) server exposing two **prompts** —
the user-controlled primitive that
[`examples/mcp-resources-vs-tools/`](../mcp-resources-vs-tools/) left out when
it sorted model-driven tools from application-driven resources. Offline
self-test through the SDK's in-memory `Client`: no subprocess, no live host
(Claude Desktop/Code), no network, no API key.

From the research note:
[`research/2026-09-18-mcp-prompts.md`](../../research/2026-09-18-mcp-prompts.md).

## What's here

| File | What it is |
|------|-----------|
| `server.py` | `MCPServer("prompts-demo")`: `review_code` (required + optional argument, returns a `str`) and `debug_error` (returns `list[Message]`, a seeded conversation). |
| `test_server.py` | Offline self-test: drives `server.mcp` through `mcp.Client`'s in-memory transport, asserting the seven claims below. |
| `requirements.txt` | `mcp>=2.1.0,<3` — note the **narrower floor** than this repo's other MCP examples; see below. |

## Run the self-test (no API key, no network)

```bash
cd examples/mcp-prompts
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_server.py
```

Expected output — **7** `ok` lines, exit code 0 (verbatim from a real run
against `mcp` 2.2.0 on Python 3.13.1, 2026-09-18):

```
ok  list_prompts() returns review_code + debug_error; code is required, language is not
ok  get_prompt('review_code') with language omitted -> 1 user message using the 'python' default
ok  get_prompt('review_code', language='rust') -> the override replaces the default
ok  get_prompt('debug_error') -> 2 messages, roles ['user', 'assistant'], error text in the first
ok  get_prompt('review_code', {}) raises MCPError(code=-32603) -- observed str(exc) at the default raise_exceptions=False: 'Internal server error'
ok  same call under Client(raise_exceptions=True) -> same code, but str(exc) carries the real reason: "Missing required arguments: {'code'}"
ok  get_prompt('no_such_prompt') raises MCPError(code=-32603) too -- the same generic bucket, where an unknown *resource* id gets the spec-correct -32602

All 7 self-tests passed.
```

**Tracebacks on stderr are expected, not a failure.** Two of the three error
cases (tests 5 and 7, the ones using a default `Client`) make the SDK log
`request handler raised` plus a full traceback ending in the real
`ValueError` — that is the server-side log the SDK's docs say the detailed
reason goes to. Test 6's `raise_exceptions=True` client logs *nothing*; it
re-raises instead. On a terminal those tracebacks interleave with the `ok`
lines above. `stdout` itself stays clean, and the exit code is 0.

## Why `mcp>=2.1.0`, not the `>=2.0.0` the other examples pin

`server.py` imports `Message`/`UserMessage`/`AssistantMessage` straight from
`mcp.server.mcpserver`. That top-level re-export landed in **v2.1.0**
(2026-08-24); on 2.0.x the import raises `ImportError` — verified by
installing `mcp==2.0.0` and trying it, not just read off a changelog. The
older, deeper path `mcp.server.mcpserver.prompts.base` works on both, but this
example uses the current documented one, so the floor moves up. Everything
else here runs on 2.0.x.

## The three claims this makes checkable, not just cited

### 1. The return type picks the message shape

```python
@mcp.prompt(title="Code review")
def review_code(code: str, language: str = "python") -> str: ...
```

- `str` → exactly **one `user`-role message**. Test 2 asserts the count, the
  role, and that the omitted `language` resolved to `"python"`; test 3
  asserts `"rust"` *replaces* the default rather than sitting beside it.
- `list[Message]` → a **seeded multi-turn conversation**, rendered in list
  order. `debug_error` returns `[UserMessage(...), AssistantMessage(...)]`,
  and test 4 asserts the roles come back as `["user", "assistant"]`. That
  assistant turn is not something the model said — it is a turn the prompt
  author put in the model's mouth to steer the next reply.

Note the shape difference from an Anthropic API message: a `PromptMessage`
holds **one** content block, not an array of them.

### 2. Arguments are a flat list of strings, and required-ness comes from defaults

`list_prompts()` reports `review_code`'s arguments as
`code (required=True)` and `language (required=False)` — derived purely from
which parameter has a default, exactly like `@mcp.tool()`. Unlike a tool,
**no JSON Schema is generated**: there is nothing to generate it from, since
prompt arguments are always plain strings. A form a person fills in, not a
payload a model constructs. Test 1 asserts that metadata without invoking
either handler.

### 3. The spec-vs-SDK error-code gap, and the two-`Client` contrast

The [spec](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)
says a missing required argument and an invalid prompt name are both
`-32602` (Invalid params). **The Python SDK returns `-32603` (Internal
error) for both** — `Prompt.render()` raises a plain `ValueError` before your
function runs, and the dispatch path wraps any unhandled exception in the
generic internal-error bucket. Tests 5–7 assert the number.

This is the *opposite* choice the same SDK makes for resources:
[`examples/mcp-resources-vs-tools/`](../mcp-resources-vs-tools/)'s
`test_reading_unknown_note_raises_mcp_error_invalid_params` shows an unknown
resource id getting the spec-correct `-32602`, because
`ResourceNotFoundError` is special-cased. Two ID-not-found situations, one
codebase, two different codes — worth knowing before you write a client that
branches on `exc.code`.

The second half is what the *message* says, which depends on a client flag:

| `Client(mcp)` (`raise_exceptions=False`, the default) | `Client(mcp, raise_exceptions=True)` |
|---|---|
| `exc.code == -32603` | `exc.code == -32603` (unchanged) |
| `str(exc) == 'Internal server error'` — sanitized even though this is an in-memory client with no wire between the two halves | `str(exc) == "Missing required arguments: {'code'}"` — the original `ValueError` text, chained as `__cause__` |
| the real reason is logged server-side (stderr) | nothing is logged; the exception is re-raised instead |

Test 5 deliberately **prints** the observed `str(exc)` rather than asserting
it, so the test output shows what the default actually hands back; test 6
asserts the descriptive text under `raise_exceptions=True`. If you debug a
prompt against the default client, "Internal server error" is all you get —
flip the flag or read the log.

## Poke it manually (optional, not part of the self-test)

```bash
.venv/bin/python server.py
```

Blocks with zero output, waiting on stdin — that's correct: once `mcp.run()`
starts serving, stdout is the JSON-RPC wire, which is why there is no
`print()` anywhere in `server.py`. Ctrl-C to stop. To click around instead:

```bash
.venv/bin/pip install "mcp[cli]>=2.1.0,<3"
.venv/bin/mcp dev server.py   # requires npx on PATH
```

## Explicitly out of scope

- **The live host.** How Claude Code surfaces these as
  `/mcp__prompts-demo__review_code` slash commands is a real question, but
  the research note found the official docs page that used to describe it no
  longer carries that section, and the argument-passing syntax is only
  corroborated by third-party write-ups and a closed GitHub issue. Verifying
  it against the actual CLI is its own increment, not a guess bolted onto
  this one — the same way `mcp-resources-vs-tools/` deferred its live
  `@`-mention half.
- `Image`/`Audio` prompt content and bare content-block returns (real in
  v2.1.0+, but surface area the two-prompt core doesn't need).
- `prompts/list_changed` notifications and subscriptions.
- `InputRequiredResult` multi-round-trip argument collection, and argument
  autocompletion (`completions`).
