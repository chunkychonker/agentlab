# Knowledge index

Map of the knowledge base. The researcher keeps this current as notes are added.

## Coding agents
- [[tool-use-loop]] — the hand-written Messages API tool loop + gotchas
- [[streaming-tool-use]] — the streaming sibling: SSE event flow
  (`message_start`/`content_block_start`/`_delta`/`_stop`/`message_delta`/
  `message_stop`), the text-delta-vs-`input_json_delta` asymmetry (`tool_use`
  input starts `{}` and arrives as raw JSON string fragments, parseable only
  at `content_block_stop`), source-verified Python SDK event attribute names,
  and the offline `SimpleNamespace`-event-replay testing trick
- [[thinking-blocks]] — the `thinking` / `redacted_thinking` round-trip
  contract: pass every block back complete, unmodified, in order within a
  tool-use turn or get a 400; `signature` byte-preservation; filtering by
  `type == "thinking"` silently drops `redacted_thinking`; the streaming shape
  (`thinking_delta` then one `signature_delta`; `display:"omitted"` sends no
  thinking text; `redacted_thinking` is delta-less); and manual-vs-adaptive
  model support (manual `budget_tokens` is 400 on 4.7+, event shapes identical
  across modes)
- [[typed-tool-registry]] — `@beta_tool` + `client.beta.messages.tool_runner`: typed schemas, validation, registry pattern
- [[orchestrator-workers]] — fan-out-to-specialists pattern; `client.messages.parse(output_format=...)` for the plan step; parallelizing the delegate step with `ThreadPoolExecutor.map` (input-order results, sharing one sync client across the pool, latency-not-token win, and the shared-prefix cache-killer); the three distinct "subagent" products and when each applies
- [[tool-failure-taxonomy]] — the three dispositions for a failing tool call
  (retry locally / report to the model with `is_error` / abort loudly), the
  `is_error` wire contract and what a good error message looks like, and
  `strict: true` as prevention for one whole error class
- [[anthropic-python-sdk]] — SDK basics: client, `messages.create`, response shape
- [[sdk-retry-behavior]] — the *other* retry layer: source-verified SDK transport
  retry defaults, which statuses retry, `retry-after` handling and its ≤60s
  clamp, the wrong jitter comment, and the status → exception table
- [[anthropic-models]] — current model IDs and prices (re-check, don't guess)
- [[context-editing]] — keeping a long tool loop inside the context budget
  server-side: the three `context_management` strategies and their two betas,
  the source-verified `clear_tool_uses_20250919` field shapes (including the
  two `trigger` forms and `clear_tool_inputs` being more than a bool), the
  all-or-nothing `clear_at_least` and cache-invalidation tradeoffs, and the
  free `count_tokens` preview path — plus the asymmetry that trips people up
  (`count_tokens` returns `original_input_tokens` only, never `applied_edits`)
- [[compaction]] — the *summarize* sibling: the `compact_20260112` edit's four
  fields and its 50k-token trigger floor, `pause_after_compaction` +
  `stop_reason: "compaction"`, the three ways to detect it (never
  `applied_edits`), the null-summary failure mode both vendors' example
  snippets crash on, `encrypted_content` round-tripping, why top-level
  `usage.input_tokens` under-reports the bill, and why Haiku can't run it
- [[prompt-caching]] — where `cache_control` breakpoints go in a message list
  that grows every turn: the 4-breakpoint cap, the 20-block lookback, the
  tools/system/rolling/anchor layout for a hand-written tool loop, per-model
  minimum cacheable prefix (Haiku 4.5 wants 4,096), the
  `cache_creation_input_tokens` / `cache_read_input_tokens` / `input_tokens`
  identity and the 1.25×/0.10× multipliers, why `count_tokens` can't preview it
  for $0, the practitioner cache-killers (incl. parallel fan-out sharing a
  prefix — N writes, 0 reads), and the direct tension with [[context-editing]]

## Skills
- [[agent-skills]] — `SKILL.md` anatomy: progressive disclosure (3 load
  tiers), the documented/checkable frontmatter rules, the model-invocation
  trigger contract, Claude-Code-vs-API skill differences, what is/isn't
  offline-testable, the `${CLAUDE_SKILL_DIR}` + `allowed-tools` mechanism
  for a bundled script to run without a permission prompt (version-gate
  claim corrected 2026-08-16 — no longer documented — and a still-open bug
  report; **verified live 2026-08-16 on 2.1.221 — the single-token
  space-suffix form does suppress the prompt**, via headless mode's
  deterministic deny behavior, plus the captured `stream-json` shape of a
  denied tool call), and
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
- Testing agent loops offline: inject a fake client (see [[tool-use-loop]]);
  for a streaming loop, the same trick applies one layer down — replay a
  scripted list of fake SSE events through a pure accumulator, and fake
  `client.messages.stream()`'s context-manager protocol for the full loop
  (see [[streaming-tool-use]])
- Testing retry/backoff logic offline: inject `sleep` and `jitter` as parameters
  and keep the policy a pure function, so the test is instant and deterministic
  (see [[tool-failure-taxonomy]])
- Testing typed tools offline: schema generation + Pydantic validation are pure
  local code, no fake client needed (see [[typed-tool-registry]])
- Verifying an API behaviour for $0: `count_tokens` is free, rate-limited
  independently of message creation, and accepts several of the same request
  params — so effects that show up in the input prefix (context editing, prompt
  size) can be measured for real without a billed generation
  (see [[context-editing]]) — but only for effects the endpoint reproduces:
  it applies existing compaction blocks and will **not** trigger new ones, so
  compaction cannot be previewed for $0 (see [[compaction]]); likewise it runs
  no caching logic and returns no cache fields, so prompt caching can't be
  previewed for $0 either (see [[prompt-caching]])
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
- [[bash-3.2-testable-scripts]] — the same inject-the-side-effect idea in shell:
  macOS ships only bash 3.2 (so no bash-4 syntax in pipeline scripts), passing a
  *function name* and calling it indirectly is what makes orchestration testable
  offline, why a fake must mutate real state rather than a counter, the
  `grep -c`-exits-1-on-zero-matches trap, and why plain bash beats adding `bats`

## Repo hygiene & self-verification
- [[pipeline-claim-lifecycle]] — how a `BACKLOG.md` claim moves through a night
  and the two places it is silently lost: a failed cycle's `snapshot_dirty_main`
  + `reset_to_clean_main` releases the claim with no PR for `gh pr list` to
  find, and the replenishment gate is satisfied by the researcher's own
  empty-backlog fallback (so it has never once fired). Plus the literal
  `^- \[ \]` counting contract, now consolidated to a single executable copy
  at `.pipeline/backlog.sh:39`, and why the surrounding `|| true` is
  load-bearing
- [[doc-transcript-drift]] — READMEs that paste program output rot silently;
  why diff-scoped nightly review structurally cannot catch an invariant
  spanning two files that are never edited together, the MATCH / DRIFT /
  **UNRUNNABLE** verdict taxonomy a transcript checker needs (a missing
  dependency is not drift), and why exact-match-with-no-`--update`-flag is the
  point rather than a limitation (Go/doctest precedent)
