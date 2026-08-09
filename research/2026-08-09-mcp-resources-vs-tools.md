# MCP resources vs tools: when to use which

## Question

MCP servers can expose the same underlying data or capability as either a
**tool** or a **resource**. What's the actual, verifiable difference in
control model, protocol mechanics, and host behavior — and what's a concrete
rule for which one a given piece of a server belongs in?

## Findings

### The protocol-level distinction (spec + SDK source, verified 2026-08-09)

Per the official spec (fetched today from
[modelcontextprotocol.io/docs/concepts/resources](https://modelcontextprotocol.io/docs/concepts/resources),
page references protocol version `2026-07-28`):

- **Resources are application-driven.** "Resources in MCP are designed to be
  application-driven, with host applications determining how to incorporate
  context based on their needs" — via a UI picker, search/filter, or
  automatic inclusion by the *host*, not the model deciding mid-conversation
  to call a function.
- **Tools are model-driven** (this half is old, well-established knowledge
  already in [[tool-use-loop]] and [[mcp-python-sdk]] — the model reads
  name/description/schema and decides when to invoke).
- A resource has a `uri`, optional `name`/`title`/`description`/`mimeType`,
  and is fetched via two *separate* RPCs: `resources/list` (metadata only)
  and `resources/read` (content, given a URI). A **resource template**
  (`resources/templates/list`) is the resource equivalent of a parameterized
  tool: a URI with `{placeholders}` (RFC 6570) instead of one static URI.
- Resource errors are protocol-level JSON-RPC errors (`-32602` for "not
  found"), not tool-style `is_error` content blocks.

### Verified directly against the installed SDK (not just docs/blogs)

I downloaded the actual `mcp` 2.0.0 wheel (`pip download mcp --no-deps`) and
read the source rather than trusting search-result summaries (several blog
posts surfaced by web search — Apigene, MCPForge, Stacktree — restate the
spec correctly but are marketing content, not verified against code):

- **`@mcp.resource(uri)`** (in `mcp/server/mcpserver/server.py`) decides
  static-vs-template purely from whether `uri` contains a `{param}`: no
  params → static resource (`FunctionResource`); params → template, and the
  URI's variable names **must exactly equal** the function's parameter names
  or it raises `ValueError` at decoration time (not at first call). This
  exactly mirrors [[agent-skills]]'s "fail at declaration time, not at first
  use" pattern.
- **Listing a resource never executes its function.** Confirmed by reading
  `MCPServer.list_resources()` (`server.py:505`): it maps registered
  `Resource` objects straight to `MCPResource` metadata — no `.read()` call
  anywhere in that path. Only `MCPServer.read_resource()` (`server.py:540`)
  calls `await resource.read()`. This means listing 1,000 resources costs
  nothing per-resource; only the ones a user/host actually opens run code —
  a real, load-bearing cost asymmetry versus tools, whose *schemas* are all
  paid for by every request that includes them (mitigated in Claude Code by
  tool search, see below, but not eliminated at the protocol level).
- **Resource errors raise on the client; tool errors don't.** A resource
  template handler raising `ResourceNotFoundError` (`mcp/server/mcpserver/exceptions.py`)
  propagates through `read_resource()` and surfaces to the SDK's in-memory
  `Client` as `mcp.shared.exceptions.MCPError` with `code=INVALID_PARAMS`
  (-32602) — an exception the caller must catch. This is the *opposite*
  failure shape from tools, where (per [[mcp-python-sdk]], also verified by
  direct execution in a prior cycle) a schema violation or raised exception
  inside a tool body surfaces as `result.is_error=True` content, and does
  **not** raise on the client. Anyone testing both primitives with the same
  try/except pattern will get it wrong for one of them.

### How Claude Code actually handles resources today (verified 2026-08-09, current docs)

This is the part most existing writeups (including a still-cited October
2025 community research doc I could not even reach — it 404'd — and several
now-stale, ~13-month-old closed GitHub issues,
[#3122](https://github.com/anthropics/claude-code/issues/3122) closed
2025-07-07 and [#1461](https://github.com/anthropics/claude-code/issues/1461)
closed 2025-05-31) get out of date on, because host support has moved. Per
the **current** official docs
([code.claude.com/docs/en/mcp](https://code.claude.com/docs/en/mcp), fetched
2026-08-09, referencing CLI versions up to v2.1.221 — consistent with the
CLI version PR #19 verified locally the same week):

- Resources are surfaced to the **user**, not auto-injected to the model:
  typing `@` shows resources from connected MCP servers alongside files in
  autocomplete; the reference syntax is `@server:protocol://resource/path`.
  "Resources are automatically fetched and included as attachments when
  referenced" — i.e., fetched on explicit `@`-mention, matching the spec's
  "application-driven" model exactly (the application here delegates the
  driving to the user).
  Resource templates *are* supported (this fixes the stale 2025-07 issue
  above, which predates the fix).
- Separately: "Claude Code automatically provides tools to list and read MCP
  resources when servers support them" — meaning Claude Code also
  synthesizes something like `ListMcpResourcesTool`/`ReadMcpResourceTool` so
  the *model* can autonomously discover and pull resource content when it
  decides to, not only when the user `@`-mentions one. (This matches issue
  titles like "MCP Resources Pagination Support for `ListMcpResourcesTool`"
  found in the anthropics/claude-code tracker — that tool exists and has its
  own limitations, e.g. no cursor/pagination support as of that report.)
- Net effect: in Claude Code specifically, the resources-vs-tools line is
  blurrier than the spec suggests, because the host paves over "application
  decides" with a synthetic tool the model can call anyway. The clean
  distinction (user/host-driven vs model-driven) is real at the *protocol*
  level and fully real for any host that does its own resource UI (e.g.
  Claude Desktop's resource picker, referenced directly in the spec's own
  screenshot); Claude Code's synthetic list/read tools are an
  implementation choice, not a protocol requirement, and could change.

### The practical rule this converges on

- **Side effects, or the model must decide "if/when/with what arguments" →
  tool.** Anything that writes, creates, sends, deletes, or needs
  model-chosen parameters at call time.
- **Read-only reference content a user or host attaches on demand → resource.**
  Cheap to enumerate in bulk (list is free), addressed by URI, and the
  right shape when *you* (or the host UI) — not the model — decide what's
  relevant to a given turn.
- **A resource *template* is the closest resource-side equivalent of a
  tool's input schema** — same "one function, many concrete instances"
  shape, same "validate the shape once, at declaration" posture — but still
  fetched by URI, not invoked with named arguments, and still surfaced by a
  different RPC than tools.

## Build proposal

**What:** `examples/mcp-resources-vs-tools/` — one MCP server holding a
tiny in-memory notes store, exposing the *same data* through both
primitives to make the contrast concrete and testable, not just described:

- `notes://index` — a **static resource**: JSON list of `{id, title}` for
  every note. Read-only, no arguments.
- `notes://{note_id}` — a **resource template**: full text of one note.
- `create_note(title: str, body: str) -> str` — a **tool**: the only way to
  add a note (a side effect with model-chosen content), returns the new
  note's id.

This is deliberately small — one file, no network, no LLM call, no new
dependency (reuses `mcp` + `pydantic`, already a dependency of
`examples/mcp-hello-world/`) — and isolates exactly the two claims from the
Findings that are checkable in code:

1. listing resources never executes a resource function (call-counter proof)
2. reading an unknown resource raises `MCPError`; calling the tool with bad
   input returns `is_error=True` without raising — the two failure shapes
   are genuinely different, in the same file, side by side

**Where:** `examples/mcp-resources-vs-tools/`
(checked `examples/`, open PRs, and `git branch -a` — name is free; the only
adjacent open work is PR #19, `mcp-connect-claude-code`, a different topic
about driving the real `claude` CLI, not the resources/tools API contrast).

**Shape**, matching the existing `mcp-hello-world`/`mcp-hn-search`
convention exactly:

```
examples/mcp-resources-vs-tools/
  server.py           # MCPServer("notes"): 1 static resource, 1 template, 1 tool
  test_server.py       # offline self-test via in-memory Client(mcp)
  requirements.txt     # mcp>=2.0.0,<3 ; pydantic>=2
  README.md            # what it demonstrates, how to run, what "it works" means
```

`server.py` interface (layers 1-3, no bodies — builder fills these in):

```python
mcp = MCPServer("notes")

@mcp.resource("notes://index")
def list_notes_index() -> str:
    """Return JSON: [{"id": ..., "title": ...}, ...] for every note. Never
    called by resources/list — only by resources/read of this exact URI."""

@mcp.resource("notes://{note_id}")
def read_note(note_id: str) -> str:
    """Return the full body of one note.
    Failure mode: unknown note_id -> raise ResourceNotFoundError (propagates
    to the client as MCPError, code -32602). Do not return an error string."""

@mcp.tool()
def create_note(
    title: Annotated[str, Field(min_length=1, max_length=200)],
    body: Annotated[str, Field(max_length=10_000)],
) -> str:
    """Create a note, return its id.
    Failure modes (both non-raising is_error=True, per Field constraints):
      empty/over-length title; over-length body."""
```

**"It works" — acceptance criteria for `test_server.py`** (offline, via
`Client(mcp)`, no subprocess/network, mirrors `mcp-hello-world/test_server.py`):

- `list_resources()` returns exactly `notes://index` (the template is
  *not* in this list — it belongs to `list_resource_templates()` instead).
- `list_resource_templates()` returns exactly one template,
  `notes://{note_id}`.
- Calling `list_resources()` does **not** invoke `list_notes_index`'s body
  (assert via a module-level call counter) — proves the "list is free, read
  executes" claim empirically, not just by citation.
- `read_resource("notes://index")` returns valid JSON containing the seeded
  note(s).
- `read_resource("notes://<unknown-id>")` raises `MCPError` with
  `code == -32602` — caught with `pytest.raises`-equivalent
  try/except in the plain-assert style this repo uses (no pytest
  dependency, matching existing examples).
- `call_tool("create_note", {"title": "x", "body": "y"})` returns
  `is_error is not True` and a new id; a subsequent
  `read_resource("notes://<that-id>")` returns the note just created —
  proves the tool's side effect is visible through the resource path.
- `call_tool("create_note", {"title": "", "body": "y"})` (empty title,
  violates `min_length=1`) returns `is_error=True` and does **not** raise —
  the direct contrast with the resource-not-found case above, in the same
  test file.

Run: `python3 examples/mcp-resources-vs-tools/test_server.py`, expect all
assertions to print `ok` and exit 0 — same convention as
`mcp-hello-world/test_server.py`.

**Explicitly out of scope** (so the builder doesn't scope-creep): no
`resources/list_changed` notifications, no subscriptions, no actually
wiring this server into Claude Code's `@`-mention flow (that end-to-end
host-connection question is PR #19's territory, not this one) — this
example is about the protocol/SDK-level contract, testable with the
in-memory `Client` the way every other MCP example in this repo already is.

## Open questions

- The October 2025 community "MCP Resources Support Research" doc
  (glama.ai, DollhouseMCP) that turned up in search 404'd when fetched —
  could not verify its claims first-hand, so I did not rely on it; the
  current official Claude Code docs (fetched today) superseded whatever it
  said anyway.
- Whether Claude *Desktop* (not Code) still shows a resource picker UI as
  pictured in the spec's own screenshot — not verified this cycle, out of
  scope for a Claude-Code-focused repo but worth knowing if a future
  increment targets Desktop specifically.
- Whether `ListMcpResourcesTool`/`ReadMcpResourceTool` (Claude Code's
  synthetic tools for model-driven resource access) support pagination
  today — the only source found was a GitHub issue of unknown current
  status ([#3141](https://github.com/anthropics/claude-code/issues/3141));
  did not confirm whether it's fixed, and it doesn't affect the build
  proposal above (which never touches the real `claude` CLI).
- Real-world practitioner reception of resources (adoption rate, common
  complaints) is not covered — the `hn-search` MCP tool mentioned in my
  instructions was not available in this session's toolset, so this angle
  relied on GitHub issues and docs rather than forum discussion.
