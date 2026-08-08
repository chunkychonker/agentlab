# Backlog

The researcher pulls the top unclaimed item each cycle. Keep items small enough
to research and build in a single day. Newest ideas at the bottom; the pipeline
works top-down. Mark `[researching]`, `[building]`, `[done <PR#>]` as it moves.

## Coding agents
- [ ] Eval harness for the reviewer agent: fixture-based regression tests (known-bug
  increment + assert-on-verdict runner), seeded with the self-reference and
  subdirectory-link regex bugs caught in review on 2026-08-07 — proves the
  reviewer still catches bugs it's already caught once, after any prompt change
- [done #1] Minimal agent loop from scratch (Anthropic SDK): one tool, manual tool-use loop
- [done #2] Multi-tool agent with a typed tool registry
- [done #4] Subagent delegation: an orchestrator that fans out to specialist agents

## Skills
- [done #7] Anatomy of a skill: a minimal model-invoked skill with a clear trigger
- [done #5, #10] A skill that shells out to a local script (like the recruiting scanner pattern)
- [done #6, #11] Packaging a skill with reference files the model loads on demand

## MCP
- [done #8] Hello-world MCP server (stdio) exposing one tool
- [done #9] MCP server wrapping a public REST API (e.g. Hacker News Algolia)
- [done #19] Connecting a custom MCP server to Claude Code and calling it end-to-end
- [ ] MCP resources vs tools: when to use which

## Coding agents (deferred, was next before Skills/MCP got prioritized 2026-07-29)
- [ ] Tool-use error handling and retries done well

## Notes
- Prefer the latest Claude models and the current Anthropic SDK. Check the
  `claude-api` skill before writing any API code — do not guess model ids or params.
- Every build must actually run. No stubs, no placeholder TODOs left behind.
