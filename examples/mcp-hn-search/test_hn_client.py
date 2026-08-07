"""Offline self-test for hn_client.fetch -- httpx.MockTransport-backed,
zero network access. Reproduces the three real error shapes confirmed live
in research/2026-08-06-mcp-hn-search.md: a clean-JSON 404, an HTML 500,
and a timeout -- plus the success path.

Run: python3 test_hn_client.py
"""

import asyncio

import httpx

import hn_client


async def test_200_json_returns_parsed_dict() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"hits": [], "nbHits": 0})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        result = await hn_client.fetch("/search", {"query": "x"}, client)
    assert result == {"hits": [], "nbHits": 0}, result
    print("ok  200 JSON response -> fetch returns the parsed dict")


async def test_404_json_error_body_surfaces_error_field() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"error": "Not Found", "status": 404})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        try:
            await hn_client.fetch("/items/999999999", {}, client)
        except hn_client.HNApiError as exc:
            assert exc.status == 404, exc.status
            assert "Not Found" in exc.message, exc.message
        else:
            raise AssertionError("expected HNApiError to be raised")
    print("ok  404 JSON error body -> HNApiError(status=404), message surfaces the JSON 'error' field")


async def test_500_html_error_body_does_not_leak_json_decode_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            500,
            content="<html><body>Internal Server Error</body></html>",
            headers={"content-type": "text/html"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        try:
            await hn_client.fetch("/users/nonexistent", {}, client)
        except hn_client.HNApiError as exc:
            assert exc.status == 500, exc.status
            assert "JSONDecodeError" not in exc.message, exc.message
        else:
            raise AssertionError("expected HNApiError to be raised")
    print("ok  500 HTML error body -> HNApiError(status=500), no raw JSONDecodeError leaks into the message")


async def test_timeout_raises_hn_api_error_with_no_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.TimeoutException("simulated timeout", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        try:
            await hn_client.fetch("/search", {"query": "x"}, client)
        except hn_client.HNApiError as exc:
            assert exc.status is None, exc.status
        else:
            raise AssertionError("expected HNApiError to be raised")
    print("ok  handler raising httpx.TimeoutException -> HNApiError(status=None)")


async def _run_all() -> None:
    await test_200_json_returns_parsed_dict()
    await test_404_json_error_body_surfaces_error_field()
    await test_500_html_error_body_does_not_leak_json_decode_error()
    await test_timeout_raises_hn_api_error_with_no_status()


def main() -> int:
    asyncio.run(_run_all())
    print("\nAll 4 self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
