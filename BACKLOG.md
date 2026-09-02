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
- [done #32] Streaming the hand-written tool loop: the same one-tool loop as
  `examples/minimal-agent-loop/`, but with `client.messages.stream()`, where a
  tool's input arrives as `input_json_delta` fragments that must be accumulated
  before the tool can be called at all. Increment: a pure event accumulator
  (event sequence in → assembled `tool_use` blocks out) under a thin streaming
  shell, tested offline by replaying a recorded event sequence — same
  inject-a-fake-client trick the loop example already uses, no key needed.
  Confirm the current event names against the `claude-api` skill first.
- [building] Parallel specialist execution in the orchestrator. `examples/orchestrator-subagents/`
  runs its `run_specialist` calls strictly one after another and its README names
  concurrent fan-out the "natural next increment". Increment: dispatch the
  independent subtasks of a `Plan` at once (`concurrent.futures.ThreadPoolExecutor`,
  or `asyncio.gather` if the shell goes async), with `plan_task` and `synthesize`
  unchanged and results reassembled in plan order rather than completion order.
  Test offline in the style of `examples/orchestrator-subagents/test_agent.py`: a
  fake client that blocks on a latch so the test can assert the specialist calls
  overlap, plus a second assertion that the synthesised answer does not depend on
  which finished first. No key for the test. Worth a README note on the
  `[[prompt-caching]]` cache-killer that parallel calls sharing a prefix each pay
  the full cache write.
- [ ] `thinking` blocks in the streaming accumulator. `examples/streaming-tool-loop/`
  lists them as explicitly out of scope: `accumulate()` raises on a
  `content_block_start` for a `thinking` block instead of assembling it. But a
  tool loop with extended thinking on must echo those blocks back verbatim,
  signature included, or the next turn is a 400. Increment: extend the pure
  accumulator to build `thinking` blocks from their delta + signature-delta
  events alongside `text` and `tool_use`, keep them in `content` in wire shape,
  and leave the loud-failure contract intact for genuinely malformed sequences.
  Test offline by replaying a recorded thinking+tool_use event list through
  `accumulate()` — the same trick `examples/streaming-tool-loop/test_agent.py`
  already uses, no key. Confirm the delta event names against the `claude-api`
  skill first, as the #32 item did for the base events.
- [ ] `strict: true` tool schemas as prevention rather than cure.
  `knowledge/tool-failure-taxonomy.md` records strict schema-constrained sampling
  as removing "one whole error class", and both `examples/typed-tool-registry/`
  and `examples/tool-error-policy/` push it out of scope. Increment: a
  typed-tool-registry-shaped example whose tool schemas set the strict flag,
  showing the model can no longer emit an input that fails Pydantic validation —
  the `.call(...)` `ValueError` path `test_agent.py` exercises becomes
  unreachable from the model side. Offline part: assert the emitted schema
  carries the strict marker and is otherwise well-formed (pure, no key, like the
  existing schema-shape checks). Live part: one cheap run contrasting a strict
  and a non-strict registry on an input the loose one fumbles. Confirm the exact
  field and beta-header name against the `claude-api` skill before building; do
  not guess it.

## Skills
- [done #7] Anatomy of a skill: a minimal model-invoked skill with a clear trigger
- [done #5, #10] A skill that shells out to a local script (like the recruiting scanner pattern)
- [done #6, #11] Packaging a skill with reference files the model loads on demand
- [done #33] Verify the `${CLAUDE_SKILL_DIR}` + `allowed-tools` no-permission-prompt claim
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
- [stranded cycle/2026-08-29-unshipped-120828-1] MCP prompts, the third primitive: `examples/mcp-resources-vs-tools/` sorts
  model-driven from application-driven and leaves user-driven prompts out
  entirely. Increment: add a prompt to a server (arguments included), test
  list/get through the in-memory `Client` the way
  `examples/mcp-hello-world/test_server.py` does, and check how Claude Code
  actually surfaces it rather than trusting the spec — the same discipline as
  `knowledge/claude-code-mcp-connection.md`, which found the host's real
  behaviour differed from the docs.
- [ ] MCP resources through the real Claude Code host, not the in-memory `Client`.
  `examples/mcp-resources-vs-tools/` proves the protocol-level contract offline
  and explicitly defers the live `@`-mention flow to "PR #19's territory";
  `knowledge/mcp-resources.md` describes how the host surfaces resources
  (`@`-mention plus synthetic list/read tools) only "against current docs", never
  verified live. Increment: a scripted end-to-end run in the style of
  `examples/mcp-connect-claude-code/run_e2e.sh` — register the `notes` server,
  drive the `claude` CLI with `--bare --strict-mcp-config --mcp-config ...
  --output-format stream-json --verbose`, `@`-mention `notes://index`, and assert
  from the transcript whether the host emits synthetic resource list/read tools
  and under what `mcp__` names — then correct `knowledge/mcp-resources.md` with
  what actually happened, the docs-vs-reality discipline
  `knowledge/claude-code-mcp-connection.md` already applied. Costs one small
  billed run; state that in the README like `examples/mcp-connect-claude-code/` does.

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
- [done #27] Server-side compaction (`compact_20260112`, beta `compact-2026-01-12`): the
  summarize-don't-prune sibling of context editing. Different response shape
  (compaction blocks, `pause_after_compaction`) and the quality-degradation
  criticism practitioners aim at it. Deliberately split out of the context-editing
  cycle above; do that one first.
- [done #26] Fix the replenishment ordering gap in `run.sh`: top the backlog up *before*
  the cycle loop when unclaimed items are fewer than the night's draw, not only
  after it. Today the last cycle of every drain-the-backlog night finds nothing
  to claim and has to file its own work.

- [done #35] Prompt caching across a long tool loop: where the `cache_control` breakpoints
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
- [ ] Measure the context-editing vs prompt-caching trade — nothing in the lab does
  yet. `examples/prompt-caching-tool-loop/` and `knowledge/context-editing.md`
  both flag it: `clear_tool_uses_20250919` invalidates every cache breakpoint
  below the edit, so a long loop running both trades a smaller prompt against a
  colder cache. Increment: compose the pure `place_breakpoints` policy from
  `examples/prompt-caching-tool-loop/placement.py` with the
  `clear_tool_uses_20250919` edit from `examples/context-editing-preview/policy.py`
  over one growing tool loop, and report `cache_creation_input_tokens` /
  `cache_read_input_tokens` / `input_tokens` across the turn the edit fires: the
  net of tokens the clear removes against tokens re-billed as a fresh cache write.
  Offline test over the composed policy (pure, no key). The tokens the clear
  removes can be previewed for $0 with `count_tokens` as in #25; the cache-write
  cost that same clear incurs cannot (#35), so the net still needs one cheap real
  generation — say so in the README.
- [ ] Previewing `clear_thinking_20251015` for $0, the sibling edit
  `examples/context-editing-preview/` names as out of scope. Increment: a second
  pure policy type beside `ClearToolUsesPolicy` in `policy.py` that serialises the
  `clear_thinking_20251015` `context_management` edit, plus a shell that counts a
  thinking-heavy transcript twice — plain vs edited — to report the tokens dropped
  by clearing reasoning blocks. The same $0 `count_tokens` discipline as #25,
  which already showed the endpoint applies these edits without a billed call.
  Offline self-test in the style of
  `examples/context-editing-preview/test_preview.py`: pure serialisation plus a
  synthetic thinking transcript, no key, no network. Check the field shapes
  against `knowledge/context-editing.md` and the `claude-api` skill first, and
  note whether `count_tokens` needs real thinking blocks in the input or accepts
  synthetic ones.

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
- [done #28] Reconcile orphaned backlog claims left behind by a failed cycle.
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
