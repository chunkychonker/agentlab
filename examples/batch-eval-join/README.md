# Batch eval join: Message Batches, polled and joined by `custom_id`

Every other billed run in this lab is synchronous. This example sends a small
fixed eval set as **one Message Batch** at batch pricing. Two pure functions
carry the logic. `poll_decision` decides from the batch's `processing_status`
whether to keep waiting. `join_results` matches each result line back to its
eval case by `custom_id`, because the API does not return results in
submission order.

From the research note:
[`research/2026-10-19-message-batches-eval.md`](../../research/2026-10-19-message-batches-eval.md).
Background: [`knowledge/message-batches.md`](../../knowledge/message-batches.md).

## Design (layers 1-3)

- **Intent:** run a fixed prompt set as one batch, decide when to stop
  polling, and join the results back to their inputs by `custom_id`. Out of
  scope: prompt caching, retry/resubmit orchestration, cancel, and the async
  client.
- **Spec:**
  - `build_requests` keeps case order. It rejects empty input, `max_tokens < 1`,
    duplicate ids, ids that don't match `^[a-zA-Z0-9_-]{1,64}$`, and batches
    over 100,000 requests or 256 MB.
  - `poll_decision`: `in_progress` and `canceling` give WAIT. `ended` gives
    FETCH. Any other status raises, so an API change can't cause an endless
    poll.
  - `join_results` returns outcomes in **case** order. It raises a distinct
    named error for a missing result, an unknown id, a duplicate result, or a
    malformed line. It never returns a partial list.
  - `grade`: errored, canceled and expired cases are `no_result`, never `fail`,
    because the model was never judged. The substring match ignores case.
  - `Errored.retryable` is false only for `invalid_request_error`, which has to
    be fixed before it is resent. It is a property derived from `error_type`,
    so the two fields can't disagree.
- **Interfaces:**
  - `plan.EvalCase(id, prompt, expected_substring)` checks itself when it is
    constructed.
  - `plan.build_requests(cases, model, max_tokens) -> list[dict]`
  - `plan.poll_decision(processing_status) -> PollDecision` (`WAIT` / `FETCH`)
  - `join.join_results(cases, result_lines) -> list[tuple[EvalCase, Outcome]]`,
    where `Outcome = Succeeded | Errored | Canceled | Expired`
  - `join.grade(case, outcome) -> Grade` (`PASS` / `FAIL` / `NO_RESULT`)
  - `batch_cli.submit(client, requests) -> str`
  - `batch_cli.wait_and_collect(client, batch_id, *, sleep, interval, on_poll=...) -> list[dict]`

| File | What it is |
|------|-----------|
| `plan.py` | Pure. Builds the request list and makes the poll decision. No SDK, no I/O. |
| `join.py` | Pure. Joins results by `custom_id`, defines the outcome union, and grades. It takes plain JSONL-shaped dicts. |
| `cost.py` | Pure. Batch-price arithmetic over a source-dated price table. |
| `batch_cli.py` | The imperative shell: SDK calls, the clock, the key, and printing. Its default mode is a dry run. |
| `fixtures/results.jsonl` | Four result lines in scrambled order: succeeded, errored `overloaded_error`, expired, errored `invalid_request_error`. |
| `test_batch.py` | Offline self-test. Stdlib only, no key, no network. |
| `check_sdk_shape.py` | Contract check against the installed SDK. Needs `requirements.txt`, but no key and no network. |
| `requirements.txt` | `anthropic>=1.15.0,<2`. Only `--live` and `check_sdk_shape.py` need it. |

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/batch-eval-join
python3 test_batch.py
```

Expected output:

```
ok  join returns case order from a scrambled results file
ok  invalid_request_error is not retryable; overloaded/api errors are
ok  errored/expired/canceled grade no_result; wrong text grades fail
ok  missing, unknown, duplicate result, duplicate case, bad id: 5 named errors
ok  unknown result type or missing field raises MalformedResult
ok  build_requests keeps order and rejects empty/0 tokens/oversize
ok  poll_decision: in_progress/canceling wait, ended fetch, else raise
ok  wait_and_collect: 3 retrieves, 2 sleeps, results only after ended
ok  an unknown status raises and results is never called
ok  batch cost is 50% of the dated standard price
ok  dry run and keyless --live never import the SDK

All 11 self-tests passed.
```

`wait_and_collect` has no default `sleep`. The test passes `slept.append` and
checks that the fake client saw exactly `retrieve, retrieve, retrieve, results`
with two 60 s sleeps in between, and that `results` was never called before
`ended`.

## Check the fixture against the real SDK (no key, no network)

```bash
pip install -r requirements.txt
python3 check_sdk_shape.py
```

This prints four `ok` lines. The check confirms three things:

1. The hand-written fixture validates as the SDK's
   `MessageBatchIndividualResponse`, and `model_dump(mode="json")` of it joins
   to the same outcomes as the raw JSONL. The live shell takes the
   `model_dump` path and the offline test takes the raw path.
2. `poll_decision` accepts every value in the SDK's `processing_status`
   `Literal`.
3. `batch_cli._run_live` runs end to end through a real `anthropic.Anthropic`
   client over an `httpx` `MockTransport`, and nothing leaves the process.

## Dry run (the default) and the live run (billed, opt-in)

```bash
python3 batch_cli.py          # prints the 4 request payloads and a cost ceiling; sends nothing
python3 batch_cli.py --live   # needs ANTHROPIC_API_KEY; submits a real batch
```

**`--live` is billed.** It submits 4 requests to `claude-haiku-4-5` with
`max_tokens=64`. The output side is capped at 256 tokens, which is $0.00064
at batch price. Input is a few dozen tokens per request. Most batches end
within an hour, so the live run polls once a minute until then. Without a key,
`--live` exits 2 and sends nothing. It does not fall back to a dry run, because
you explicitly asked for a live run.

The model id lives in the one constant `MODEL` in `batch_cli.py`.

**Price claim, dated 2026-10-19** (from the
[batch processing guide](https://platform.claude.com/docs/en/build-with-claude/batch-processing)):
batches are a flat 50% off the standard price. That makes Haiku 4.5
$0.50 in / $2.50 out per MTok, and Sonnet 5 $1 / $5. `cost.py` stores the
standard prices together with `BATCH_DISCOUNT = 0.5` and the fetch date. It
raises on a model it has no price for rather than guessing. Re-check the page
before relying on these numbers.

## Findings from building it

- **The SDK answers one of the note's open questions.** In `anthropic` 1.15.0,
  `batches.results(id)` first calls `retrieve` itself. If `results_url` is
  null, it raises `AnthropicError("No results_url ... Has it finished
  processing?")` on the client, before any HTTP request to the results
  endpoint. So with the SDK, requesting results early fails loudly; it does not
  return partial data. The HTTP status the raw endpoint returns is still
  unverified.
- **That costs one extra retrieve.** A live run makes N poll retrieves, one
  more retrieve inside `results()`, and then the JSONL download. The
  mocked-transport check asserts this exact sequence.
- **The verified field names in SDK 1.15.0:** `custom_id`, `result.type`, and
  `result.error.error.type`. `request_id` lives on the `ErrorResponse`
  (`result.error.request_id`), not on the result itself.
- **The SDK uses `httpx2`.** `anthropic` 1.15.0 depends on `httpx2`, not
  `httpx`. `check_sdk_shape.py` imports `httpx2` and falls back to `httpx`.

## Assumptions and gaps

- **The fixture is hand-written, not recorded.** There was no key this cycle.
  It is written in the documented shape, and `check_sdk_shape.py` validates it
  against the SDK models. A real `--live` transcript has not been captured yet.
- `Succeeded.text` joins the message's `text` blocks. If a succeeded message
  has no text block, the text is `""` and the case grades `fail`. That's a
  faithful reading of the response, not a stand-in for an error.
- `usage.service_tier` is kept as `str | None`. The live run prints it, but
  nothing asserts that it is `"batch"`, because the note could not confirm the
  field is reliably set.
- The dry run doesn't count input tokens. Counting would need the network, so
  it prints an exact output-side ceiling and a character count instead.
- `wait_and_collect` has no poll cap of its own. It relies on the API to end
  every batch within its 24-hour expiry. An unknown status still raises
  immediately.
- The docs recommend dry-running the request shape against the synchronous
  Messages API, because per-request params are only validated when the whole
  batch ends. This example doesn't do that. The `invalid_request_error` fixture
  line shows what you get back if you skip it.
