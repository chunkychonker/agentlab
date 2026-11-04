"""Check the loop against the real ``anthropic`` SDK over a mocked HTTP transport.

The stdlib self-test (test_tool_search.py) proves the loop logic with dicts.
This proves the boundary: that the pinned SDK accepts ``defer_loading`` and the
search tool on the wire, parses the tool-search blocks, and that ``sdk_send``'s
``model_dump`` round trip echoes them back byte-for-byte equal to what the
"server" sent. No key, no network: every request is answered by an
``httpx2.MockTransport`` with the docs-shaped turns in recorded.py.

Needs:  pip install -r requirements.txt
Run:    python sdk_contract_check.py
"""

from __future__ import annotations

import json

import anthropic
import httpx2  # the HTTP client anthropic 2.4.0 depends on (not httpx)

import recorded
from agent import MODEL, run_agent, sdk_send
from catalog import HOT_TOOLS, make_catalog
from toolset import SearchVariant, build_tools

EXPECTED_API_VERSION = "2026-10-21"


def _message(content: list[dict], stop_reason: str) -> dict:
    return {
        "id": "msg_mock", "type": "message", "role": "assistant", "model": MODEL,
        "content": content, "stop_reason": stop_reason, "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def main() -> int:
    """Exit 0 if every check holds; an AssertionError (exit 1) names the first that does not."""
    replies = [_message(recorded.search_then_call(), "tool_use"), _message(recorded.final_answer(), "end_turn")]
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        if request.url.path == "/v1/messages/count_tokens":
            return httpx2.Response(200, json={"input_tokens": 1})
        return httpx2.Response(200, json=replies.pop(0))

    client = anthropic.Anthropic(api_key="mock-key", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    tools = build_tools(make_catalog(40), HOT_TOOLS, SearchVariant.REGEX)
    result = run_agent(sdk_send(client), tools, "What's the weather in Madison?")
    client.messages.count_tokens(model=MODEL, tools=tools, messages=[{"role": "user", "content": "hi"}])

    first, second, count = (json.loads(r.content) for r in seen)
    assert all(r.headers["anthropic-version"] == EXPECTED_API_VERSION for r in seen), "API version header"
    print(f"ok  SDK {anthropic.__version__} sends anthropic-version {EXPECTED_API_VERSION}")

    for label, body in (("messages.create", first), ("count_tokens", count)):
        assert body["tools"] == tools, f"{label}: tools array reached the wire unchanged"
    print("ok  defer_loading and the search tool reach the wire unchanged (create and count_tokens)")

    echoed = second["messages"][1]["content"]
    assert echoed == recorded.search_then_call(), f"model_dump round trip changed the blocks: {echoed}"
    print("ok  parsed search blocks round-trip through model_dump to the exact JSON the server sent")

    assert [b["tool_use_id"] for b in second["messages"][2]["content"]] == [recorded.CLIENT_ID]
    assert second["tools"] == first["tools"]
    assert result.discovered == ("get_weather",) and result.text.startswith("It is 41F")
    print("ok  second request: tool_result only for the toolu_ id, same tools; loop returns the answer")

    print("\nAll 4 SDK contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
