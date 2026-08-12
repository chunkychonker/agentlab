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
- [ ] Streaming the hand-written tool loop: the same one-tool loop as
  `examples/minimal-agent-loop/`, but with `client.messages.stream()`, where a
  tool's input arrives as `input_json_delta` fragments that must be accumulated
  before the tool can be called at all. Increment: a pure event accumulator
  (event sequence in → assembled `tool_use` blocks out) under a thin streaming
  shell, tested offline by replaying a recorded event sequence — same
  inject-a-fake-client trick the loop example already uses, no key needed.
  Confirm the current event names against the `claude-api` skill first.

## Skills
- [done #7] Anatomy of a skill: a minimal model-invoked skill with a clear trigger
- [done #5, #10] A skill that shells out to a local script (like the recruiting scanner pattern)
- [done #6, #11] Packaging a skill with reference files the model loads on demand
- [ ] Verify the `${CLAUDE_SKILL_DIR}` + `allowed-tools` no-permission-prompt claim
  against the real Claude Code host. `knowledge/agent-skills.md` records it as
  documented but carrying a still-open bug report, "worth verifying live before
  relying on it", and `examples/skill-script-execution/` bundles exactly such a
  script. Increment: a scripted end-to-end run in the style of
  `examples/mcp-connect-claude-code/run_e2e.sh` — install the skill, invoke the
  CLI, assert on the `stream-json` transcript whether the bundled script ran
  unprompted — then correct the knowledge note with whatever actually happened.
  Costs one small billed run; state that in the README like that example does.

## MCP
- [done #8] Hello-world MCP server (stdio) exposing one tool
- [done #9] MCP server wrapping a public REST API (e.g. Hacker News Algolia)
- [done #19] Connecting a custom MCP server to Claude Code and calling it end-to-end
- [done #20] MCP resources vs tools: when to use which
- [ ] MCP prompts, the third primitive: `examples/mcp-resources-vs-tools/` sorts
  model-driven from application-driven and leaves user-driven prompts out
  entirely. Increment: add a prompt to a server (arguments included), test
  list/get through the in-memory `Client` the way
  `examples/mcp-hello-world/test_server.py` does, and check how Claude Code
  actually surfaces it rather than trusting the spec — the same discipline as
  `knowledge/claude-code-mcp-connection.md`, which found the host's real
  behaviour differed from the docs.

## Coding agents (deferred, was next before Skills/MCP got prioritized 2026-07-29)
- [done #23] Tool-use error handling and retries done well

## Health-check findings (2026-08-10, `logs/last-health.md`)
Both are small; a builder can reasonably take them in one cycle.
- [done #24] `examples/typed-tool-registry/README.md` claims "All 4 self-tests passed" but the
  suite emits "All 6" — the `run_agent` text-join and max-iterations `RuntimeError`
  checks are uncounted. Wrong since PR #2 landed; each night's reviewer only sees
  that day's diff, so nothing catches it. Fix the count, and check whether the
  README should enumerate the cases so the next drift is visible.
- [done #24] `examples/tool-error-policy/agent.py` (this line said `policy.py`; the
  function is in `agent.py:187`) — `call_tool_with_retry`'s docstring
  promises a `ValueError` for `max_attempts < 1`, but that raise is unreachable:
  `range(1, 1)` is empty, so the caller gets an `AssertionError` from unrelated
  code instead. Validate at the boundary per Protocol §4; start with a failing
  test per §6. Caught by the reviewer on 2026-08-10 and merged anyway.

## Context & cost
- [done #25] Previewing server-side context editing (`clear_tool_uses_20250919`) for
  $0 with the free token-counting endpoint: a pure policy type that serialises
  the `context_management` edit, and a shell that counts the same tool-heavy
  transcript twice (plain vs. edited) to report the real token saving before
  spending a cent on generation. Filed by the researcher on 2026-08-11 because
  the backlog was drained by cycle 1 and replenishment only runs after the last
  cycle — see `research/2026-08-11-context-editing-preview.md`.
- [building] Server-side compaction (`compact_20260112`, beta `compact-2026-01-12`): the
  summarize-don't-prune sibling of context editing. Different response shape
  (compaction blocks, `pause_after_compaction`) and the quality-degradation
  criticism practitioners aim at it. Deliberately split out of the context-editing
  cycle above; do that one first.
- [done #26] Fix the replenishment ordering gap in `run.sh`: top the backlog up *before*
  the cycle loop when unclaimed items are fewer than the night's draw, not only
  after it. Today the last cycle of every drain-the-backlog night finds nothing
  to claim and has to file its own work.

- [ ] Prompt caching across a long tool loop: where the `cache_control` breakpoints
  go in a message list that grows every turn, and proving the saving instead of
  assuming it. Increment: a pure placement policy (message list in → list with
  breakpoints out, respecting the documented cap — 4 at last check, confirm
  against the `claude-api` skill rather than this line) plus a small runner that
  reports `cache_creation_input_tokens` vs `cache_read_input_tokens` over two
  turns. Direct sequel to the context-editing work: `knowledge/context-editing.md`
  already notes that clearing tool results invalidates the cache below the edit,
  and nothing in the lab measures that conflict. Unlike #25 this cannot be
  previewed for $0 — `count_tokens` reports no cache fields — so the runner needs
  one cheap real generation; say so plainly in the README.

## Pipeline & repo hygiene
- [ ] Teach the health check to run
  `examples/readme-transcript-check/check_transcript.py` over every example
  README instead of spot-checking transcripts by hand — the follow-up the
  2026-08-11 note deferred because a repo-wide sweep needs per-example venvs,
  which the health check already builds. Two things to settle inside the cycle:
  an explicit opt-out marker for blocks that cannot be reproduced offline
  (`mcp-connect-claude-code` has two, one billed and live, and the checker
  currently refuses with `AmbiguousTranscript`), and whether the other
  transcripts are in fact deterministic — only two were ever verified. Stays
  report-only; the health check never fixes and never blocks.
- [ ] Reconcile orphaned backlog claims left behind by a failed cycle.
  `knowledge/pipeline-claim-lifecycle.md` documents it as failure 1:
  `snapshot_dirty_main` + `reset_to_clean_main` carry finished work off to a
  `cycle/<date>-unshipped-*` branch and restore a `BACKLOG.md` where the item
  reads `[ ]` again, so the next researcher rebuilds the same topic from
  scratch — and the researcher's `gh pr list` guard cannot see it, because a
  snapshot branch is not a PR. There is a live instance in this repo right now:
  `cycle/2026-08-12-unshipped-213702-1` holds a built server-side-compaction
  increment while its backlog item sits unclaimed above. Increment: a sourceable
  reconciler in `.pipeline/` that maps unshipped branches to the claims they
  hold and surfaces them before the loop claims anything, with an offline test
  in the style of `.pipeline/test_backlog.sh` (injected side effects, bash 3.2,
  no network, no key).

## Notes
- Prefer the latest Claude models and the current Anthropic SDK. Check the
  `claude-api` skill before writing any API code — do not guess model ids or params.
- Every build must actually run. No stubs, no placeholder TODOs left behind.
