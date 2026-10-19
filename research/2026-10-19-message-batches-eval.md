# Message Batches API for an eval run (2026-10-19)

## Question
What exactly do the Batches endpoints, status values, and result types look like, so a pure poll-decision and a custom_id results join can be built and tested offline?

## Findings
Primary sources, fetched 2026-10-19: [Batch processing guide](https://platform.claude.com/docs/en/build-with-claude/batch-processing) and [Batch results API reference](https://platform.claude.com/docs/en/api/messages/batches/results). Both are current docs. The `hn-search` MCP server failed to connect this cycle (missing venv), so there is no practitioner-reception data. Everything below is vendor docs only.

- **Flow:** `POST /v1/messages/batches` creates the batch, `GET .../{id}` retrieves it, and `GET .../{id}/results` streams JSONL. There is also a cancel endpoint and a list endpoint. There is no beta header.
- **Python SDK:** `client.messages.batches.create(requests=[Request(custom_id=..., params=MessageCreateParamsNonStreaming(...))])`, `.retrieve(id)`, `.results(id)` (an iterator of per-line objects), `.cancel(id)`. The imports are `from anthropic.types.message_create_params import MessageCreateParamsNonStreaming` and `from anthropic.types.messages.batch_create_params import Request`.
- **`custom_id`:** must match `^[a-zA-Z0-9_-]{1,64}$` and be unique within the batch.
- **Limits:** at most 100,000 requests or 256 MB per batch, whichever comes first. Most batches finish within 1 hour. A batch expires after 24 hours. Results stay downloadable for 29 days after creation.
- **Batch object:** `processing_status` is `in_progress`, `canceling`, or `ended`. `request_counts` has `processing`, `succeeded`, `errored`, `canceled`, `expired`. `results_url` is `null` until the batch ends. The batch also carries `created_at`, `expires_at`, `ended_at`, and `cancel_initiated_at`.
- **Results:** each JSONL line is `{"custom_id", "result": {"type": ...}}`. The four types are:
  - `succeeded`: carries a `message`, whose `usage.service_tier` can be `"batch"`.
  - `errored`: carries `error` (an ErrorResponse), so the type sits at `result.error.error.type`, plus `request_id`.
  - `canceled`: no payload.
  - `expired`: no payload.
  The docs say errored, canceled and expired requests are not billed.
- **Retry rule from the docs' own snippet:** an `errored` result of type `invalid_request_error` must be fixed before it is resent. Any other error type can be retried directly.
- **Ordering:** both docs say results can come back in any order. The docs' own example shows the second request returned first. Join on `custom_id` only.
- **Validation is deferred:** per-request `params` are validated asynchronously, and the errors appear in the results only when the whole batch ends. Dry-run the request shape against the sync Messages API first.
- **Unsupported in a batch:** `stream: true`, `speed` (fast mode), and `max_tokens: 0`, which means no cache pre-warming. Batches may slightly overshoot the workspace spend limit.
- **Pricing:** a flat 50% off. Haiku 4.5 is $0.50 in / $2.50 out per MTok. Sonnet 5 is $1 / $5.
  - Caching discounts stack with the batch discount, but cache hits are best-effort (30-98%).
  - The docs suggest the 1-hour TTL for batches. See [[prompt-caching]].
  - The pricing table now also lists newer models (`claude-haiku-5-5`, `claude-sonnet-5-5`, `claude-opus-5-5`). `knowledge/anthropic-models.md` predates them, so re-check it. The builder should keep the model in one constant, defaulting to `claude-haiku-4-5`.
- **I did not verify:** the exact field names on SDK result objects beyond what the docs' snippets use (`result.custom_id`, `result.result.type`, `.error.error.type`). The offline tests should build `SimpleNamespace` fakes from those names and the builder should confirm them against the installed SDK. I also did not verify the HTTP status when `results` is requested before the batch ends.

## Build proposal
**Directory:** `examples/batch-eval-join/`. Checked: it is not in `examples/` and there is no open PR (`gh pr list` returned nothing).

**Intent.** Run a small fixed eval prompt set as one batch. Decide from the batch object whether to keep polling, and join results back to the inputs by `custom_id`. Out of scope: the live billed run (opt-in only), prompt caching, retry/resubmit orchestration, and cancel.

**Files (stdlib-only core, `anthropic>=1.15.0,<2` for the live shell only):**
- `plan.py` (pure):
  - `EvalCase(id, prompt, expected_substring)`.
  - `build_requests(cases, model, max_tokens) -> list[dict]`. It raises `ValueError` on a duplicate id, an id not matching the `custom_id` regex, an empty case list, more than 100,000 cases, or `max_tokens < 1`.
  - `PollDecision` as an enum: `WAIT`, `FETCH`.
  - `poll_decision(processing_status) -> PollDecision`. `in_progress` and `canceling` give `WAIT`, `ended` gives `FETCH`. Any other value raises `ValueError`, so an API change fails loudly instead of waiting forever.
- `join.py` (pure):
  - `Outcome` as a union of `Succeeded(text, output_tokens)`, `Errored(error_type, retryable)`, `Canceled`, `Expired`.
  - `join_results(cases, result_lines) -> list[tuple[EvalCase, Outcome]]`. The output is in case order, not result order.
  - It raises `UnknownCustomId` for a result with no case, and `MissingResult` for a case with no result. It raises on a duplicate `custom_id` in the results. Each failure names the id.
  - `grade(case, outcome) -> "pass" | "fail" | "no_result"`. Errored, canceled and expired are `no_result`, never `fail`.
- `batch_cli.py` (shell):
  - `submit(client, requests)` and `wait_and_collect(client, batch_id, sleep=time.sleep, interval=60)`.
  - `main` reads the key. With no `--live` flag it prints the request payload and the estimated batch cost, and exits 0 without a network call.
- `test_batch.py`, offline, stdlib only. A recorded `fixtures/results.jsonl` holds 4 lines in scrambled order: one succeeded, one errored with `invalid_request_error`, one errored with `overloaded_error`, one expired.

**Acceptance criteria:**
1. `python test_batch.py` exits 0 with no key and no network.
2. `join_results` returns outcomes in case order even though the fixture is scrambled.
3. `invalid_request_error` gives `retryable=False`. `overloaded_error` and `api_error` give `retryable=True`.
4. Expired and errored cases grade `no_result`. A succeeded case with the wrong text grades `fail`.
5. A missing result, an unknown id, a duplicate result id, a duplicate case id and a bad `custom_id` each raise a distinct, named error.
6. `poll_decision("weird")` raises.
7. `wait_and_collect` is tested with a fake client scripted `in_progress, in_progress, ended` and an injected no-op sleep. It asserts exactly 3 retrieves and that `results` is called only after `ended`.
8. The README says plainly that the live run is billed and opt-in, and records the 50% price as a source-dated claim.

## Open questions
- Whether `results` before `ended` returns an error or partial data. The docs imply `results_url` is null until ended.
- The SDK's exact result object attribute names (see above).
- Whether `usage.service_tier == "batch"` is reliably set. It could serve as a cheap live-run assertion.
