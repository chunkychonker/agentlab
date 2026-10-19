"""Contract check against the installed anthropic SDK (needs requirements.txt,
no key, no network).

The offline self-test feeds join_results plain dicts from fixtures/results.jsonl.
The live shell feeds it ``line.model_dump(mode="json")`` from SDK objects. This
script proves the two paths agree, that the SDK's processing_status values
are exactly the ones poll_decision knows, and that the live shell runs end to
end through the real SDK client over a mocked HTTP transport (nothing leaves
the process).

Run:
    pip install -r requirements.txt
    python check_sdk_shape.py
"""

from __future__ import annotations

import contextlib
import io
import json
import sys
import typing
from pathlib import Path

import anthropic
from anthropic.types.messages import MessageBatch, MessageBatchIndividualResponse

try:  # anthropic 1.15.0 depends on httpx2; older 1.x releases used httpx
    import httpx2 as httpx
except ImportError:
    import httpx

import batch_cli
from batch_cli import EVAL_CASES
from join import join_results
from plan import poll_decision

FIXTURE = Path(__file__).parent / "fixtures" / "results.jsonl"


def main() -> int:
    raw = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line]
    via_sdk = [
        MessageBatchIndividualResponse.model_validate(line).model_dump(mode="json")
        for line in raw
    ]
    assert join_results(EVAL_CASES, via_sdk) == join_results(EVAL_CASES, raw)
    print(f"ok  fixture validates as MessageBatchIndividualResponse (anthropic {anthropic.__version__})")
    print("ok  model_dump(mode='json') joins to the same outcomes as the raw JSONL")

    statuses = typing.get_args(MessageBatch.model_fields["processing_status"].annotation)
    for status in statuses:
        poll_decision(status)  # raises on any status the core does not know
    print(f"ok  poll_decision handles every SDK processing_status {sorted(statuses)}")

    _check_live_shell_over_mock_transport(FIXTURE.read_bytes())
    return 0


def _batch_json(status: str) -> dict:
    ended = status == "ended"
    return {
        "id": "msgbatch_mock",
        "type": "message_batch",
        "processing_status": status,
        "request_counts": {
            "processing": 0 if ended else 4,
            "succeeded": 1 if ended else 0,
            "errored": 2 if ended else 0,
            "canceled": 0,
            "expired": 1 if ended else 0,
        },
        "created_at": "2026-10-19T00:00:00Z",
        "expires_at": "2026-10-20T00:00:00Z",
        "ended_at": "2026-10-19T00:05:00Z" if ended else None,
        "cancel_initiated_at": None,
        "archived_at": None,
        "results_url": (
            "https://api.anthropic.com/v1/messages/batches/msgbatch_mock/results"
            if ended
            else None
        ),
    }


def _check_live_shell_over_mock_transport(results_body: bytes) -> None:
    statuses = iter(["in_progress", "ended"])
    calls: list[str] = []

    def handler(request):
        calls.append(f"{request.method} {request.url.path}")
        if request.method == "POST":
            body = json.loads(request.content)
            assert [r["custom_id"] for r in body["requests"]] == [c.id for c in EVAL_CASES]
            return httpx.Response(200, json=_batch_json("in_progress"))
        if request.url.path.endswith("/results"):
            return httpx.Response(200, content=results_body)
        # The SDK's results() re-retrieves once more, so stay "ended" after that.
        return httpx.Response(200, json=_batch_json(next(statuses, "ended")))

    client = anthropic.Anthropic(
        api_key="mock-not-a-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        max_retries=0,
    )
    requests = batch_cli.build_requests(EVAL_CASES, batch_cli.MODEL, batch_cli.MAX_TOKENS)
    out = io.StringIO()
    with contextlib.redirect_stdout(out):
        assert batch_cli._run_live(client, requests, interval=0.0) == 0
    assert calls == [
        "POST /v1/messages/batches",
        "GET /v1/messages/batches/msgbatch_mock",  # poll 1: in_progress
        "GET /v1/messages/batches/msgbatch_mock",  # poll 2: ended
        "GET /v1/messages/batches/msgbatch_mock",  # inside the SDK's results()
        "GET /v1/messages/batches/msgbatch_mock/results",
    ], calls
    assert "1/4 passed" in out.getvalue(), out.getvalue()
    print("ok  live shell runs end to end through the SDK over a mocked transport")


if __name__ == "__main__":
    sys.exit(main())
