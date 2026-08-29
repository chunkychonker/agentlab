# MCP prompts, the third primitive

## Question

What is the MCP **prompts** primitive at the protocol/SDK level, how do you
declare and exercise one with the official Python SDK's in-memory `Client`,
and how does Claude Code actually surface it (vs. what the spec says)?

## Findings

### The primitive: user-controlled message templates

- Prompts are **user-controlled**: the server exposes named message templates,
  the *user* picks one from a menu (slash command, button) and fills in its
  arguments; the rendered messages drop into the conversation as if typed.
  This is the deliberate contrast with tools (model-driven) and resources
  (application/host-driven). "This refers to who decides *when* the prompt is
  used, not who authors its content." — [spec, prompts, protocol version
  `2026-07-28`](https://modelcontextprotocol.io/specification/2026-07-28/server/prompts)
  (fetched 2026-08-29).
- Two RPCs: `prompts/list` (metadata: `name`, optional `title`,
  `description`, `arguments[]`, `icons[]`; paginated + cacheable) and
  `prompts/get` (`{name, arguments}` -> `{description, messages[], resultType}`).
- **Prompt arguments are a flat list of named string values** — "a form a
  person fills in, not a payload a model constructs." No JSON Schema. Each
  argument is `{name, description?, required}`.
- A `PromptMessage` is `{role: "user"|"assistant", content: <block>}` where
  `content` is a **single** block (`text` / `image` / `audio` /
  `resource_link` / `resource`), not an array. Pre-filling an `assistant`
  message is the documented way to steer the model's next reply.
- `listChanged` capability + `notifications/prompts/list_changed` exist for
  runtime menu changes; `prompts/get` can also return an `InputRequiredResult`
  for multi-round-trip argument collection. Both are out of scope below.
- Spec error handling: invalid prompt name -> `-32602`; **missing required
  arguments -> `-32602`** (Invalid params); internal errors -> `-32603`.

### The Python SDK (`mcp` v2, latest v2.1.1, released 2026-08-25)

Verified against repo `modelcontextprotocol/python-sdk@main` (pushed
2026-08-28): `docs/servers/prompts.md`, `src/mcp/server/mcpserver/prompts/base.py`,
`.../prompts/manager.py`, `src/mcp/server/mcpserver/server.py`,
`src/mcp/client/client.py`, `src/mcp/shared/direct_dispatcher.py`,
`docs_src/prompts/tutorial00{2,3}.py`.

- Declaration mirrors `@mcp.tool()` exactly — `@mcp.prompt()` on a function;
  **name** = function name, **description** = docstring, **arguments** =
  parameters. A parameter *without a default is required*; *with a default is
  optional*. `@mcp.prompt(title=..., name=...)` and
  `Annotated[str, Field(description=...)]` per-argument, same as tools.
  ```python
  from mcp.server import MCPServer
  from mcp.server.mcpserver.prompts.base import AssistantMessage, Message, UserMessage

  mcp = MCPServer("Code Helper")

  @mcp.prompt(title="Code review")
  def review_code(
      code: Annotated[str, Field(description="The code to review.")],
      language: Annotated[str, Field(description="...")] = "python",
  ) -> str:
      """Review a piece of code."""
      return f"Please review this {language} code:\n\n{code}"

  @mcp.prompt()
  def debug_error(error: str) -> list[Message]:
      """Start a debugging conversation."""
      return [
          UserMessage("I'm seeing this error:"),
          UserMessage(error),
          AssistantMessage("I'll help debug that. What have you tried so far?"),
      ]
  ```
- **Return type:** a `str` becomes **one `user` message**; a `list[Message]`
  (`UserMessage` / `AssistantMessage` from
  `mcp.server.mcpserver.prompts.base`, `Message` is the base / return
  annotation) seeds a multi-turn conversation. `UserMessage`/`AssistantMessage`
  wrap a bare `str` in `TextContent`; they also take a content block or an
  `Image`/`Audio` helper.
- **Client side (in-memory `Client`):** `await client.list_prompts()` ->
  `ListPromptsResult(.prompts)`; `await client.get_prompt(name, arguments)` ->
  `GetPromptResult(.description, .messages)` where each
  `.messages[i]` has `.role` and `.content` (single block, `.content.type`,
  `.content.text`). Same in-memory `Client(mcp)` seam every other MCP example
  in this repo already uses — no subprocess, no host, no key.

### Spec-vs-reality gap #1: the missing-required-argument error code

The SDK's own `docs/servers/prompts.md` states plainly: render a prompt
without a required argument and "the request itself fails with a JSON-RPC
error (code `-32603`)" — **not** the spec's `-32602`. Traced through source:
`Prompt.render()` raises `ValueError("Missing required arguments: {'code'}")`
-> `mcpserver.get_prompt` re-wraps as `ValueError(str(e))` ->
`DirectDispatcher` (the in-memory transport) catches the generic `Exception`
and raises `MCPError(code=INTERNAL_ERROR=-32603, message=str(e))`. An unknown
prompt name (`ValueError("Unknown prompt: X")`) takes the same path -> also
`-32603`. The spec says both should be `-32602`.

Contrast, same SDK: a resource-not-found (`ResourceNotFoundError`) *does* get
the spec-correct `-32602` — `server.py` special-cases it
(`code = INVALID_PARAMS if isinstance(err, ResourceNotFoundError) else
INTERNAL_ERROR`). So the two primitives are **internally inconsistent** on the
"bad input identifier" error code. This is exactly the
`knowledge/claude-code-mcp-connection.md` / `knowledge/mcp-resources.md`
lesson: verify against code, not the spec text.

Sub-caveat for the test: the `docs` page says the client sees a *sanitized*
`"Internal server error"` and the reason is log-only. That is the **wire**
path (`raise_handler_exceptions=False`). The **in-memory `Client`** defaults
to `raise_handler_exceptions=True`, so `str(exc)` should actually contain
`"Missing required arguments: {'code'}"`. The builder must run it and record
whichever is true; the load-bearing assertion is `exc.code == -32603`.

> **Build-time correction (2026-08-29, measured against installed mcp 2.1.1).**
> The `-32603` prediction above is **confirmed**. The sub-caveat is **wrong**:
> the in-memory `Client` sanitizes too. `create_direct_dispatcher_pair` does
> default to `raise_handler_exceptions=True`, but `Client` never lets that
> default apply — `Client.raise_exceptions: bool = False`
> (`mcp/client/client.py:300`, carrying a maintainer `TODO`) is passed
> straight through at `client.py:114`. Observed:
>
> | Client | `exc.code` | `str(exc)` |
> |---|---|---|
> | `Client(mcp)` | `-32603` | `'Internal server error'` |
> | `Client(mcp, raise_exceptions=True)` | `-32603` | `"Missing required arguments: {'code'}"` / `'Unknown prompt: no_such_prompt'` |
>
> Identical for both bad-input cases; the original `ValueError` is chained as
> `__cause__` only under the opt-in. The example asserts both columns.
> Also confirmed at build time: `message.content` is a **single**
> `TextContent` block (`mcp_types._types.TextContent`), so `.content.type` /
> `.content.text` is the right attribute path — it is not a list.

### Spec-vs-reality gap #2: how Claude Code surfaces prompts

The spec mandates no UI model (only suggests slash commands). Claude Code's
own docs, [`code.claude.com/docs/en/mcp` -> "Use MCP prompts as commands"](https://code.claude.com/docs/en/mcp)
(fetched 2026-08-29), are specific:

- Type `/` and each MCP prompt is listed as **`/servername:promptname (MCP)`**.
  `/mcp__servername__promptname` also runs it. (`/mcp` connects/manages
  servers; the prompts show up in the normal `/` command menu.)
- No-argument prompt: `/mcp__github__list_prs`.
- Arguments are passed **positionally, space-separated, split on whitespace,
  one token each**: `/mcp__github__pr_review 456`,
  `/mcp__jira__create_issue login-bug high`. "Arguments are parsed based on
  the prompt's defined parameters." Results are "injected directly into the
  conversation."
- Server-name sanitization: any char outside `A-Za-z0-9_-` -> `_`; the prompt
  name is used as declared.

So the protocol's "named form fields" become **ordered positional tokens** in
Claude Code — a host-specific mapping worth writing down, same spirit as the
resource `@`-mention finding in `knowledge/mcp-resources.md`.

### Practitioner reception

- Widely called "the most underused primitive by far"; the consensus is that
  server authors should ship 3-4 prompts alongside their tools because they
  know good usage better than any user
  ([dev.to/aws-heroes, 2026](https://dev.to/aws-heroes/mcp-prompts-and-resources-the-primitives-youre-not-using-3oo1);
  [archestra.ai blog](https://archestra.ai/blog/mcp-tools-resources-prompts)).
- Dissent: [modelcontextprotocol Discussion #1779, "Replace MCP prompts with
  Skills or make prompts invokable by Agent"](https://github.com/modelcontextprotocol/modelcontextprotocol/discussions/1779)
  (2025-11-07) argues the user-only trigger model is a limitation — an agent
  that knows a task needs the `find-housing-assistance` prompt still can't
  invoke it — and overlaps the emerging "skills" pattern. Note for us: Claude
  Code has *merged* custom commands into skills
  (`code.claude.com/docs/en/skills`), so MCP prompts and local skills now land
  in the same `/` namespace (a synced skill whose name collides with an MCP
  prompt is skipped in favor of the other command).

## Build proposal

### Layer 1 — Intent

Add `examples/mcp-prompts/`: a minimal, self-contained MCP server that
exposes **one prompt with a required + an optional argument returning a single
user message**, and **one prompt returning a multi-message conversation with a
pre-filled `assistant` turn**, plus an offline self-test that drives it
through the SDK's in-memory `Client` and asserts the `prompts/list` /
`prompts/get` contract — completing the tools/resources/prompts trilogy that
`examples/mcp-resources-vs-tools/` left at two.

**Out of scope:** `list_changed` / subscriptions; `InputRequiredResult`
multi-round-trip argument collection; image / audio / embedded-resource
message content; server-side argument autocompletion (`completions`); wiring
into a live Claude Code host (that is `examples/mcp-connect-claude-code/`'s
job and costs a billed run — this example stays offline, no key, like every
other MCP example here). Do **not** modify `examples/mcp-resources-vs-tools/`
(keeps that reviewed example's diff at zero; cross-link instead).

### Layer 2 — Behavioral spec

**`server.py`** — `MCPServer("prompts-demo")`, two `@mcp.prompt()` functions,
`if __name__ == "__main__": mcp.run()` guard, no `print()` anywhere (stdout is
the wire), no I/O / globals in the function bodies (pure string / message-list
returns).

1. `review_code(code: <required>, language: str = "python")` returns
   `str` -> `f"Please review this {language} code:\n\n{code}"`. Use
   `Annotated[str, Field(description=...)]` on both params and
   `@mcp.prompt(title="Code review")`.
2. `debug_error(error_text: <required>)` returns `list[Message]`:
   `[UserMessage("I hit this error:"), UserMessage(error_text),
   AssistantMessage("Let's work through it. What did you expect to happen?")]`.

**`test_server.py`** — offline, in-memory `Client(mcp)`, plain `async test_*`
functions each printing `ok  <desc>`, run from `main() -> int`, final line
`All 7 self-tests passed.` (match `examples/mcp-hello-world/test_server.py`
exactly, incl. `from mcp import Client, MCPError`).

Acceptance criteria (each is one test):

- **AC1** `list_prompts()` returns exactly 2 prompts; names ==
  `{"review_code", "debug_error"}`.
- **AC2** In `review_code`'s `arguments`: the `code` entry has
  `required is True`; the `language` entry has `required is False` and carries
  its `description` string.
- **AC3** `get_prompt("review_code", {"code": "def f(): pass"})` -> exactly 1
  message; `role == "user"`; `content.type == "text"`; text contains
  `"def f(): pass"` **and** `"python"` (default applied).
- **AC4** `get_prompt("review_code", {"code": "...", "language": "rust"})` ->
  text contains `"rust"` and not `"python"` (optional-arg override).
- **AC5** `get_prompt("debug_error", {"error_text": "TypeError: x"})` ->
  exactly 3 messages; roles == `["user", "user", "assistant"]`; message[1]
  text == `"TypeError: x"` exactly; message[2] is the pre-filled assistant
  steering line.
- **AC6** `get_prompt("review_code", {})` (missing required `code`) raises
  `mcp.shared.exceptions.MCPError` with `exc.code == -32603`; the test also
  records whether `str(exc)` contains `"Missing required arguments"` /
  `"code"` (expected true via the in-memory dispatcher) — if the observed
  code or message differs, the builder updates this note + the README with
  what actually happened.
  **Built as:** code confirmed `-32603`. Message expectation corrected —
  `Client(mcp)` yields exactly `'Internal server error'`; the detailed
  message needs `Client(mcp, raise_exceptions=True)`. The test asserts both.
- **AC7** `get_prompt("no_such_prompt")` raises `MCPError` with
  `exc.code == -32603` and `"Unknown prompt"` in the message.
  **Built as:** code confirmed `-32603`; `"Unknown prompt"` is in the message
  only under `raise_exceptions=True` (default client: `'Internal server
  error'`). The test asserts both. README states
  both AC6 and AC7 diverge from the spec's `-32602`, and that resources in the
  same SDK get the spec-correct `-32602` (cross-link
  `examples/mcp-resources-vs-tools/`).

**`requirements.txt`** — `mcp>=2.0.0,<3` (matches the other MCP examples;
pydantic transitively). If a test only passes on >=2.1, bump and say so.

**`README.md`** — mirror `examples/mcp-resources-vs-tools/README.md`
structure: what's-here table; run instructions (`python3 -m venv .venv && ...`);
an **exact** expected-output block (7 `ok` lines + `All 7 self-tests passed.`)
so `examples/readme-transcript-check/` stays green; a "prompts are
user-controlled" section (spec framing); a "How Claude Code actually surfaces
this — from docs, 2026-08-29, not live-tested here" section (the
`/servername:promptname (MCP)` listing, `/mcp__server__prompt` form,
positional whitespace-split args, name sanitization, results injected into
the conversation; note the named-form -> positional-token mapping); a
"spec-vs-SDK error code" section (AC6/AC7); an "Explicitly out of scope" list;
a "Poke it manually" note (`.venv/bin/mcp dev server.py`, needs `npx`).

### Layer 3 — Interfaces

```python
# server.py
from typing import Annotated
from pydantic import Field
from mcp.server import MCPServer
from mcp.server.mcpserver.prompts.base import AssistantMessage, Message, UserMessage

mcp = MCPServer("prompts-demo")

@mcp.prompt(title="Code review")
def review_code(
    code: Annotated[str, Field(description="The code to review.")],
    language: Annotated[str, Field(description="Language the code is written in.")] = "python",
) -> str: ...

@mcp.prompt()
def debug_error(
    error_text: Annotated[str, Field(description="The error message / traceback.")],
) -> list[Message]: ...

if __name__ == "__main__":
    mcp.run()
```

```python
# test_server.py
import asyncio
from mcp import Client, MCPError
from server import mcp

async def test_list_prompts_returns_exactly_two() -> None: ...
async def test_review_code_argument_required_flags() -> None: ...
async def test_review_code_default_language_one_user_message() -> None: ...
async def test_review_code_language_override() -> None: ...
async def test_debug_error_seeds_three_messages_last_is_assistant() -> None: ...
async def test_missing_required_argument_raises_mcp_error_code() -> None: ...
async def test_unknown_prompt_raises_mcp_error_code() -> None: ...

def main() -> int: ...
if __name__ == "__main__":
    raise SystemExit(main())
```

### "It works" (self-test)

```
cd examples/mcp-prompts
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_server.py
# -> 7 "ok  ..." lines, then "All 7 self-tests passed.", exit 0
```

No API key, no network, no subprocess, no live host.

### Directory-name check

`examples/` on `main` has `mcp-connect-claude-code`, `mcp-hello-world`,
`mcp-hn-search`, `mcp-resources-vs-tools` — no `mcp-prompts`.
`gh pr list --state open` is empty; no `git branch -a` entry mentions
prompts. `examples/mcp-prompts/` is free.

## Open questions

- ~~**AC6/AC7 exact behavior** must be confirmed by running against the
  installed SDK~~ — **RESOLVED at build time** against mcp 2.1.1. `exc.code`
  is `-32603` for both, as predicted. The predicted *message* was wrong; see
  the Build-time correction box under "Spec-vs-reality gap #1".
- ~~`GetPromptResult` message attribute access (`.content.text` vs `.content`
  being a list)~~ — **RESOLVED at build time**: `.content` is a single
  `TextContent` block, so `.content.type` and `.content.text` are correct.
- Whether to also ship an optional, explicitly-billed
  `run_e2e.sh` (like `examples/mcp-connect-claude-code/`) that proves the
  `/mcp__prompts-demo__review_code` slash command works against a real
  `claude` CLI. Deferred here to keep the increment one-day and key-free;
  the Claude Code surfacing is documented from primary-source docs instead.
  A future backlog item could add the live check.
