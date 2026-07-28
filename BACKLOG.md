# Backlog

The researcher pulls the top unclaimed item each cycle. Keep items small enough
to research and build in a single day. Newest ideas at the bottom; the pipeline
works top-down. Mark `[researching]`, `[building]`, `[done <PR#>]` as it moves.

## Coding agents
- [building] Minimal agent loop from scratch (Anthropic SDK): one tool, manual tool-use loop
- [building] Multi-tool agent with a typed tool registry
- [ ] Subagent delegation: an orchestrator that fans out to specialist agents
- [ ] Adding a lightweight eval harness to score an agent's outputs
- [ ] Tool-use error handling and retries done well

## Skills
- [ ] Anatomy of a skill: a minimal model-invoked skill with a clear trigger
- [ ] A skill that shells out to a local script (like the recruiting scanner pattern)
- [ ] Packaging a skill with reference files the model loads on demand

## MCP
- [ ] Hello-world MCP server (stdio) exposing one tool
- [ ] MCP server wrapping a public REST API (e.g. Hacker News Algolia)
- [ ] Connecting a custom MCP server to Claude Code and calling it end-to-end
- [ ] MCP resources vs tools: when to use which

## Notes
- Prefer the latest Claude models and the current Anthropic SDK. Check the
  `claude-api` skill before writing any API code — do not guess model ids or params.
- Every build must actually run. No stubs, no placeholder TODOs left behind.
