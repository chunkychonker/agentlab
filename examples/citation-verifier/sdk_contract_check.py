"""The edge, through the real ``anthropic`` SDK over mocked HTTP. Needs the SDK, no key.

Checks that:
  1. ``live_demo.run`` sends both documents with citations enabled, and that a
     server answer whose pointers are right comes back through the SDK's
     typed models + ``model_dump(exclude_none=True)`` as all MATCH (exit 0).
  2. The same path exits 1 on an off-by-one server answer and on an answer
     with no citations at all.
  3. ``stream.accumulate`` agrees with the SDK's own stream accumulator on a
     ``citations_delta`` stream.

The fake server derives its citation offsets from the request body it
receives, not from constants shared with the code under test.

Check 3 imports ``accumulate_event`` from ``anthropic.lib.streaming._messages``,
a private module. If an SDK upgrade moves it, this check fails at import time;
the offline self-test is unaffected.
"""

from __future__ import annotations

import contextlib
import io
import json
import sys

import anthropic
import httpx2  # the HTTP client anthropic 2.x depends on (not httpx)
from anthropic.lib.streaming._messages import accumulate_event

import live_demo
from stream import accumulate


def _message(content: list[dict]) -> dict:
    return {
        "id": "msg_fake",
        "type": "message",
        "role": "assistant",
        "model": live_demo.MODEL,
        "content": content,
        "stop_reason": "end_turn",
        "stop_sequence": None,
        "usage": {"input_tokens": 1, "output_tokens": 1},
    }


def _answer_from(body: dict, shift: int) -> list[dict]:
    """Cite sentence 2 of document 0 and block 0 of document 1, offsets computed from ``body``."""
    lake, terrace = body["messages"][0]["content"][:2]
    text = lake["source"]["data"]
    sentence = "Its deepest point is 25 metres. "
    start = text.index(sentence)
    first_block = terrace["source"]["content"][0]["text"]
    return [
        {"type": "text", "text": "Lake Mendota is "},
        {
            "type": "text",
            "text": "25 metres deep at its deepest",
            "citations": [{
                "type": "char_location", "cited_text": sentence, "document_index": 0,
                "document_title": lake["title"], "start_char_index": start + shift,
                "end_char_index": start + len(sentence) + shift,
            }],
        },
        {"type": "text", "text": ", and "},
        {
            "type": "text",
            "text": "the terrace opened in 1928",
            "citations": [{
                "type": "content_block_location", "cited_text": first_block, "document_index": 1,
                "document_title": terrace["title"], "start_block_index": 0, "end_block_index": 1,
            }],
        },
        {"type": "text", "text": "."},
    ]


def _run_against(answer) -> tuple[int, list[httpx2.Request], str]:
    seen: list[httpx2.Request] = []

    def handler(request: httpx2.Request) -> httpx2.Response:
        seen.append(request)
        return httpx2.Response(200, json=_message(answer(json.loads(request.content))))

    client = anthropic.Anthropic(api_key="test-key", http_client=httpx2.Client(transport=httpx2.MockTransport(handler)))
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        code = live_demo.run(client)
    return code, seen, out.getvalue()


def check_happy_path() -> str:
    code, seen, out = _run_against(lambda body: _answer_from(body, shift=0))
    assert code == live_demo.EXIT_OK, out
    [request] = seen
    body = json.loads(request.content)
    docs = [b for b in body["messages"][0]["content"] if b["type"] == "document"]
    assert [d["citations"] for d in docs] == [{"enabled": True}] * 2, docs
    assert [d["source"]["type"] for d in docs] == ["text", "content"], docs
    assert "MATCH=2" in out and "uncited text blocks: [0, 2, 4]" in out, out
    return f"SDK {anthropic.__version__} (anthropic-version {request.headers['anthropic-version']}): both documents sent with citations enabled; 2/2 MATCH, exit 0"


def check_failures_exit_nonzero() -> str:
    code, _, out = _run_against(lambda body: _answer_from(body, shift=1))
    assert code == live_demo.EXIT_UNVERIFIED and "MISMATCH=1" in out, out
    code, _, out = _run_against(lambda body: [{"type": "text", "text": "No citations here."}])
    assert code == live_demo.EXIT_UNVERIFIED and "0 citation(s): none" in out, out
    return "off-by-one answer -> MISMATCH, exit 1; answer with zero citations -> exit 1"


def check_stream_matches_sdk() -> str:
    c1 = {"type": "char_location", "cited_text": "The grass is green. ", "document_index": 0,
          "document_title": "Example Document", "start_char_index": 0, "end_char_index": 20}
    c2 = dict(c1, cited_text="The sky is blue.", start_char_index=20, end_char_index=36)
    events = [
        {"type": "message_start", "message": _message([])},
        {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "According to the document, "}},
        {"type": "content_block_stop", "index": 0},
        {"type": "content_block_start", "index": 1, "content_block": {"type": "text", "text": ""}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": "grass is green"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "citations_delta", "citation": c1}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "text_delta", "text": ", sky is blue"}},
        {"type": "content_block_delta", "index": 1, "delta": {"type": "citations_delta", "citation": c2}},
        {"type": "content_block_stop", "index": 1},
        {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 9}},
        {"type": "message_stop"},
    ]
    snapshot = None
    for event in events:
        snapshot = accumulate_event(event=event, current_snapshot=snapshot, json_bufs={})
    sdk_blocks = [b.model_dump(exclude_none=True) for b in snapshot.content]
    ours = accumulate(events)
    assert ours == sdk_blocks, (ours, sdk_blocks)
    return "accumulate() == SDK accumulate_event() on a 2-block, 2-citations_delta stream"


def main() -> int:
    for check in (check_happy_path, check_failures_exit_nonzero, check_stream_matches_sdk):
        print(f"ok  {check()}")
    print("\nAll 3 SDK contract checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
