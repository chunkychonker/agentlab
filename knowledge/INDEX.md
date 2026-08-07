# Knowledge index

Map of the knowledge base. The researcher keeps this current as notes are added.

## Coding agents
- [[tool-use-loop]] — the hand-written Messages API tool loop + gotchas
- [[typed-tool-registry]] — `@beta_tool` + `client.beta.messages.tool_runner`: typed schemas, validation, registry pattern
- [[orchestrator-workers]] — fan-out-to-specialists pattern; `client.messages.parse(output_format=...)` for the plan step; the three distinct "subagent" products and when each applies
- [[anthropic-python-sdk]] — SDK basics: client, `messages.create`, response shape
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

## Cross-cutting patterns & gotchas
- Testing agent loops offline: inject a fake client (see [[tool-use-loop]])
- Testing typed tools offline: schema generation + Pydantic validation are pure
  local code, no fake client needed (see [[typed-tool-registry]])
- Testing MCP servers offline: connect the SDK's in-memory `Client` straight
  to the server object, no subprocess/host needed (see [[mcp-python-sdk]]);
  for tools that do real outbound HTTP, that's only enough to test
  registration/schema — test the I/O-doing function itself by injecting an
  `httpx.MockTransport`-backed client (see [[mcp-python-sdk]], [[hn-algolia-api]])
