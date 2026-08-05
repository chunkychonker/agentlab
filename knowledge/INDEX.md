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
  trigger contract, Claude-Code-vs-API skill differences, and what is/isn't
  offline-testable

## MCP
_(no notes yet)_

## Cross-cutting patterns & gotchas
- Testing agent loops offline: inject a fake client (see [[tool-use-loop]])
- Testing typed tools offline: schema generation + Pydantic validation are pure
  local code, no fake client needed (see [[typed-tool-registry]])
