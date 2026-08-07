# Hacker News Algolia Search API

Public, unauthenticated read API over Hacker News, run by Algolia. Base URL
(live-verified 2026-08-06): `https://hn.algolia.com/api/v1`. Use `https://`
directly — the bare `http://` form 301-redirects to it.

## Endpoints (all confirmed by direct request, not just docs)

| Endpoint | Behavior |
|---|---|
| `GET /search?query=...` | Relevance-ranked (then points, then comments). |
| `GET /search_by_date?query=...` | Same shape, sorted by `created_at_i` descending. |
| `GET /items/:id` | Full item incl. nested `children` comment tree. |
| `GET /users/:username` | `{"username", "about", "karma"}`. |

Query params: `query` (text), `tags` (comma = AND, e.g. `tags=story`;
`story`/`comment`/`ask_hn`/`show_hn`/`poll`/`front_page`/`author_USERNAME`
are documented tag values — parenthesized OR syntax is documented but I did
not independently confirm it live), `numericFilters` (e.g. `points>100`),
`page` (0-indexed), `hitsPerPage` (max 1000 — confirmed by [algolia/hn-search
issue #230](https://github.com/algolia/hn-search/issues/230), requesting more
does not return more). No auth, no API key, no `X-RateLimit-*` response
headers observed on any call — a "10,000 req/hour" figure floating around in
blog posts has **no primary-source confirmation found**; don't code rate-limit
logic against it as if it were a documented contract.

## The gotcha: error response bodies are not reliably JSON

Confirmed by direct requests, 2026-08-06 — this is the fact a wrapper must
handle correctly:

- `items/:id`, nonexistent id → **HTTP 404, clean JSON**:
  `{"error":"Not Found","status":404}`.
- `users/:username`, nonexistent user → **HTTP 500, HTML body** (a generic
  server error page), not JSON.
- `search`, malformed `numericFilters` → **HTTP 400, HTML body**, not JSON.

A client that does `response.json()` unconditionally on a non-2xx will throw
a raw `JSONDecodeError` on two of these three real shapes. Correct handling:
branch on status first, then attempt-JSON-then-fall-back-to-text, never
assume the error body's content type.

Also confirmed: a zero-match search returns `200` with `hits: [], nbHits: 0`
— empty results are success, not a failure mode. Don't conflate the two.

## Response shape worth knowing before parsing

Each search hit carries a `_highlightResult` object duplicating every field
with HTML `<em>` tags injected — noise for a downstream LLM caller, strip it
rather than forward it. `items/:id`'s `children` array is the full recursive
comment tree (can be thousands of nodes for a popular story) — cap what you
return rather than forwarding it whole.

Research note (worked build proposal, wrapping this as an MCP tool with
offline `httpx.MockTransport` tests for the three error shapes above):
[2026-08-06-mcp-hn-search](../research/2026-08-06-mcp-hn-search.md). MCP
mechanics and the offline-HTTP-mocking pattern: [[mcp-python-sdk]].

Sources: [cotera.co HN API guide](https://cotera.co/articles/hacker-news-api-guide) (undated, cross-checked live), [DADL Algolia HN Search MCP Server listing](https://www.dadl.ai/d/algolia-hn-search/) ("last reviewed 2026-04-04"), [algolia/hn-search #230](https://github.com/algolia/hn-search/issues/230) — plus direct `curl` requests to the live API, 2026-08-06.
