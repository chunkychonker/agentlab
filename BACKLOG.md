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
- [done #37] Parallel specialist execution in the orchestrator. `examples/orchestrator-subagents/`
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
- [done #38] `thinking` blocks in the streaming accumulator. `examples/streaming-tool-loop/`
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
- [done #46] `strict: true` tool schemas as prevention rather than cure.
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
- [done #45] MCP prompts, the third primitive: `examples/mcp-resources-vs-tools/` sorts
  model-driven from application-driven and leaves user-driven prompts out
  entirely. Increment: add a prompt to a server (arguments included), test
  list/get through the in-memory `Client` the way
  `examples/mcp-hello-world/test_server.py` does, and check how Claude Code
  actually surfaces it rather than trusting the spec — the same discipline as
  `knowledge/claude-code-mcp-connection.md`, which found the host's real
  behaviour differed from the docs.
- [stranded cycle/2026-09-05-unshipped-133345-1] MCP resources through the real Claude Code host, not the in-memory `Client`.
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
- [done #40] Measure the context-editing vs prompt-caching trade — nothing in the lab does
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
- [done #47] Previewing `clear_thinking_20251015` for $0, the sibling edit
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
- [done #48] 1-hour cache TTL support in `examples/prompt-caching-tool-loop/`.
  Both `placement.py`'s module docstring and the README footnote name the
  1-hour TTL (`{"type": "ephemeral", "ttl": "1h"}`, a 2x write multiplier
  instead of 1.25x) as explicitly out of scope, and PR #40's own research note
  names the same thing out of scope for the context-editing-tradeoff work, so
  it's genuinely untouched. Filed by the researcher on 2026-09-16 because the
  Coding agents / Skills / MCP sections above had no plain `[ ]` item left —
  every remaining entry there is `[done #N]` or a `[stranded cycle/...]` claim
  that a `git diff` against its branch showed was already fully researched
  and built, just unshipped by a session-limit-killed cycle (see
  `logs/last-health.md`). Increment: add a `ttl` keyword-only parameter to
  `place_breakpoints` (default unchanged, byte-identical marker); price the
  2x-vs-1.25x write with a `write_multiplier` field on `report.Saving` instead
  of the hardcoded constant; read `usage.cache_creation`'s nested
  `ephemeral_5m_input_tokens`/`ephemeral_1h_input_tokens` to *prove* a
  `ttl="1h"` request actually billed at 1h rather than assuming the request
  shape was enough. Offline tests extend the existing 23+17 assertions in
  `test_placement.py`/`test_report.py` (no key, no network); one cheap live
  run captures a second dated transcript next to the existing 2026-08-31 one
  so the README shows both real TTL tradeoffs side by side. See
  `research/2026-09-16-prompt-caching-1h-ttl.md`.

## Pipeline & repo hygiene
- [done #41] Teach the health check to run
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

## Health-check findings
Filed automatically by `.pipeline/run.sh` from `logs/last-health.md`.
Full detail is in the dated `logs/lab-health-*.log` for that date.
- [done #44] fix (health 2026-09-02): run-2026-08-29_114701 — no "=== done"; log stops mid "cycle 2/2: review" with no verdict and no "Aborting." line. Cycle 1 was a clean review FAIL (mcp-prompts knowledge note not reconciled with the build); cycle 2 research h...
- [stranded cycle/2026-09-17-unshipped-024123-1] fix (health 2026-09-02): run-2026-08-30_114704 — "main has uncommitted changes to tracked files (likely a FAILed cycle awaiting a manual fix) — resolve manually before the next run. Aborting." Aborted at preflight; downstream of run-2026-08-29's t...
- [done #49] fix (health 2026-09-02): run-2026-08-31_114702 — "NETWORK UNREACHABLE (api.anthropic.com / github.com) — check VPN. Aborting." Sole line in the log; first occurrence since the PR #31 probe fix.
- [stranded cycle/2026-09-19-unshipped-021911-1] fix (health 2026-09-02): run-2026-09-01_134705 — shipped 0/2. Cycle 1 clean VERDICT: FAIL (no increment: researcher killed by the 600s ceiling, builder correctly refused to build). Cycle 2 verdict MISSING (same 600s researcher kill; review phase wro...
- [building] fix (health 2026-09-02): "Background tasks still running after 600s; terminating. Set CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS=0 to wait indefinitely." — 2 nights: 2026-08-29 (cycle 2 research) and 2026-09-01 (cycle 1 research, cycle 2 research, and the pipeline-observer phase — 3 occurrences that night). Direct cause of run-2026-09-01 shipping 0/2 a... (researcher 2026-09-21: the named cause is already fixed on main by direct commit 832134b, 2026-09-20 — no PR, so `reconcile_shipped_claim` cannot ever close this line; see research/2026-09-21-backlog-direct-commit-reconcile.md, which spends this claim on that reconciliation gap instead — Failure 4 in knowledge/pipeline-claim-lifecycle.md)
- [done #42] fix (health 2026-09-02): BACKLOG.md:24 "Parallel specialist execution in the orchestrator" marked [building], shipped in PR #37 (merged 2026-09-02, branch cycle/2026-09-02-parallel-specialist-execution) — never advanced to [done #37]. Both maintain/auto-merge and the post-loop reconcile for that cycle have already run, so no remaining pipeline step will correct it. (researcher 2026-09-10: one increment resolves this and the PR #38 line below — see research/2026-09-10-backlog-mark-done-reconcile.md)
- [done #42] fix (health 2026-09-02): BACKLOG.md:36 "thinking blocks in the streaming accumulator" marked [building], shipped in PR #38 (merged 2026-09-02, branch cycle/2026-09-02-streaming-thinking-accumulator) — never advanced to [done #38]. Same as PR #37: both of tonight's cycles shipped without a mark-done, the 2026-08-16 PR #33 failure mode recurring on both cycles. (builder 2026-09-10: subject resolved by the increment claimed above — BACKLOG.md:36 now reads [done #38] and `reconcile_shipped_claim` makes the transition automatic. Left unclaimed rather than pre-marked with a PR number that does not exist yet; close it on sight.)
- [ ] fix (health 2026-09-02): no run log for 2026-08-17 through 2026-08-28 — 12 consecutive scheduled nights with no logs/run-*.log present. logs/pause-resume.log records a manual pause/resume dated 2026-08-18/19; whether the nightly job ran on any of these dates cannot be det...
- [stranded cycle/2026-09-15-unshipped-023258-2] fix (health 2026-09-02): .pipeline/strays/2026-08-31-manual/ — 1 file (HANDOFF-2026-08-31.md, ~15 KB), ~2 days old (dir mtime 2026-08-31 03:43). Hand-named, not preflight's bare-timestamp format, so parked here manually; nothing ever revisits .pipeline/strays/.
- [stranded cycle/2026-09-14-unshipped-021453-1] fix (health 2026-09-13): BACKLOG.md:216 is still marked [building] ("fix (health 2026-09-02): BACKLOG.md:24 ... never advanced to [done #37]") but the work shipped and merged as PR #42 (cycle/2026-09-10-backlog-mark-done-reconcile, merged 2026-09-10) and its subject BACKLOG.md:24 now reads [done #37] — the line should read [done #42]. The reconcile helper that would have caught this shipped inside PR #42 itself, so it could not reconcile its own cycle, and later cycles only reconcile their own claim...
- [ ] fix (health 2026-09-13): BACKLOG.md:217 is still marked [ ] ("fix (health 2026-09-02): BACKLOG.md:36 ... never advanced to [done #38]") but its subject is already resolved — BACKLOG.md:36 now reads [done #38] and PR #38 is MERGED. The line's own note says "close it on sight" and no cycle has closed it.
- [ ] fix (health 2026-09-15): run-2026-09-03_134704 — shipped 0/2, cycle 1 & 2 both 'exited non-zero' (review, research) on session limit
- [ ] fix (health 2026-09-15): run-2026-09-05_130001 — shipped 0/2, cycle 1 clean VERDICT: FAIL (real review), cycle 2 build exited non-zero on session limit
- [ ] fix (health 2026-09-15): run-2026-09-07_020001 — shipped 0/2, cycle 1 & 2 both exited non-zero (review, research) on session limit
- [ ] fix (health 2026-09-15): run-2026-09-08_020001 — shipped 0/2, cycle 1 & 2 both exited non-zero (review, research) on session limit; health also failed same cause
- [ ] fix (health 2026-09-15): run-2026-09-09_020001 — shipped 1/2, cycle 2 research exited non-zero on session limit; health and pipeline observer also failed same cause
- [ ] fix (health 2026-09-15): run-2026-09-10_020005 — cycle 1 shipped PR #42, cycle 2 research exited non-zero on session limit, then log silently stops mid post-cycle-2 reconcile with no "=== done" and no "Aborting." line
- [ ] fix (health 2026-09-15): run-2026-09-11_020004 — NETWORK UNREACHABLE (api.anthropic.com / github.com) — check VPN. Aborting.
- [ ] fix (health 2026-09-15): run-2026-09-12_020005 — shipped 1/2, cycle 2 build exited non-zero on session limit; health and pipeline observer also failed same cause
- [ ] fix (health 2026-09-15): run-2026-09-13_020004 — shipped 0/2, cycle 1 & 2 both "review verdict is MISSING" — research killed by the 600s background-task ceiling both times; pipeline observer also failed on session limit
- [ ] fix (health 2026-09-15): run-2026-09-14_020002 — shipped 0/2, cycle 1 & 2 both clean VERDICT: FAIL, root-caused both times to research killed by the 600s background-task ceiling; pipeline observer hit the same 600s ceiling and never wrote its report
- [ ] fix (health 2026-09-15): "You've hit your session limit" — 8x on 2026-09-03, 2026-09-05, 2026-09-07, 2026-09-08, 2026-09-09, 2026-09-10, 2026-09-12, 2026-09-13 — hit every phase type at least once (cycle review, cycle research, cycle build, health, pipeline...
- [ ] fix (health 2026-09-15): "Background tasks still running after 600s; terminating" blocking a phase's artifact (not just printing) — 2x on 2026-09-13 (both cycles, review verdict MISSING) and 2026-09-14 (both cycles, clean VERDICT: FAIL; also hit pipeline observer same night) — named in the 09-13 log itself as "the fourth night t...
- [ ] fix (health 2026-09-15): "its item is no longer in BACKLOG.md (reworded or removed) — a human needs to look" for cycle/2026-09-12-unshipped-024106-1 — 3x on 2026-09-12, 2026-09-13, 2026-09-14, printed by every reconcile-stranded-claims pass since, never acted on
- [ ] fix (health 2026-09-15): phase 'cycle 2/2: research' exited non-zero — 5x: 2026-09-03, 2026-09-07, 2026-09-08, 2026-09-09, 2026-09-10 (all session-limit)
- [ ] fix (health 2026-09-15): phase 'cycle 1/2: review' exited non-zero — 3x: 2026-09-03, 2026-09-07, 2026-09-08 (all session-limit)
- [ ] fix (health 2026-09-15): phase 'health' exited non-zero — 3x: 2026-09-08, 2026-09-09, 2026-09-12 (all session-limit)
- [ ] fix (health 2026-09-15): phase 'pipeline observer' exited non-zero — 3x: 2026-09-09, 2026-09-12, 2026-09-13 (all session-limit)
- [ ] fix (health 2026-09-15): phase 'cycle 2/2: build' exited non-zero — 2x: 2026-09-05, 2026-09-12 (both session-limit)
- [ ] fix (health 2026-09-15): BACKLOG.md:216 marked [building], shipped in PR #42 (merged 2026-09-10, branch cycle/2026-09-10-backlog-mark-done-reconcile) — never advanced to [done #42]. This line is itself a health finding about PR #37 that its own fix (PR #42) should have closed but couldn't reconcile in its own cycle; the 2026-09-13 health check alread...
- [ ] fix (health 2026-09-15): no run log for 2026-09-04
- [ ] fix (health 2026-09-15): no run log for 2026-09-06

## Stranded work (unshipped branches)
Appended by `.pipeline/run.sh` when a failed cycle's claim names an item
that is not on main — the researcher wrote the topic and the claim in one
edit, so main never had it. The branch holds the work; salvaging it is a
human's call.
- [stranded cycle/2026-09-12-unshipped-024106-1] MCP's other transport: Streamable HTTP, not stdio. Every MCP example
  in the lab so far (`mcp-hello-world`, and the two stranded `mcp-prompts` /
  `mcp-resources-claude-code` items above) either uses stdio or bypasses the wire
  entirely via the SDK's in-memory `Client`; `mcp-hello-world/README.md` names
  "HTTP transports (streamable-http, sse)" explicitly out of scope. Increment: a
  server exposed over Streamable HTTP instead, proven at the actual wire level —
  session-ID issuance, JSON vs SSE response framing, the DNS-rebinding
  Host-header check — via `httpx2.ASGITransport` wired directly to
  `MCPServer.streamable_http_app()`, no real socket, no live network, no key.
  See `research/2026-09-12-mcp-streamable-http.md`.
- [stranded cycle/2026-09-16-unshipped-023645-2] Manifest formats beyond `requirements.txt`/`package.json` for the
  dependency-pin scanner. `examples/skill-script-execution/README.md` names this
  explicitly out of scope ("Manifest formats beyond requirements.txt/package.json
  (e.g. Cargo.toml, go.mod) — a natural follow-up, not this cycle's scope").
  Increment: teach `scan_dependencies.py` a `scan_cargo_toml` case (stdlib
  `tomllib`, Python ≥3.11, no new dependency) covering `[dependencies]`,
  `[dev-dependencies]`, `[build-dependencies]`; only an explicit `=`-prefixed
  exact requirement counts as pinned — a bare or `^`-prefixed version defaults
  to Cargo's caret range, the same "not actually pinned" trap `package.json`'s
  bare-semver case already covers for npm. `{ path = ... }`/`{ git = ... }`/
  `{ workspace = true }` table entries have no meaningful semver pin to
  evaluate and are skipped silently, same precedent as `requirements.txt`'s
  comment/`-r` lines. go.mod stays out of scope: Go's module file has no
  floating-range syntax, so the pinned-vs-unpinned question doesn't apply
  there. Offline self-test only, extending `test_scan_dependencies.py`'s
  existing pattern — no API key, no network. See
  `research/2026-09-16-cargo-toml-dependency-scan.md`.
- [stranded cycle/2026-09-20-unshipped-022445-1] fix (health 2026-09-02): run-2026-09-01_134705 — shipped 0/2. Cycle 1 clean VERDICT: FAIL (no increment: researcher killed by the 600s ceiling, builder correctly refused to build). Cycle 2 verdict MISSING (same 600s researcher kill; review phase wro... (builder 2026-09-20: the literal 600s-ceiling cause named here is already fixed on main by commit 832134b, merged earlier tonight; per research/2026-09-20-pipeline-run-log-classifier-wiring.md this claim is being spent on the gap that finding leads to instead — wiring .pipeline/run_log.sh's classifier into the pipeline-observer phase, the phase these health findings keep showing dying on its own token budget.)
