# Tool search + `defer_loading`: a big tool catalog without the context bill

Date: 2026-11-04. Mode: demo. No plain `[ ]` feature item existed on `BACKLOG.md`
(only stale health findings and stranded claims; `gh pr list` shows only #62, a
pipeline PR). Per the 2026-10-27 precedent, the researcher filed a new item and
marked it `[researching]`.

## Question
How does the API's tool search tool (`defer_loading: true` + a search tool) work on
the wire, and what can be proven about it offline and for ~$0 in a small example?

## Findings
Primary source: [Tool search tool docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool)
(fetched 2026-11-04; page marked `status: stable`; `claude-api` skill not needed for ids,
all ids below are copied from that page's model table). Not previously in the lab
(`grep -ri "defer_loading" knowledge examples` hits only an unrelated MCP file).

- **No beta header.** Page is "stable", request uses `anthropic-version: 2026-10-21`
  ([[anthropic-api-2026-10-21]]). Supported on all current models incl.
  `claude-haiku-4-5-20251001`, `claude-sonnet-5`, `claude-opus-5` (Opus 4.1 and earlier: no).
- **Two search tools**, same shape: `{"type": "tool_search_tool_regex_20251119", "name": "tool_search_tool_regex"}`
  and `{"type": "tool_search_tool_bm25_20251119", "name": "tool_search_tool_bm25"}`.
  Regex: Claude writes Python `re.search()` patterns, case-insensitive, max 200 chars.
  BM25: natural language, max 500 chars. Both search tool name, description, argument
  names and argument descriptions.
- **`defer_loading: true` is a per-tool key** on ordinary tool definitions. You STILL
  send every definition on every request; it only controls what enters the model's
  context. At least one tool must be non-deferred (else 400 "At least one tool must
  have defer_loading=false"). Never defer the search tool. Docs advise keeping the 3-5
  hottest tools non-deferred. Limit: 10,000 deferred tools per request.
- **Wire shape of a search** (assistant content): `server_tool_use` (`name:
  "tool_search_tool_regex"`, `input: {pattern, limit?}`; `limit` 1..10,000, default 5)
  -> `tool_search_tool_result` with `content: {"type": "tool_search_tool_search_result",
  "tool_references": [{"type": "tool_reference", "tool_name": ...}]}` -> then a normal
  `tool_use` on a discovered tool. Next turn: echo assistant content back unchanged,
  add `tool_result` only for the normal `toolu_` id, resend the same full `tools`
  array. Returning a `tool_result` for the `srvtoolu_` id is a 400.
- **Failures:** empty match = 200 with empty `tool_references` (not an error). A bad
  search is a 200 with `content.type == "tool_search_tool_result_error"` and
  `error_code` in `invalid_tool_input` (bad regex / >200 chars), `unavailable`,
  `too_many_requests`. 400s: all-deferred; a `tool_reference` naming a tool absent from
  `tools`; `defer_loading` together with `cache_control` on the same tool.
- **Caching:** deferred tools are excluded from the prefix; discoveries are appended
  inline as `tool_reference`, so the cached prefix is untouched. Cache breakpoint goes
  on a non-deferred tool. Composes with `strict` ([[strict-tool-use]]) without grammar
  recompilation. See [[prompt-caching]].
- **Cost:** not metered as a server tool; loaded definitions bill as ordinary input
  tokens. Docs claim ~55k tokens of definitions for a 5-server MCP setup and >85%
  reduction; threshold guidance: use at >=10 tools or >10k tokens of definitions.
  These are vendor figures, unverified here.
- **Custom client-side search:** a normal custom tool whose `tool_result` content is
  `[{"type": "tool_reference", "tool_name": "..."}]`; each name must exist in top-level
  `tools` (normally deferred). Enables embeddings search. Server format
  (`tool_search_tool_result`) is NOT to be used for the custom form.
- **MCP:** with the MCP connector set `defer_loading` on the `mcp_toolset`'s
  `default_config`/`configs`, not per tool. Computer/browser toolsets take it inside
  `configs`, all members must agree.
- Related lab notes: [[tool-use-loop]], [[typed-tool-registry]], [[token-counting-gate]],
  [[context-editing]] (another way to fight context bloat; this one acts on definitions
  rather than results).
- Practitioner reception: the `hn-search` MCP server failed to connect this cycle
  (ENOENT on its `.venv`), so no HN evidence was gathered. Not claimed.

## Build proposal
Directory: `examples/tool-search-catalog/` (not present in `ls examples/` on main;
no open PR touches it).

**Intent.** Show how a hand-written loop must change to use server-side tool search:
build a catalog request with deferred tools, and handle the new block types
(`server_tool_use`, `tool_search_tool_result`) correctly in the loop. Out of scope:
BM25 vs regex quality comparison, MCP connector, embeddings search, streaming, batches,
a quality/accuracy benchmark.

**Files (stdlib + `anthropic` pinned to what `examples/token-budget-guard/requirements.txt` uses, re-check on 2.x)**
- `catalog.py` - pure: `Tool = dict`; `make_catalog(n: int) -> list[dict]` deterministic synthetic tools with service-prefixed names (`github_`, `slack_`, `jira_`...) and arg descriptions; `HOT_TOOLS` constant.
- `request.py` - pure: `build_tools(catalog, hot: set[str], variant: Literal["regex","bm25"]) -> list[dict]` (search tool first, hot tools plain, rest `defer_loading: True`); `validate_tools(tools) -> None` raising `ToolsetError` for the three documented 400s (all deferred, deferred search tool, `defer_loading` + `cache_control`, duplicate names).
- `local_search.py` - pure reference model of the regex tool: `search(catalog, pattern, limit=5) -> list[str]` using `re.search(..., re.IGNORECASE)` over name, description, arg names, arg descriptions; pattern >200 chars or invalid regex returns an error value (typed `SearchError("invalid_tool_input", msg)`), empty match returns `[]`. Labeled a *model* of the server, not the server.
- `blocks.py` - pure: `discovered_tools(content) -> list[str]` (collect `tool_reference.tool_name` from `tool_search_tool_result` blocks), `search_error(content) -> str | None`, `pending_client_tool_uses(content) -> list[block]` (only ids starting `toolu_`, never `srvtoolu_`), `to_followup_message(content, results)` building the next user turn with `tool_result` only for client tool_use blocks.
- `agent.py` - thin loop with injected client (same pattern as `examples/minimal-agent-loop/`); live run `python agent.py "..."` with `claude-haiku-4-5`.
- `measure.py` - billed-free ($0) live script: `client.messages.count_tokens` on the same catalog all-loaded vs deferred; prints both counts. README records whatever happens.
- `test_tool_search.py`, `README.md` (transcript sections that need a key marked `<!-- transcript-check: skip ... -->`).

**Acceptance criteria (offline, no key)**
1. `build_tools(make_catalog(40), HOT, "regex")` has exactly one `tool_search_tool_regex_20251119`, 40-len(HOT) tools with `defer_loading is True`, hot tools without the key; `validate_tools` passes. Each of the four invalid toolsets raises `ToolsetError` with a distinct message.
2. `local_search`: pattern `weather` finds the weather tool; `get_.*_data` matches by regex; uppercase pattern matches (case-insensitive); default limit 5, explicit `limit=2` caps; 201-char pattern and `"("` return `SearchError("invalid_tool_input", ...)`; no match returns `[]`, not an error.
3. `blocks`: a recorded docs-shaped response (server_tool_use + tool_search_tool_result + tool_use) yields `discovered_tools == ["get_weather"]`; `pending_client_tool_uses` returns only the `toolu_` block; `to_followup_message` emits no `tool_result` for the `srvtoolu_` id (assert absence).
4. Loop test with a fake client replaying two scripted responses: first the docs-shaped search-then-tool_use response, then `end_turn`; assert the second request's `messages` contain the first assistant content unchanged (deep-equal, search blocks included) and the identical full `tools` list (deferred definitions still present).
5. A `tool_search_tool_result_error` response makes the loop raise a typed error naming the `error_code` (fail loudly, per repo posture) rather than continuing.
6. Live, $0: `measure.py` prints token counts for loaded vs deferred. EXPECTED but UNCONFIRMED: deferred is much smaller. If `count_tokens` ignores `defer_loading` (counts equal), the README says so plainly and falls back to one tiny billed `messages.create` comparing `usage.input_tokens` (`claude-haiku-4-5`, <$0.01). Either outcome is a valid, recorded result.

**Failure modes to state in README:** deferred tools are still sent every request (bandwidth, not context); discovery depends on description keywords; regex over 200 chars; Haiku-class models may choose bad patterns; `tool_reference` to an unknown name is a 400.

## Open questions
- Does `count_tokens` honor `defer_loading`/tool search (i.e. is the saving measurable for $0)? Unconfirmed; docs do not say.
- Exact Python SDK 2.x typed class names for the search tool (`ToolSearchToolRegex20251119`?). C#/Go/Java names appear in docs; for Python dicts are used in docs, so use dicts.
- Whether the SDK `tool_runner` / `beta_tool` path supports `defer_loading` is unchecked.
- No HN/practitioner reception gathered (MCP server down).
