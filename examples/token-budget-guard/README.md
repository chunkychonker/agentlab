# Token-budget guard for the hand-written loop

Before every request, the tool loop measures it with the free token-counting
endpoint (`client.messages.count_tokens`). If the counted input plus the reply
reserve (`max_tokens`) would go over a limit, the loop stops and returns a typed
`BudgetExceeded(turn, counted_tokens, limit)` without sending that request. It
never raises in that case, and it never truncates history.

From the research note:
[`research/2026-10-16-token-budget-guard.md`](../../research/2026-10-16-token-budget-guard.md).
The loop's shape comes from [`../minimal-agent-loop/`](../minimal-agent-loop/).

## Design (layers 1-3)

- **Intent:** stop the loop before it sends a request whose counted input
  tokens go over a limit. Out of scope: truncating or compacting history (see
  `context-editing-*` and `server-side-compaction`), cumulative billing across
  turns, and streaming.
- **Spec:** stop on turn *t* iff `counted(t) + MAX_TOKENS > limit`. Being exactly
  at the limit is allowed. A stopped request is never passed to `create`. If
  `count_tokens` errors, the error propagates and nothing is sent (the guard
  fails closed). Running out of `max_turns` still raises `RuntimeError`.
- **Interfaces:**
  - `budget.check_budget(counted_tokens, limit, turn, reserve=0) -> BudgetExceeded | None`
    is pure. It raises `ValueError` on a negative count or reserve, a limit of 0
    or less, or a turn below 1.
  - `agent.run_guarded_agent(client, user_message, *, token_limit, max_turns=5, verbose=False) -> str | BudgetExceeded`

| File | What it is |
|------|-----------|
| `budget.py` | The pure go/stop decision and the `BudgetExceeded` result type. No I/O, no SDK. |
| `agent.py` | The imperative shell: the guarded loop plus the live `main` (the only place the key is read). |
| `tools.py` | The safe calculator tool and its schema, **copied** from `minimal-agent-loop` so this directory runs on its own. |
| `test_guard.py` | Offline self-test using a fake client with scripted counts. |
| `requirements.txt` | `anthropic>=1.15.0,<2`. Only the live run needs it. |

## Run the self-test (no API key needed)

```bash
cd examples/token-budget-guard
python test_guard.py
```

Expected output:

```
ok  check_budget: under / at / one-over / reserve boundary
ok  check_budget raises ValueError on invalid arguments
ok  climbing counts stop on turn 3; the over-budget request is never sent
ok  generous limit: final text, one count before each create, same request
ok  count_tokens error propagates; create is never called (fail-closed)
ok  max_turns exhaustion still raises RuntimeError

All 6 self-tests passed.
```

In the stop scenario, the fake returns counts of 100, 400 and 900 against a
limit of 500 (plus the reserve). The test checks that the call order is exactly
`count, create, count, create, count`, so the turn-3 request was measured and
then dropped.

## Run it live (needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python agent.py               # default --limit 8000: runs to the answer
python agent.py --limit 1500  # tight: stops before sending (the reserve alone is 1024)
```

Each turn prints `counted=… +reserve=1024 limit=… -> ok|STOP`. If
`ANTHROPIC_API_KEY` is not set, `agent.py` prints a note and exits 0. The live
path was **not** run for this build because no key was available. The SDK
contract was checked offline against `anthropic==1.15.0`: the `count_tokens`
signature has no `max_tokens` parameter, `MessageTokensCount.input_tokens: int`
is real, and a mocked-transport run confirmed the request goes to
`POST /v1/messages/count_tokens` with `model/system/tools/messages` only.

## Things to know

- **The count is an estimate.** Real usage can differ slightly, and Anthropic
  may add unbilled system tokens. Treat the guard as a fence, not an exact meter.
- **Tokenizer-specific.** The count uses the tokenizer of `MODEL`
  (`claude-haiku-4-5`). Claude 4.7+ models count about 30% higher, so re-measure
  if you switch models.
- **Conservative compared with billing.** `count_tokens` ignores cache
  discounts, so the guard may stop a request that would actually be cheap if
  it hit the cache.
- **Fails closed.** If `count_tokens` is down, the agent stops instead of
  running unmetered. The cost is one extra free API call per turn. Its rate
  limits are separate from message creation.
- **Not supported here:** server tools, the MCP connector, and `url`/`file`
  sources make `count_tokens` return `invalid_request_error`. This loop uses
  only a client tool.
