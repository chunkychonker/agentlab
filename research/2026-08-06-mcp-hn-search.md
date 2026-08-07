# MCP server wrapping a public REST API (Hacker News Algolia)

## Question

What does it actually take — mechanically and in failure-mode terms — to wrap
a real, live, unauthenticated public REST API (the Hacker News Algolia Search
API) as an MCP server tool, and what's the smallest same-day-buildable
increment that proves the wrapper handles that API's real edge cases, testable
fully offline?

## Note on backlog selection

The topmost literal `[ ]` items in `BACKLOG.md` (Skills: "shells out to a
local script", "reference files") already have **complete, unmerged work**
sitting in open PRs #5 (`cycle/2026-07-30-skill-script-execution`, opened
2026-07-30) and #6 (`cycle/2026-07-31-skill-reference-files`, opened
2026-07-31) — both have research notes, working examples with tests, and
matching `knowledge/agent-skills.md` updates already on their branches; they
just haven't been reviewed/merged/back-merged into `main`, so `BACKLOG.md` on
`main` still shows them as unclaimed. Redoing that research would be pure
waste. I skipped both and took the next genuinely unbuilt item: "MCP server
wrapping a public REST API (e.g. Hacker News Algolia)," marked `[researching]`.
**Flag for the maintainer**: PRs #5 and #6 are stale (zero review activity
since creation) and should be triaged — either merged or explicitly closed —
so `BACKLOG.md` stops lying about their status.

## Findings

### The API itself — live-verified today (2026-08-06), not taken from docs alone

I hit `https://hn.algolia.com/api/v1/...` directly with `curl` rather than
trusting blog snippets, since the official interactive docs page at
`hn.algolia.com/api` is a client-rendered SPA that doesn't yield to a text
fetch. Secondary sources used only to triangulate endpoint names, then every
claim below was independently confirmed by an actual request:

- [Hacker News API guide, cotera.co](https://cotera.co/articles/hacker-news-api-guide) (undated but content matches live behavior)
- [Algolia HN Search MCP Server, DADL registry](https://www.dadl.ai/d/algolia-hn-search/) — "Last reviewed: 2026-04-04" — confirms someone already shipped a 4-tool version of essentially this idea (`search`, `search_by_date`, `get_item`, `get_user`); good corroboration this is a sane, well-trodden shape, but I did not read its source, only the listing summary.
- [algolia/hn-search GitHub issue #230](https://github.com/algolia/hn-search/issues/230) — confirms a real, current constraint: hits capped at 1000 regardless of `page`.

**Base URL and endpoints** (all confirmed live, 2026-08-06):

| Endpoint | Verified behavior |
|---|---|
| `GET https://hn.algolia.com/api/v1/search?query=...` | Relevance-ranked. `http://` (no `s`) 301-redirects to `https://` — use `https://` directly, don't rely on the redirect. |
| `GET https://hn.algolia.com/api/v1/search_by_date?query=...` | Same shape, sorted by `created_at_i` descending. Confirmed: query `mcp` returned a hit from `2026-08-06T04:46:42Z`, i.e. today. |
| `GET https://hn.algolia.com/api/v1/items/:id` | Full item incl. nested `children` comment tree. `items/1` returned the real, decades-old first HN item (`pg`/`sama` thread). |
| `GET https://hn.algolia.com/api/v1/users/:username` | `{"username", "about", "karma"}`. `users/pg` returned real karma (157316 at time of writing). |

No auth, no API key — confirmed (every call above succeeded with zero
headers). Response header `access-control-allow-origin: *`; no
`X-RateLimit-*` headers observed on any response, so a client cannot
self-throttle from response headers alone — a documented "10,000 req/hour"
figure appears in secondary sources but I found **no primary Algolia/HN
statement of an official numeric limit**, so treat that number as unconfirmed
folklore, not a contract to code against.

**Query parameters** (confirmed live): `query` (text), `tags` (comma =
AND, e.g. `tags=story`; parenthesized OR is documented but my own paren test
was inconclusive due to shell quoting, not independently re-verified),
`numericFilters` (e.g. `points>100`), `page` (0-indexed), `hitsPerPage` (max
1000, confirmed by the linked GitHub issue — SDK/tool should treat an
attempt to request more as pointless, not silently accept it).

**Response shape** (from an actual captured `search` response, query=`claude`,
`tags=story`, `hitsPerPage=2`): top level has `hits`, `nbHits`, `page`,
`nbPages`, `hitsPerPage`, `processingTimeMS`, `query`, `params`, plus Algolia
internals (`exhaustive*`, `processingTimingsMS`, `serverTimeMS`) that are
irrelevant noise for an LLM caller. Each hit has `objectID`, `title`, `url`,
`author`, `points`, `num_comments`, `created_at`/`created_at_i`, `_tags`,
`story_id`, plus `_highlightResult` (HTML-highlighted duplicate of every
field — pure noise for a tool response, should be stripped, not forwarded)
and, for `items/:id`, a recursive `children` array (each child has the same
shape plus `text`, `parent_id`, `type`).

### The real gotcha: error responses are not reliably JSON

This is the finding that actually matters for building a correct wrapper —
confirmed by direct requests, not assumed:

- `items/:id` with a nonexistent id → **HTTP 404 with a clean JSON body**:
  `{"error":"Not Found","status":404}`.
- `users/:username` with a nonexistent username → **HTTP 500 with an HTML
  body** (`<html>...Internal Server Error...</html>`), not JSON.
- `search` with malformed `numericFilters` (e.g. `points>notanumber`) →
  **HTTP 400 with an HTML body** (a generic Werkzeug/Flask-style error page),
  not JSON.

So: a wrapper that does `response.json()` unconditionally on a non-2xx will
throw a raw `JSONDecodeError` on two of the three error shapes it will
actually encounter, producing a confusing message instead of a clear one.
Any correct wrapper must branch on status first, then attempt-and-fall-back
on body parsing rather than assume JSON. This is exactly the kind of gotcha
this repo's research is supposed to surface before the builder hits it
blind.

Also confirmed live: an **empty-but-valid** result (query with zero matches)
returns `200` with `hits: [], nbHits: 0` — this is success, not a failure
mode, and a wrapper must not conflate "no results" with "error."

### MCP SDK mechanics — reused from yesterday's fresh knowledge note, not re-derived

`knowledge/mcp-python-sdk.md` (written 2026-08-05, one day old, already
verified against installed `mcp==2.0.0`) already covers: v2 import
(`from mcp.server import MCPServer`, `FastMCP` is gone), `@mcp.tool()`
deriving schema from type hints, stdio-default `mcp.run()`, the
stdout-is-the-wire gotcha, in-memory `Client` testing, and the
tool-error-vs-protocol-error split (ordinary exceptions inside a tool body →
non-raising `is_error=True` `CallToolResult`; `MCPError` → actually raises on
the client). I did not re-derive any of this.

One new confirmed fact needed for *this* build: **`@mcp.tool()` supports
`async def`**, and I/O-doing tools should be async — [python-sdk
`docs/servers/tools.md`](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/tools.md)
(fetched 2026-08-06), verbatim guidance: "If a tool does I/O (calls an API,
reads a file, queries a database), declare it `async def` and `await` inside
it. The SDK awaits it." A plain `def` tool instead runs in a thread pool. This
also matches the SDK's own official weather-server quickstart
([`quickstart-resources/weather-server-python/weather.py`](https://github.com/modelcontextprotocol/quickstart-resources/blob/main/weather-server-python/weather.py))
which uses `async with httpx.AsyncClient() as client: await client.get(...)`
inside an `async def` tool — **note this quickstart file itself still imports
`from mcp.server.fastmcp import FastMCP`, the removed v1 path**, reconfirming
yesterday's finding that search-engine-surfaced MCP examples are running
stale against the current v2 SDK. Use `from mcp.server import MCPServer`, not
this quickstart's import line.

### Offline-testable HTTP mocking — verified by executing it, not just reading docs

httpx (`0.28.1`, latest as of today) ships `httpx.MockTransport`: a transport
that takes a plain handler function `(httpx.Request) -> httpx.Response` and
can be attached to `httpx.AsyncClient(transport=...)`. I verified this
myself end-to-end in a throwaway venv (not taken from docs alone): a mock
handler correctly produced a 200 JSON response, a 404 JSON-body response, a
500 HTML-body response, and — by simply `raise`ing `httpx.TimeoutException`
inside the handler — a genuine timeout exception that propagated through
`await client.get(...)` exactly like a real network timeout would. This means
every real failure mode found above (404-JSON, 400/500-HTML, timeout) can be
reproduced deterministically, offline, with zero network access, as long as
the code under test accepts an `httpx.AsyncClient` as an explicit parameter
(dependency injection at the function boundary) rather than constructing one
internally with no seam. [httpx transports docs](https://www.python-httpx.org/advanced/transports/) (fetched 2026-08-06, version not stated on the page but matches installed 0.28.1 behavior, confirmed by execution).

## Build proposal

### Intent

Ship `examples/mcp-hn-search/`: a stdio MCP server with two tools —
`search_stories` and `get_story` — that wrap the live HN Algolia Search API,
correctly handling the three real error shapes found above (404-JSON,
400/500-HTML, timeout), fully self-tested offline via `httpx.MockTransport`
(no live network calls in the automated test suite). Out of scope: the
`users/:username` endpoint (a third tool would dilute this from "one clear
idea" to "an API client library"; `get_story`'s 404 case already exercises
the JSON-error path, so a `get_user` tool wouldn't add a new *pattern*, only
a new endpoint), full nested comment trees (`items/:id`'s `children` can be
thousands of nodes deep — return only the first few top-level comments,
stripped of HTML-highlight noise), rate limiting/backoff logic (no confirmed
official limit to code against), and registering the server with a live
Claude Code/Desktop session (that's the next backlog item, "Connecting a
custom MCP server to Claude Code end-to-end" — this example proves the tool
logic is correct via the SDK's own client machinery, live-host wiring is a
separate concern).

### Behavioral spec

**`examples/mcp-hn-search/hn_client.py`** (the I/O edge — the only file that
imports `httpx`):

- `HN_BASE_URL = "https://hn.algolia.com/api/v1"` (named constant, not
  inlined per-call).
- `class HNApiError(Exception)`: carries `status: int | None` (`None` for a
  timeout/connection failure, since there's no HTTP status in that case) and
  a human-readable `message`.
- `async def fetch(path: str, params: dict[str, str | int], client: httpx.AsyncClient) -> dict`:
  performs `GET {HN_BASE_URL}{path}` with `params`; on 2xx, returns
  `response.json()`; on non-2xx, attempts `response.json()` and falls back to
  a truncated `response.text` if that raises `json.JSONDecodeError`, then
  raises `HNApiError(status=response.status_code, message=...)` — **never**
  lets a raw `JSONDecodeError` escape from an error branch. On
  `httpx.TimeoutException`/`httpx.ConnectError`, raises
  `HNApiError(status=None, message="HN API request failed: ...")`. The
  `client` parameter is required, not defaulted — this is the injection seam
  that makes offline testing possible; the caller (`server.py`) owns
  constructing the real client, `test_hn_client.py` owns constructing the
  `MockTransport` one.

**`examples/mcp-hn-search/hn_parse.py`** (the pure core — no imports beyond
the stdlib, no I/O, no `httpx`):

- `def summarize_hits(raw: dict) -> list[dict]`: maps each entry in
  `raw["hits"]` to `{objectID, title, url, author, points, num_comments,
  created_at}` — drops `_highlightResult` and other Algolia-internal noise.
  Empty `hits` → `[]`, not an error (this is a stated invariant, not
  incidental behavior).
- `def summarize_item(raw: dict) -> dict`: maps a raw `items/:id` response to
  `{objectID, title, url, author, points, num_comments, created_at,
  top_comments}` where `top_comments` is the first 5 entries of `children`
  (or fewer/empty if there are fewer), each reduced to `{author, text}` with
  `text` HTML-entity-decoded via `html.unescape` (stdlib) — a deliberate cap,
  stated as an invariant: this function never returns more than 5 comments
  regardless of input size.

**`examples/mcp-hn-search/server.py`** (the thin imperative shell —
`MCPServer`, tool registration only):

- `@mcp.tool() async def search_stories(query: str, hits_per_page: Annotated[int, Field(ge=1, le=50)] = 5, sort: Literal["relevance", "date"] = "relevance") -> list[dict]`:
  opens an `httpx.AsyncClient(timeout=10.0)` as a context manager (matches
  the SDK's own quickstart pattern), calls `hn_client.fetch` against
  `/search` or `/search_by_date` depending on `sort`, with `tags=story` fixed
  (keeps results to stories, not comments — a stated scope decision, not an
  omission), then `hn_parse.summarize_hits`. Lets `HNApiError` propagate
  (becomes a normal, non-raising `is_error=True` tool result per the SDK
  mechanics already recorded in `knowledge/mcp-python-sdk.md`).
- `@mcp.tool() async def get_story(story_id: Annotated[int, Field(ge=1)]) -> dict`:
  same shape, calls `/items/{story_id}`, `hn_parse.summarize_item`.
- `if __name__ == "__main__": mcp.run()` guard (import-safety, same reason as
  the existing `mcp-hello-world` example).

**Acceptance criteria ("it works"):**

1. `python3 -c "import server"` succeeds with zero output and does not block
   (proves the `__main__` guard is correct — a regression here would hang the
   import).
2. `python3 test_hn_parse.py` (or via pytest) — pure, offline, no `httpx`
   import needed at all — passes fixtures built from the **actual captured
   JSON** in this note (the `claude`/`Philpax` search hit, the `pg` items/1
   thread) asserting: `_highlightResult` is absent from output;
   `summarize_hits({"hits": []})` returns `[]` without raising;
   `summarize_item` on a fixture with 8 children returns exactly 5
   `top_comments`.
3. `python3 test_hn_client.py` — offline, `httpx.MockTransport`-backed,
   asserts each of the three real gotchas found above by construction, not
   assumption:
   - a 200 JSON handler response → `fetch` returns the parsed dict.
   - a 404 handler response with `{"error":"Not Found","status":404}` →
     `fetch` raises `HNApiError` with `status=404` and a message that
     surfaces the JSON `error` field (proves the JSON-error path works).
   - a 500 handler response with an HTML body → `fetch` raises `HNApiError`
     with `status=500` and a message that does **not** contain a raw
     `JSONDecodeError` traceback (proves the HTML-fallback path works — this
     is the one that would silently break without today's live testing).
   - a handler that raises `httpx.TimeoutException` → `fetch` raises
     `HNApiError` with `status=None`.
4. `test_server.py` — offline, via the SDK's in-memory `Client(mcp)` —
   asserts `list_tools()` returns exactly `search_stories` and `get_story`
   with the expected required/optional parameters in their schemas (proves
   registration and schema derivation, the same shape of check
   `mcp-hello-world`'s test already established). **Explicitly does not**
   call the tools through this in-memory client, since the tool bodies
   perform real outbound HTTP to a live API — doing so would make the
   self-test network-dependent and flaky. This is a stated, deliberate
   design boundary, not an oversight: correctness of the HTTP-handling logic
   is proven by `test_hn_client.py`'s injected-mock coverage instead.
5. `README.md` documents the base URL/endpoints table, the three real error
   gotchas with the exact captured HTTP status/body shape from this note,
   the "why no live-network test" boundary decision, and the manual live
   check (`mcp dev server.py`, same pattern as `mcp-hello-world`) with an
   explicit instruction to try a real query and a real invalid `story_id`.

### Interfaces (stubs only)

```python
# hn_client.py
import httpx

HN_BASE_URL = "https://hn.algolia.com/api/v1"

class HNApiError(Exception):
    def __init__(self, status: int | None, message: str) -> None: ...

async def fetch(
    path: str,
    params: dict[str, str | int],
    client: httpx.AsyncClient,
) -> dict:
    """GET {HN_BASE_URL}{path} with params.

    Failure modes: raises HNApiError(status=<code>, message=...) on any
    non-2xx response (JSON error body preferred, falls back to truncated
    text if the body isn't JSON -- never lets JSONDecodeError escape from
    an error branch); raises HNApiError(status=None, message=...) on a
    timeout or connection failure. Never returns a partial/default dict
    in place of raising.
    """

# hn_parse.py  (no httpx import -- pure)
def summarize_hits(raw: dict) -> list[dict]: ...
def summarize_item(raw: dict) -> dict: ...

# server.py
from mcp.server import MCPServer
mcp = MCPServer("hn-search")

@mcp.tool()
async def search_stories(
    query: str,
    hits_per_page: int = 5,
    sort: str = "relevance",
) -> list[dict]: ...

@mcp.tool()
async def get_story(story_id: int) -> dict: ...
```

### Open questions

- The "10,000 requests/hour" figure for the HN Algolia API appears only in
  secondary sources (blog posts), never in an Algolia/HN primary statement I
  could find — do not build retry/backoff logic against this number as if
  confirmed; the build proposal above deliberately omits rate-limit handling
  for this reason.
- Whether `tags=(story,poll)`-style parenthesized OR logic is actually
  honored by the live API wasn't cleanly confirmed today (my own curl test
  was inconclusive due to shell/URL-encoding of parentheses); the build
  proposal sidesteps this by using a fixed `tags=story` rather than exposing
  arbitrary tag syntax to the model.
- PRs #5 and #6 (see "Note on backlog selection" above) are stale and should
  be triaged by the maintainer independent of this cycle's work.

## Sources

- [Hacker News API guide, cotera.co](https://cotera.co/articles/hacker-news-api-guide) — endpoint/param reference, cross-checked live, undated page.
- [Algolia HN Search MCP Server, DADL registry](https://www.dadl.ai/d/algolia-hn-search/) — corroboration only, "last reviewed 2026-04-04."
- [algolia/hn-search issue #230](https://github.com/algolia/hn-search/issues/230) — 1000-hit cap confirmation.
- Live `curl` requests to `https://hn.algolia.com/api/v1/*`, executed 2026-08-06 (this cycle) — primary source for all endpoint/error-shape claims.
- [python-sdk docs/servers/tools.md](https://github.com/modelcontextprotocol/python-sdk/blob/main/docs/servers/tools.md) — async tool support, fetched 2026-08-06.
- [quickstart-resources weather-server-python/weather.py](https://github.com/modelcontextprotocol/quickstart-resources/blob/main/weather-server-python/weather.py) — httpx-in-tool pattern; note it uses the removed v1 `FastMCP` import, do not copy that line.
- [httpx transports docs](https://www.python-httpx.org/advanced/transports/) — `MockTransport`, verified by direct execution in a throwaway venv, 2026-08-06.
- `knowledge/mcp-python-sdk.md` — reused, not re-derived (v2 import path, stdio mechanics, tool-error-vs-protocol-error split, all written 2026-08-05).
