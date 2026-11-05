"""Check the loop against the real ``anthropic`` SDK over a mocked HTTP transport.

The stdlib self-test proves the loop logic with dicts. This proves the
boundary, which the research note left open. It checks that the pinned SDK
puts ``allowed_callers`` and the code-execution tool on the wire unchanged,
that it parses ``caller`` on ``tool_use`` and the top-level ``container``,
that ``sdk_send``'s ``model_dump`` echoes the paused turn back exactly as
the "server" sent it, and that ``container`` goes out as a plain string. No
key, no network: an ``httpx2.MockTransport`` serves the fixtures/ bodies.

Needs:  pip install -r requirements.txt
Run:    python sdk_contract_check.py
"""

from __future__ import annotations

import json
from pathlib import Path

import anthropic
import httpx2  # the HTTP client anthropic 2.x depends on (not httpx)

from demo_tools import HANDLERS, TOOLS
from loop import run
from main import MAX_TOKENS, MODEL, sdk_send

EXPECTED_API_VERSION = "2026-10-21"
FIXTURES = Path(__file__).parent / "fixtures"


def _bodies(*names: str) -> list[dict]:
    return [json.loads((FIXTURES / f"{name}.json").read_text()) for name in names]


def _drive(*names: str):
    """Run the loop with the SDK client, serving the named fixtures in order. Returns (result, request bodies, headers)."""
    replies = _bodies(*names)
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=replies.pop(0))

    client = anthropic.Anthropic(api_key="mock-key", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    result = run(sdk_send(client, MODEL, MAX_TOKENS), "Which regions missed Q3 target?", TOOLS, HANDLERS)
    return result, [json.loads(r.content) for r in seen], [r.headers for r in seen]


def main() -> int:
    """Exit 0 if every check holds. Otherwise an AssertionError (exit 1) names the first that does not."""
    paused, final = _bodies("paused_three_calls", "final")
    result, (first, second), headers = _drive("paused_three_calls", "final")

    assert all(h["anthropic-version"] == EXPECTED_API_VERSION for h in headers), "API version header"
    print(f"ok  SDK {anthropic.__version__} sends anthropic-version {EXPECTED_API_VERSION}")

    assert first["tools"] == TOOLS and second["tools"] == TOOLS, "tools changed on the wire"
    assert first["model"] == MODEL and "container" not in first
    print("ok  allowed_callers + code_execution_20260120 reach the wire unchanged, on both requests")

    assert second["messages"][1]["content"] == paused["content"], "model_dump round trip changed the paused turn"
    print("ok  caller-tagged tool_use + server_tool_use round-trip through model_dump to the exact server JSON")

    assert second["container"] == "container_xyz789", f"container sent as {second.get('container')!r}"
    reply_blocks = second["messages"][2]["content"]
    assert [b["type"] for b in reply_blocks] == ["tool_result"] * 3
    assert [b["tool_use_id"] for b in reply_blocks] == ["toolu_01", "toolu_02", "toolu_03"]
    print("ok  request 2: container passed as a plain string; reply is 3 tool_results only, in call order")

    assert result.stdout == final["content"][0]["content"]["stdout"]
    assert result.final_text == final["content"][1]["text"] and result.programmatic_calls == 3
    print("ok  code_execution_tool_result parsed: RunResult carries stdout and the final text")

    mixed_result, (_, mixed_second), _ = _drive("mixed_direct_and_programmatic", "final")
    mixed = _bodies("mixed_direct_and_programmatic")[0]
    assert mixed_second["messages"][1]["content"] == mixed["content"], "direct caller lost in round trip"
    assert (mixed_result.direct_calls, mixed_result.programmatic_calls) == (1, 1)
    print('ok  caller {"type": "direct"} also round-trips; mixed turn counted 1 direct + 1 programmatic')

    print("\nAll 6 SDK contract checks passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
