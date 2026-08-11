# Backlog

The researcher pulls the top unclaimed item each cycle. Keep items small enough
to research and build in a single day. Newest ideas at the bottom; the pipeline
works top-down. Mark `[researching]`, `[building]`, `[done <PR#>]` as it moves.

## Coding agents
- [done #17] Eval harness for the reviewer agent: fixture-based regression tests (known-bug
  increment + assert-on-verdict runner), seeded with the self-reference and
  subdirectory-link regex bugs caught in review on 2026-08-07 — proves the
  reviewer still catches bugs it's already caught once, after any prompt change
  (marked done 2026-08-09: merged to main per `git log`, backlog entry was stale)
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
- [done #20] MCP resources vs tools: when to use which

## Coding agents (deferred, was next before Skills/MCP got prioritized 2026-07-29)
- [done #23] Tool-use error handling and retries done well

## Health-check findings (2026-08-10, `logs/last-health.md`)
Both are small; a builder can reasonably take them in one cycle.
- `examples/typed-tool-registry/README.md` claims "All 4 self-tests passed" but the
  suite emits "All 6" — the `run_agent` text-join and max-iterations `RuntimeError`
  checks are uncounted. Wrong since PR #2 landed; each night's reviewer only sees
  that day's diff, so nothing catches it. Fix the count, and check whether the
  README should enumerate the cases so the next drift is visible.
- `examples/tool-error-policy/policy.py` — `call_tool_with_retry`'s docstring
  promises a `ValueError` for `max_attempts < 1`, but that raise is unreachable:
  `range(1, 1)` is empty, so the caller gets an `AssertionError` from unrelated
  code instead. Validate at the boundary per Protocol §4; start with a failing
  test per §6. Caught by the reviewer on 2026-08-10 and merged anyway.

## Notes
- Prefer the latest Claude models and the current Anthropic SDK. Check the
  `claude-api` skill before writing any API code — do not guess model ids or params.
- Every build must actually run. No stubs, no placeholder TODOs left behind.
