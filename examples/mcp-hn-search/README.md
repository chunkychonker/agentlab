# HN Algolia Search MCP server (`search_stories`, `get_story`)

A stdio [MCP](https://modelcontextprotocol.io) server with two tools that
wrap the live, unauthenticated Hacker News Algolia Search API — and
correctly handle the API's real, live-verified error shapes instead of
assuming every non-2xx response is JSON.

From the research note:
[`research/2026-08-06-mcp-hn-search.md`](../../research/2026-08-06-mcp-hn-search.md).
Background also collected in
[`knowledge/hn-algolia-api.md`](../../knowledge/hn-algolia-api.md) and
[`knowledge/mcp-python-sdk.md`](../../knowledge/mcp-python-sdk.md).

## What's here

| File | What it is |
|------|-----------|
| `hn_client.py` | The I/O edge — the only file that imports `httpx`. `fetch(path, params, client)` performs one GET and turns every real failure mode into `HNApiError`. `client` is a required parameter, not constructed internally — the seam that makes offline testing possible. |
| `hn_parse.py` | The pure core — no `httpx`, no I/O. Maps raw API responses to caller-facing summaries, stripping Algolia noise and capping the comment tree. |
| `server.py` | The thin imperative shell — `MCPServer("hn-search")`, tool registration, owns constructing the real `httpx.AsyncClient`. |
| `test_hn_parse.py` | Pure, fixture-based tests for `hn_parse.py`. No `httpx` import at all. |
| `test_hn_client.py` | `httpx.MockTransport`-backed tests for `hn_client.py`. Reproduces the three real error shapes below with zero network access. |
| `test_server.py` | In-memory `Client(mcp)` tests proving tool registration and schema only (see "Why no live-network test" below). |
| `requirements.txt` | `mcp>=2.0.0,<3`, `httpx>=0.28,<0.29`. |

## The API this wraps

Base URL: `https://hn.algolia.com/api/v1`. No auth, no API key. Confirmed
live 2026-08-06 (see the research note for full detail):

| Endpoint | Behavior |
|---|---|
| `GET /search?query=...` | Relevance-ranked. |
| `GET /search_by_date?query=...` | Same shape, sorted by `created_at_i` descending. |
| `GET /items/:id` | Full item incl. nested `children` comment tree. |

(`GET /users/:username` exists but is deliberately not wrapped here — see
"Out of scope" below.)

## The gotcha this wrapper exists to handle correctly

Error response bodies are **not reliably JSON** — confirmed by direct
request, 2026-08-06:

- `items/:id`, nonexistent id → **HTTP 404, clean JSON**:
  `{"error":"Not Found","status":404}`.
- `users/:username`, nonexistent user → **HTTP 500, HTML body**, not JSON.
- `search`, malformed `numericFilters` → **HTTP 400, HTML body**, not JSON.

A client that does `response.json()` unconditionally on every non-2xx
response throws a raw `json.JSONDecodeError` on two of these three real
shapes, producing a confusing message instead of a clear one.
`hn_client.fetch` branches on status first, then attempts JSON and falls
back to truncated text — verified by `test_hn_client.py`'s
`test_500_html_error_body_does_not_leak_json_decode_error`, the one test
that would silently start failing if this handling regressed.

Also handled correctly: a zero-match search returns `200` with `hits: [],
nbHits: 0` — this is success, not a failure mode. `summarize_hits({"hits":
[]})` returns `[]` without raising, and is asserted as such.

## Run the self-tests (no API key, no live network required)

```bash
cd examples/mcp-hn-search
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_hn_parse.py    # pure fixtures, no httpx import
.venv/bin/python test_hn_client.py   # httpx.MockTransport, no network
.venv/bin/python test_server.py      # in-memory Client, registration/schema only
```

All three pass offline, as verified during this build:

```
$ .venv/bin/python test_hn_parse.py
ok  summarize_hits strips _highlightResult and maps only the useful fields
ok  summarize_hits({'hits': []}) returns [] without raising
ok  summarize_item on an 8-child fixture returns exactly 5 top_comments, HTML-unescaped

All 3 self-tests passed.

$ .venv/bin/python test_hn_client.py
ok  200 JSON response -> fetch returns the parsed dict
ok  404 JSON error body -> HNApiError(status=404), message surfaces the JSON 'error' field
ok  500 HTML error body -> HNApiError(status=500), no raw JSONDecodeError leaks into the message
ok  handler raising httpx.TimeoutException -> HNApiError(status=None)

All 4 self-tests passed.

$ .venv/bin/python test_server.py
ok  list_tools() returns exactly search_stories and get_story
ok  search_stories schema: query required, hits_per_page/sort optional
ok  get_story schema: story_id required

All 3 self-tests passed.
```

Also verify the `__main__` guard doesn't accidentally start the server on
import (a regression here would hang):

```bash
.venv/bin/python -c "import server"   # succeeds, zero output, returns immediately
```

## Why no live-network test in the automated suite

`test_server.py` uses the SDK's in-memory `Client(mcp)` to prove
**registration and schema** (tool names, required/optional parameters) —
but it deliberately does **not** call `search_stories` or `get_story`
through that client, because their bodies perform a real outbound HTTP
request to the live HN API. Doing so would make the self-test
network-dependent and flaky (subject to the live API being up, rate
limits, and network flakiness in CI). Correctness of the actual
HTTP-handling logic — the 404-JSON, 500/400-HTML, and timeout paths — is
proven instead by `test_hn_client.py`'s `httpx.MockTransport`-injected
coverage, made possible because `hn_client.fetch` takes its `httpx.AsyncClient`
as a required, injected parameter rather than constructing one internally.

## Poke it against the live API (manual, optional, not part of the self-test)

```bash
.venv/bin/pip install "mcp[cli]>=2.0.0,<3"
.venv/bin/mcp dev server.py   # requires npx on PATH
```

Try a real query (e.g. `search_stories` with `query="claude"`) and then a
real invalid id (e.g. `get_story` with `story_id=999999999999`) to see the
404-JSON path surface as a tool error live, not just against the mock.
This was also verified directly during this build, outside the Inspector:

```python
>>> await hn_client.fetch("/search", {"query": "claude", "tags": "story", "hitsPerPage": 2}, client)
# -> real hits, e.g. "Claude Fable 5" (anthropic.com), 2626 points
>>> await hn_client.fetch("/items/999999999999", {}, client)
# -> HNApiError(status=404, message="HN API error 404: Not Found")
```

## Out of scope (see the research note's Build proposal for the reasoning)

- `users/:username` as a third tool — `get_story`'s 404 case already
  exercises the JSON-error path; a `get_user` tool would add a new
  endpoint but not a new *pattern*.
- Full nested comment trees — `items/:id`'s `children` can be thousands of
  nodes deep for a popular story; `summarize_item` caps `top_comments` at
  5, always, by construction (`hn_parse.MAX_TOP_COMMENTS`).
- Rate limiting / backoff — no confirmed official numeric limit to code
  against (the "10,000 req/hour" figure floating around is unconfirmed
  secondary-source folklore, not a documented contract).
- Registering this server with a live Claude Code/Desktop session — that's
  the next backlog item ("Connecting a custom MCP server to Claude Code
  end-to-end"); this example proves the tool logic is correct via the
  SDK's own client machinery, live-host wiring is a separate concern.
