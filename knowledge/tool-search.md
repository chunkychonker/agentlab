# Tool search / `defer_loading` (API, stable, no beta header)

Source: [docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-search-tool), read 2026-11-04. Note: research/2026-11-04-tool-search-deferred-tools.md.

- Tools: `tool_search_tool_regex_20251119` (Python `re.search`, case-insens, <=200 chars) and `tool_search_tool_bm25_20251119` (natural language, <=500). Searches name, description, arg names, arg descriptions.
- `defer_loading: true` on a tool keeps it out of context until discovered, but the full definition is still sent every request. >=1 tool must stay non-deferred; never defer the search tool; 10,000 deferred max; default 5 results per search (`limit` 1..10,000).
- Response: `server_tool_use` -> `tool_search_tool_result` (`tool_references`) -> normal `tool_use`. Echo all blocks back unchanged; never send `tool_result` for a `srvtoolu_` id (400). Empty match is 200 + empty list; failures are 200 with `tool_search_tool_result_error` (`invalid_tool_input`, `unavailable`, `too_many_requests`).
- Caching preserved (deferred tools excluded from prefix); `defer_loading` + `cache_control` on one tool is a 400. Custom search: return `tool_reference` blocks in a normal `tool_result`.
- Unverified: whether `count_tokens` honors deferral.

Related: [[tool-use-loop]], [[prompt-caching]], [[strict-tool-use]], [[token-counting-gate]], [[context-editing]]
