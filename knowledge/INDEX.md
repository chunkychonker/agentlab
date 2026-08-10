# Knowledge index

Map of the knowledge base. The researcher keeps this current as notes are added.

## Coding agents
- [[tool-use-loop]] — the hand-written Messages API tool loop + gotchas
- [[typed-tool-registry]] — `@beta_tool` + `client.beta.messages.tool_runner`: typed schemas, validation, registry pattern
- [[orchestrator-workers]] — fan-out-to-specialists pattern; `client.messages.parse(output_format=...)` for the plan step; the three distinct "subagent" products and when each applies
- [[tool-failure-taxonomy]] — the three dispositions for a failing tool call
  (retry locally / report to the model with `is_error` / abort loudly), the
  `is_error` wire contract and what a good error message looks like, and
  `strict: true` as prevention for one whole error class
- [[anthropic-python-sdk]] — SDK basics: client, `messages.create`, response shape
- [[sdk-retry-behavior]] — the *other* retry layer: source-verified SDK transport
  retry defaults, which statuses retry, `retry-after` handling and its ≤60s
  clamp, the wrong jitter comment, and the status → exception table
- [[anthropic-models]] — current model IDs and prices (re-check, don't guess)

## Skills
- [[agent-skills]] — `SKILL.md` anatomy: progressive disclosure (3 load
  tiers), the documented/checkable frontmatter rules, the model-invocation
  trigger contract, Claude-Code-vs-API skill differences, what is/isn't
  offline-testable, the `${CLAUDE_SKILL_DIR}` + `allowed-tools` mechanism
  for a bundled script to run without a permission prompt (v2.1.129+, and a
  still-open bug report worth verifying live before relying on it), and
  Level-3 reference files: the one-level-deep rule, TOC-for->100-lines as a
  soft/inconsistently-followed recommendation (verified against Anthropic's
  own shipped `pdf` skill), and the two reference-syntax styles seen in the
  wild (markdown links vs. bare filename mentions)

## MCP
- [[mcp-python-sdk]] — v2 `MCPServer`/`Client` API (v1's `FastMCP` import is
  gone), stdio-as-default-transport, the stdout-is-the-wire gotcha, in-memory
  `Client` testing, the tool-error-vs-protocol-error failure model, and
  `async def` tools + injecting `httpx.MockTransport` to test I/O-doing
  tools offline
- [[hn-algolia-api]] — live-verified endpoint/param/response reference for
  the Hacker News Algolia Search API, and its real gotcha: error bodies are
  sometimes HTML not JSON
- [[claude-code-mcp-connection]] — connecting a server to the real Claude
  Code host (not the SDK's in-memory `Client`): the three registration
  scopes, the `.mcp.json` pending-approval workflow and a settings-file
  reliability gap verified live in this repo, and the
  `--bare --strict-mcp-config --mcp-config ... --output-format
  stream-json --verbose` recipe for a scriptable end-to-end check,
  including the `mcp__<server>__<tool>` naming convention and the
  `stream-json` event shapes to assert on
- [[mcp-resources]] — resources vs tools: application-driven vs
  model-driven, separate list/read RPCs + resource templates, "listing never
  executes the function" (verified against SDK source), the opposite
  failure shape (resources raise `MCPError` on the client, tools don't), and
  how Claude Code actually surfaces resources today (`@`-mention +
  synthetic list/read tools, verified against current docs, not stale
  GitHub issues)

## Cross-cutting patterns & gotchas
- Testing agent loops offline: inject a fake client (see [[tool-use-loop]])
- Testing retry/backoff logic offline: inject `sleep` and `jitter` as parameters
  and keep the policy a pure function, so the test is instant and deterministic
  (see [[tool-failure-taxonomy]])
- Testing typed tools offline: schema generation + Pydantic validation are pure
  local code, no fake client needed (see [[typed-tool-registry]])
- Testing MCP servers offline: connect the SDK's in-memory `Client` straight
  to the server object, no subprocess/host needed (see [[mcp-python-sdk]]);
  for tools that do real outbound HTTP, that's only enough to test
  registration/schema — test the I/O-doing function itself by injecting an
  `httpx.MockTransport`-backed client (see [[mcp-python-sdk]], [[hn-algolia-api]])
- Testing an MCP server against the *real* Claude Code host (not the SDK
  client): the in-memory `Client` above can't prove this — it needs a real,
  billed `claude` CLI invocation with `--mcp-config`/`--strict-mcp-config`/
  `--bare` and parsing the `stream-json` transcript (see
  [[claude-code-mcp-connection]])
