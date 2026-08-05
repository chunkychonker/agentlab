Backlog item: **Hello-world MCP server (stdio) exposing one tool** (MCP section).

> Note on selection: the two items above this one in the Skills section ("A
> skill that shells out to a local script" and "Packaging a skill with
> reference files...") are marked `[ ]` in `BACKLOG.md`, but both already have
> completed work sitting in **open, unmerged PRs** (`gh pr list`: #5
> `cycle/2026-07-30-skill-script-execution`, #6
> `cycle/2026-07-31-skill-reference-files`), built against the corresponding
> local git worktrees (`examples/skill-script-execution/`,
> `examples/skill-reference-files/`, both gitignored as embedded worktrees).
> Re-researching/re-building either would duplicate work already sitting in
> review. I treated those as effectively claimed and took the next genuinely
> untouched item: the top of the **MCP** section.

## Question

What does it take to stand up the smallest real MCP server — one tool, stdio
transport — with the *current* (2026-08) official Python SDK, and how do you
test it without a live host (Claude Desktop / Claude Code) in the loop?

## Findings

**The SDK just went through a major version change, one week before this
research cycle.** The `mcp` package on PyPI is now **v2.0.0** (GA, tagged
`v2.0.0`, published 2026-07-28 per `gh api repos/modelcontextprotocol/python-sdk/releases`),
requires **Python 3.10+**. `pip install mcp` today installs 2.x; the SDK's own
README warns projects not yet migrated to pin `mcp>=1.28,<2`.
[README](https://github.com/modelcontextprotocol/python-sdk/blob/main/README.md),
[PyPI](https://pypi.org/project/mcp/) — both checked 2026-08-05, repo `pushed_at`
was 2026-08-05T00:51:13Z (same day). This is fresh enough to flag: v2 GA is
about one week old, so treat rough edges as plausible ("Something rough,
confusing, or broken? Open an issue" is in the README itself).

**Breaking change relevant here: `FastMCP` is gone, replaced by `MCPServer`.**
v1's `from mcp.server.fastmcp import FastMCP` no longer exists in v2; the class
is now imported as `from mcp.server import MCPServer` (there is no
`from mcp import MCPServer` — the two halves of the SDK, server and client,
have two distinct import paths: `from mcp import Client` and
`from mcp.server import MCPServer`).
[First steps](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/first-steps.md),
[What's new in v2](https://py.sdk.modelcontextprotocol.io/v2/whats-new/) — 2026-08-05.
Every search-engine snippet and most blog posts (CodeSignal, gofastmcp.com,
dev.to walkthroughs surfaced by web search) still show the v1
`from mcp.server.fastmcp import FastMCP` import — **that code will not run
against the installed package today.** This is exactly the kind of
memory/training-data trap the pipeline should guard against; verified against
the primary source (`docs_src/index/tutorial001.py` and
`docs_src/run/tutorial001.py` in the repo, fetched via `gh api`, both dated to
the current `main`), not against search snippets.

**Minimal server, verbatim from the repo's own `docs_src/run/tutorial001.py`**
(these files are what the SDK's own test suite exercises, per
`docs/get-started/testing.md`):

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

- `@mcp.tool()` derives name (function name), description (docstring), and
  input JSON Schema (type hints) — no manual schema.
- `mcp.run()` **defaults to stdio** with no argument
  ([`docs/run/index.md`](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/index.md),
  2026-08-05): "With no argument, the transport is `stdio`." Other transports
  (`streamable-http`, `sse`) are named explicitly, e.g.
  `mcp.run(transport="streamable-http", port=3001)`.
- The `if __name__ == "__main__":` guard matters mechanically, not just by
  convention: `mcp dev`, `mcp run`, and any test file all **import** the
  module to get the `mcp` object; without the guard, importing the file for a
  test would also start the (blocking) server.
- **stdio gotcha, stated explicitly in the docs**: stdout *is* the wire once
  serving starts. The SDK diverts flushed stdout writes to stderr during
  serving so a stray `print()` inside a tool can't corrupt the JSON-RPC
  stream — but only for output flushed *after* serving begins. A `print()` at
  import time, or one whose buffer isn't flushed until process exit, still
  lands on the wire and corrupts it. The documented fix is: use `logging`,
  never `print`, for anything you want to see while a stdio server runs
  ([`docs/run/index.md`](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/run/index.md)).

**Testing without a host**: the SDK ships `Client` with an in-memory transport
— pass it the server object directly, no subprocess, no port
([`docs/get-started/testing.md`](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/testing.md),
2026-08-05):

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

This is the exact same object the SDK's own doc examples are tested with — it
works fine driven from plain `asyncio.run()`, no pytest/anyio test-runner
required (the docs' own pytest examples add `pytest-anyio` +
`inline-snapshot` as *dev* dependencies for their doc-test suite, but that's
tooling choice, not a requirement of `Client` itself).

**Failure-mode mechanics that matter for a real self-test**, from
[`docs/servers/tools.md`](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/tools.md)
and [`docs/servers/handling-errors.md`](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/handling-errors.md)
(both 2026-08-05):

- A schema violation (wrong type, or a `Field` constraint like `max_length`)
  is rejected **before your function body runs**, and surfaces as a **tool
  error**: `result.is_error is True`, the validation message is in
  `result.content`, `result.structured_content is None`. It does **not** raise
  on the client. Docs' own example: `Field(ge=1, le=50)` on an int arg, call
  with `limit=999`, the client gets back "Input should be less than or equal
  to 50" as a normal (non-raising) `CallToolResult`.
- An ordinary exception raised **inside** the tool body behaves identically:
  `is_error=True`, exception message (prefixed with the tool name) in
  `content`. "Never `return` an error string — a returned string has
  `is_error=False`, so it looks like success. `raise`."
- The one thing that *does* raise on the client is `MCPError` (a protocol-level
  rejection, e.g. `INVALID_PARAMS`) — not needed for a hello-world tool, out of
  scope here.
- I did not run this code myself (no sandboxed execution in this research
  step), so I have **not verified the exact wording** of a Pydantic
  `Field(max_length=...)` violation message. The build should assert
  `result.is_error is True` and a loose substring (e.g. `"character"` /
  the configured limit's digits appearing) rather than an invented exact
  string, and correct the substring after actually running it once.

**Capabilities are declared automatically and are not selective per-primitive**:
even a server that registers *only* a tool still declares `tools`,
`resources`, and `prompts` capabilities (empty resource/prompt lists) — "
`MCPServer` serves all three primitives, so all three are always declared."
([`docs/get-started/first-steps.md`](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/first-steps.md)).
A test should not assert "only tools capability present" — that would be
asserting something the SDK doesn't actually do.

**Install**: `pip install "mcp[cli]"` (or `uv add "mcp[cli]"`). The `[cli]`
extra adds the `mcp` command (`mcp dev` for the MCP Inspector, `mcp run`,
`mcp install` for Claude Desktop registration) — useful for interactive
poking, not required for the automated self-test, which only needs the base
`mcp` package and its `Client`.
([`docs/get-started/installation.md`](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/get-started/installation.md), 2026-08-05.)

## Build proposal

### Intent

Ship the smallest genuine MCP server: one `MCPServer`, one tool, stdio
transport (the default), plus an offline self-test that drives it through the
SDK's own in-memory `Client` — no subprocess, no live host (Claude
Desktop/Code), no network. Out of scope: resources, prompts, HTTP transports,
`mcp install`/Claude Desktop registration, the MCP Inspector — those are later,
separate backlog items (already listed: "MCP server wrapping a public REST
API", "Connecting a custom MCP server to Claude Code", "MCP resources vs
tools").

### Behavioral spec

- **Inputs**: the tool `count_words(text: str) -> int` takes one required
  string argument, `text`, constrained via `Annotated[str, Field(max_length=10_000)]`
  (an intentionally small, in-code illustration of "make illegal states
  unrepresentable": the length limit is enforced by the schema itself, not by
  a hand-written check inside the function body).
- **Output**: whitespace-split word count of `text` (`len(text.split())`), as
  a plain `int` — the SDK turns that into both `content` (stringified) and
  `structured_content = {"result": <int>}`.
- **Invariants**:
  - The function is pure: no I/O, no globals, deterministic given `text`.
  - The server, run with `mcp.run()` under `if __name__ == "__main__":`,
    defaults to stdio and never imports anything that runs the server as a
    side effect of import (so `test_server.py` can `import server` safely).
- **Failure modes**:
  - `text` longer than 10,000 chars → schema validation rejects it before the
    function runs; the client-visible result is `is_error=True` (not a raised
    exception, not a crash) — verify and record the exact message substring
    when actually run, per the "not verified" note above.
  - `call_tool("count_words", {})` (missing required arg) → same shape:
    `is_error=True`, non-raising.
  - Calling the running server with anything other than well-formed JSON-RPC
    over stdio is out of scope — the SDK handles that opacity, this build
    isn't testing the transport's own framing.
- **Acceptance criteria ("it works")** — `python test_server.py` runs to
  completion with exit code 0 and prints one `ok` line per check, covering at
  least:
  1. `client.list_tools()` (or equivalent) returns exactly one tool named
     `count_words`.
  2. Calling it with a normal sentence returns the correct integer in
     `structured_content["result"]`.
  3. Calling it with an empty string returns `0`, not an error (0 is a valid
     count, not an illegal state).
  4. Calling it with `text` over the length limit returns `is_error=True` (a
     `CallToolResult`, not a raised exception) with a message that references
     the limit.
  5. Calling it with the required argument missing returns `is_error=True`,
     not a raised exception.
  - Separately (documented in the README as a manual check, not part of the
    automated test, since it blocks forever by design): `python server.py`
    started directly should hang silently waiting on stdin — that silence, not
    a crash or a printed banner, is the proof the stdio transport is live.

### Interfaces (no bodies — for the builder)

```python
# examples/mcp-hello-world/server.py
from mcp.server import MCPServer

mcp: MCPServer  # module-level, name required for `mcp run`/`mcp dev` discovery

def count_words(text: ...) -> int: ...  # decorated with @mcp.tool()

# if __name__ == "__main__": mcp.run()
```

```python
# examples/mcp-hello-world/test_server.py
# Self-test convention matches examples/skill-anatomy/test_validate_skill.py:
# plain `test_*` functions, each printing "ok  <description>", collected and
# run from a `main() -> int` under `if __name__ == "__main__": raise SystemExit(main())`.
# Async: wrap the body of `main()` (or each test) in `asyncio.run(...)` since
# `Client` is async; no pytest/anyio dependency needed.

async def test_lists_exactly_one_tool() -> None: ...
async def test_normal_text_returns_correct_count() -> None: ...
async def test_empty_string_returns_zero() -> None: ...
async def test_over_length_text_is_tool_error_not_exception() -> None: ...
async def test_missing_argument_is_tool_error_not_exception() -> None: ...

def main() -> int: ...
```

### Where it goes

`examples/mcp-hello-world/`:
- `server.py`
- `test_server.py`
- `requirements.txt` — pin `mcp[cli]>=2.0.0,<3` (upper-bound the major version
  per the SDK's own README advice, since v2 is one week old and the project
  should re-check before ever letting this drift to v3).
- `README.md` — what it is, how to run the self-test, how to poke it manually
  with `uv run mcp dev server.py` (documented as optional; requires `npx` on
  PATH per the SDK docs, not required for the automated self-test), and a
  link back to this note.

## Open questions

- Exact wording of the Pydantic `Field(max_length=...)` validation error
  message — not verified by execution in this research step; the builder
  should run it once and assert on the real substring, not an invented one.
- Whether `mcp[cli]` pulls in enough transitive dependencies to be a "heavy"
  add for a hello-world example — the base `mcp` package (without `[cli]`) is
  likely sufficient for `server.py` + `Client`-based tests; `[cli]` is only
  needed for the optional manual `mcp dev`/`mcp run` commands mentioned in the
  README. The builder should install base `mcp` first and only add `[cli]` if
  the manual-poke instructions in the README need it.
- v2.0.0 is ~1 week old (GA 2026-07-28) — no independent (non-Anthropic,
  non-maintainer) second source confirming stability in production beyond the
  project's own docs/changelog was checked; treat as current-and-primary but
  young.
