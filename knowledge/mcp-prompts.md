# MCP prompts (the third primitive)

The primitive `examples/mcp-resources-vs-tools/` left out. Verified 2026-08-29
against the spec (protocol version `2026-07-28`), the `mcp` Python SDK
`main` branch (repo pushed 2026-08-28, latest release v2.1.1 2026-08-25), and
Claude Code's current docs — not blog summaries.

## Control model

- **Tools** are model-driven, **resources** are application/host-driven,
  **prompts** are **user-controlled**: the server authors named message
  templates; the *user* picks one from a menu and fills in arguments; the
  rendered messages enter the conversation as if typed. "Who decides *when*",
  not "who authors the content." See [[mcp-resources]] for the tools/resources
  half of this split.
- Two RPCs: `prompts/list` (metadata) and `prompts/get` (`{name, arguments}`
  -> `{description, messages[], resultType}`).
- **Arguments are a flat list of named strings** — `{name, description?,
  required}`, no JSON Schema. "A form a person fills in, not a payload a model
  constructs." A `PromptMessage` is `{role: "user"|"assistant", content:
  <single block>}` (not an array like Anthropic messages). Pre-filling an
  `assistant` message is the documented way to steer the model's next reply.

## Python SDK: `@mcp.prompt()` is `@mcp.tool()` with two differences

Same decorator shape as [[mcp-python-sdk]]'s tools: name from the function,
description from the docstring, arguments from the parameters,
`@mcp.prompt(title=..., name=...)` + `Annotated[str, Field(description=...)]`
per argument. The only changes: **who triggers it** (the user) and **where
the result goes** (into the conversation).

- **A parameter without a default is required; with a default, optional.**
  (No schema — the required/optional bit is the whole contract.)
- **Return type decides the shape:** a `str` -> **one `user` message**; a
  `list[Message]` -> a seeded multi-turn conversation. `UserMessage` /
  `AssistantMessage` / `Message` (the base / return annotation) come from
  `mcp.server.mcpserver.prompts.base`; they wrap a bare `str` in `TextContent`.
- Client side: `await client.list_prompts()` -> `.prompts`;
  `await client.get_prompt(name, arguments)` -> `GetPromptResult(.description,
  .messages)`, each `.messages[i]` has `.role` and `.content` (single block:
  `.content.type`, `.content.text`). Testable offline through the in-memory
  `Client(mcp)` — same seam as every other MCP example in the repo.
- Runtime menu changes: `mcp.add_prompt(Prompt.from_function(...))` /
  `mcp.remove_prompt(name)` then `await ctx.notify_prompts_changed()`.

## Gotcha: the missing-required-argument error code contradicts the spec

Spec says: invalid prompt name **and** missing required arguments -> `-32602`
(Invalid params). The SDK does **not** do this:

- `Prompt.render()` raises `ValueError("Missing required arguments: {...}")`
  *before* calling your function; an unknown name raises
  `ValueError("Unknown prompt: X")`. Both are re-wrapped by
  `mcpserver.get_prompt` as a generic `ValueError`, which the in-memory
  `DirectDispatcher` turns into `MCPError(code=INTERNAL_ERROR = -32603)`.
- So via the SDK you get **`-32603`, not `-32602`**, for both cases. The
  SDK's own `docs/servers/prompts.md` states the `-32603` behavior explicitly.
- **Same SDK, opposite choice for resources:** a `ResourceNotFoundError` is
  special-cased to the spec-correct `-32602` (see [[mcp-resources]]). The two
  primitives are internally inconsistent on the "bad input identifier" code.
- There is **no tool-style non-raising error result** for prompts — "no model
  is in the loop", so `prompts/get` *raises* on the client. (Contrast the
  tool `is_error=True`, no-raise contract in [[mcp-python-sdk]].)
- Message caveat: over stdio/HTTP the client sees a sanitized
  `"Internal server error"` (reason is log-only). The **in-memory `Client`**
  defaults to `raise_handler_exceptions=True`, so there `str(exc)` keeps the
  descriptive `"Missing required arguments: {'code'}"`.

## How Claude Code actually surfaces prompts (docs, 2026-08-29)

The spec mandates no UI model. Claude Code
([`code.claude.com/docs/en/mcp` -> "Use MCP prompts as commands"]):

- Type `/`; each MCP prompt is listed as **`/servername:promptname (MCP)`**.
  `/mcp__servername__promptname` also runs it.
- **Arguments are positional, space-separated, whitespace-split, one token
  each** — `/mcp__github__pr_review 456`,
  `/mcp__jira__create_issue login-bug high`. The protocol's *named form
  fields* become *ordered positional tokens* at the host. Results are
  "injected directly into the conversation."
- Server-name sanitization: any char outside `A-Za-z0-9_-` -> `_`; prompt
  name used as declared.
- Claude Code has **merged custom commands into skills**, so MCP prompts and
  local/synced skills share the `/` namespace; a synced skill whose name
  collides with an MCP prompt is skipped in favor of the other command.

Same lesson as [[claude-code-mcp-connection]]: the host's real behavior is
more specific than (and here, diverges from) the spec — check the host's own
docs, not just the protocol.

## Practitioner view

Widely called "the most underused primitive by far"; advice is for server
authors to ship 3-4 prompts next to their tools. Dissent
([modelcontextprotocol Discussion #1779, 2025-11-07]): the user-only trigger
model means an agent that knows a task needs a given prompt still can't
invoke it, and the primitive overlaps the emerging "skills" pattern (see
[[agent-skills]]).

Research note: [2026-08-29-mcp-prompts](../research/2026-08-29-mcp-prompts.md).
Example: `examples/mcp-prompts/` (planned).

Related: [[mcp-resources]] (tools vs resources — the other two-thirds of the
split, and the primitive that gets the spec-correct `-32602`),
[[mcp-python-sdk]] (the `@mcp.tool()` mechanics `@mcp.prompt()` mirrors, and
the tool no-raise error contract prompts do *not* share),
[[claude-code-mcp-connection]] (host-vs-spec divergence, same theme),
[[agent-skills]] (the pattern Discussion #1779 wants to fold prompts into).
