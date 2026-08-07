"""The I/O edge for the HN Algolia API wrapper -- the only file here that
imports httpx.

Intent: perform a single GET against the HN Algolia Search API and turn
every real failure mode confirmed live in
../../research/2026-08-06-mcp-hn-search.md into one exception type
(`HNApiError`), never a raw, confusing `json.JSONDecodeError` or an
unstructured network exception.

The `client` parameter on `fetch` is required, not defaulted or
constructed internally -- that is the dependency-injection seam that makes
`test_hn_client.py` fully offline: production code (server.py) passes a
real `httpx.AsyncClient`, the test passes an `httpx.MockTransport`-backed
one.
"""

import json

import httpx

HN_BASE_URL = "https://hn.algolia.com/api/v1"

# How much of a non-JSON error body to keep in the exception message --
# HN's HTML error pages can run to several KB; a caller only needs enough
# to recognize the failure, not the whole page.
_ERROR_BODY_TRUNCATE_CHARS = 200


class HNApiError(Exception):
    """Raised for any HN Algolia API call that did not succeed.

    `status` is the HTTP status code, or `None` if the request never got a
    response at all (timeout or connection failure -- there is no status
    code to report in that case).
    """

    def __init__(self, status: int | None, message: str) -> None:
        self.status = status
        self.message = message
        super().__init__(message)


async def fetch(
    path: str,
    params: dict[str, str | int],
    client: httpx.AsyncClient,
) -> dict:
    """GET {HN_BASE_URL}{path} with params, using the caller-owned `client`.

    Failure modes:
      - Any non-2xx HTTP response: raises `HNApiError(status=<code>, ...)`.
        The HN API's error bodies are not reliably JSON (confirmed live:
        items/:id 404s return clean JSON, but users/:username 500s and
        malformed-numericFilters 400s return HTML) -- this function always
        attempts `response.json()` first and falls back to a truncated
        `response.text` if that raises `json.JSONDecodeError`. A raw
        `JSONDecodeError` never escapes from this branch.
      - `httpx.TimeoutException` or `httpx.ConnectError` while making the
        request: raises `HNApiError(status=None, ...)`.
    Never returns a partial or default dict in place of raising.
    """
    url = f"{HN_BASE_URL}{path}"
    try:
        response = await client.get(url, params=params)
    except (httpx.TimeoutException, httpx.ConnectError) as exc:
        raise HNApiError(status=None, message=f"HN API request failed: {exc}") from exc

    if response.is_success:
        return response.json()

    try:
        body = response.json()
        detail = body.get("error", body) if isinstance(body, dict) else body
    except json.JSONDecodeError:
        detail = response.text[:_ERROR_BODY_TRUNCATE_CHARS]

    raise HNApiError(
        status=response.status_code,
        message=f"HN API error {response.status_code}: {detail}",
    )
