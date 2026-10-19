# Message Batches API

Verified 2026-10-19 from the [batch guide](https://platform.claude.com/docs/en/build-with-claude/batch-processing)
and the [results reference](https://platform.claude.com/docs/en/api/messages/batches/results).

- Endpoints: create / retrieve / results (JSONL stream) / cancel / list under
  `/v1/messages/batches`. No beta header. Flat 50% off (Haiku 4.5 $0.50/$2.50 per MTok).
- `processing_status`: `in_progress` | `canceling` | `ended`. `results_url` is null until ended.
- Result types: `succeeded` (has `message`), `errored` (`result.error.error.type`),
  `canceled`, `expired`. The last three are not billed.
- **Results arrive in any order**: join on `custom_id` (`^[a-zA-Z0-9_-]{1,64}$`, unique per batch).
- Per-request validation is deferred to batch end; `invalid_request_error` is
  not retryable, other error types are.
- Unsupported in batch: `stream`, `speed`, `max_tokens: 0`. Limits: 100k requests / 256 MB,
  24h expiry, results kept 29 days.
- Caching stacks with the batch discount but hits are best-effort; use the 1h TTL ([[prompt-caching]]).

Related: [[anthropic-models]], [[tool-failure-taxonomy]], [[sdk-retry-behavior]]
