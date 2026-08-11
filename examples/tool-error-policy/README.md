# Tool error policy: retry / report / abort

A hand-written tool-use loop whose response to a **failing tool call** is decided
by a pure policy function returning one of three dispositions:

| Disposition | For | Who handles it | What the model sees |
|---|---|---|---|
| **Retry** | transient — connection reset, upstream 503/429 | your loop, locally, with bounded backoff | nothing at all |
| **Report** | recoverable-by-model — bad argument, not found, unknown tool name | the model | `tool_result` with `is_error: true` and an actionable message |
| **Abort** | terminal — auth denied, budget gone, the model stuck repeating a failing call | your loop, by raising | nothing; the run stops |

Conflating these is the usual bug. Burning a model turn to say "try again" for a
connection reset costs tokens and latency for a decision that needs neither;
locally retrying a permission denial just spends money faster.

From the research note:
[`research/2026-08-10-tool-error-handling-retries.md`](../../research/2026-08-10-tool-error-handling-retries.md).
Background: [`knowledge/tool-failure-taxonomy.md`](../../knowledge/tool-failure-taxonomy.md),
[`knowledge/sdk-retry-behavior.md`](../../knowledge/sdk-retry-behavior.md).

## What's here

| File | What it is |
|------|-----------|
| `policy.py` | The pure core. `classify()`, `backoff_delay()`, `jitter_factor()`, `unknown_tool_message()`, `call_key()`, `is_repeat_exhausted()`. Imports nothing from `anthropic`, does no I/O, never sleeps. |
| `agent.py` | The imperative shell. `call_tool_with_retry()` and `run_agent()`, plus the demo `fetch_metric` tool. Owns the Messages API calls, the clock, and the randomness. |
| `test_agent.py` | Offline self-test: 14 assertions covering the eight acceptance criteria. No key, no network, no waiting. |
| `requirements.txt` | `anthropic>=0.120.0` — for the **live run only**. |

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/tool-error-policy
python test_agent.py
```

Expected output:

```
ok  transient failures are retried locally and never reach the model
ok  exhausted transient retries degrade to one is_error report
ok  a fatal tool error aborts the run with zero retries
ok  an unknown tool name is reported with the real tool names
ok  backoff follows the documented sequence, capped, jitter applied
ok  every tool_use in a turn gets exactly one matching tool_result
ok  a model repeating one failing call is aborted before max_turns
ok  exhausting max_turns raises rather than returning a partial answer
ok  classify maps transient/fatal/unknown to retry/abort/report
ok  jitter factor stays in [0.75, 1.0] and only ever shortens delays
ok  call keys are order-insensitive and the repeat guard trips on time
ok  unknown_tool_message names the alternatives and rejects an empty set
ok  run_agent refuses to start with no tools
ok  a max_attempts below 1 is rejected before the tool is ever called

All 14 self-tests passed in 0ms.
```

Verifiable, not hand-copied:
`python3 check_transcript.py ../tool-error-policy -- python3 test_agent.py` from
[`examples/readme-transcript-check`](../readme-transcript-check/) compares this
block against the real thing. Note the caveat there: the `0ms` is a *measured*
duration, so this is the one transcript in the repo that exact-match makes
machine-dependent.

That sub-millisecond total is the point: `sleep` and `jitter` are **required parameters** of
`run_agent()` and `call_tool_with_retry()`, bound to `time.sleep` /
`random.random` only inside `main()`. A test cannot accidentally wait on a wall
clock, and the final assertion in `test_agent.py` fails the suite if anything
takes longer than a second.

## Run it live (needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python agent.py
```

Two demo runs against the one `fetch_metric` tool:

1. **`flaky`** raises `TransientToolError` twice, then succeeds. The loop sleeps
   ~0.5s then ~1.0s and retries locally; the model never learns anything went
   wrong and answers normally.
2. **`forbidden`** raises `FatalToolError`. The run aborts on the first turn with
   zero retries and zero extra model calls.

Without `ANTHROPIC_API_KEY` set, `agent.py` prints a one-line note and exits 0.

Model id is the constant `MODEL` at the top of `agent.py` (default
`claude-haiku-4-5`). See
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## The two retry layers, which are not the same layer

This is the distinction the example exists to make concrete.

- **Layer A — the API call to Anthropic.** Already solved by the SDK. It retries
  408/409/429/5xx twice by default with `min(0.5 * 2**n, 8.0)` and a *multiplicative*
  0.75–1.0 jitter. You configure it, you don't write it. `main()` sets it
  explicitly (`Anthropic(max_retries=API_MAX_RETRIES)`) so the two layers are
  never confused at a glance.
- **Layer B — the tool function itself.** *Nothing retries this for you.* That's
  everything else in this directory.

`BACKOFF_BASE = 0.5` and `BACKOFF_CAP = 8.0` deliberately mirror the SDK's own
`INITIAL_RETRY_DELAY` / `MAX_RETRY_DELAY`, and `jitter_factor()` mirrors its
`1 - 0.25 * random()` so jitter only ever *shortens* a delay — which is what
makes `BACKOFF_CAP` a true ceiling rather than an average.

## Why it raises instead of returning the last message

The SDK's tool runner has a silent-truncation shape: hitting `max_iterations`
exits its loop *normally*, so `until_done()` returns the last message with no
exception and no flag. A truncated run is indistinguishable from a finished one
unless you check `stop_reason == "tool_use"` yourself.

`run_agent()` inverts that. Exhausting `max_turns`, a terminal tool failure, and
the repeat guard all raise `AgentAborted`. There is no code path that returns a
partial answer as if it were an answer.

## The repeat guard

The taxonomy row people skip is *terminal*, and its most common instance is a
model that keeps re-issuing an identical call that has already failed. Every
reported failure is keyed by `call_key(name, input)` (order-insensitive JSON), and
once one key has been reported past `REPEAT_LIMIT` the run aborts rather than
spending another turn. `test_repeat_guard_aborts_before_max_turns` asserts this
fires on turn 3 of a possible 6 — the guard, not the turn budget, is what stops it.

## Invariants the loop holds

1. Every `tool_use` block is answered by exactly one `tool_result` with a matching
   `tool_use_id`, in the immediately following user turn. Asserted structurally
   across whole transcripts by `assert_paired_tool_results()`, not case by case.
2. The user turn contains tool_result blocks and nothing else, so they are
   necessarily first — text before a tool_result is a 400 from the API.
3. `is_error: true` is set **iff** the disposition was Report. Never on success,
   not even as `is_error: False`.
4. A local retry never exceeds `MAX_ATTEMPTS` invocations, and no delay exceeds
   `BACKOFF_CAP`.
5. Termination is loud (see above).

## Deviations from the research note's Layer-3 sketch

Three, all in the direction of the repo's correctness posture:

- **`Decision` is a tagged union** (`Retry | Report | Abort`) rather than one
  dataclass with a `Disposition` enum and a `message` that is `""` when
  retrying. A retry decision carrying an error message is an illegal state, so
  it isn't representable: `Retry` has no message field.
- **`call_tool_with_retry` returns `Succeeded | policy.Report`** instead of
  raising a private `_Reportable`. A report is an expected outcome, not an
  exceptional one; only `Abort` leaves via an exception, because there is
  nothing sensible to put in a `tool_result` for it.
- **`sleep` and `jitter` have no defaults** on `run_agent`. The note's invariant 4
  says they should default only in `main()`, while its interface sketch showed
  defaults on `run_agent`; the stricter reading wins, so callers must state where
  their clock and randomness come from.

Tools are also passed in as a `Sequence[Tool]` (name + description +
input_schema + implementation in one frozen dataclass) rather than read from a
module global, so the advertised schema and the dispatch table cannot drift and
the demo tool's flaky-counter is per-run state rather than a module global.

## Explicitly out of scope

Transport-level retry (the SDK's, configured at the edge and otherwise left
alone), `strict: true` schema-constrained sampling, streaming, async, the SDK
tool runner, cross-run circuit breakers, and persistence. This example also
deliberately does not touch
[`examples/minimal-agent-loop/`](../minimal-agent-loop/), whose naive
`"Error: ..."`-string handling was correct scope for *that* increment — it was
about loop mechanics, this one is about what happens when the tool fails.
