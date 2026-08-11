# Context editing preview: what would `clear_tool_uses_20250919` do to my transcript?

Server-side context editing prunes old `tool_result` bodies out of a request
before the model sees it. The catch is that **nothing local changes** — your
client keeps the full, unmodified history, and the edit happens per-request on
Anthropic's side. So you cannot find out what it did by printing your own
messages list, and the obvious way to find out is to pay for a generation.

This example finds out for **$0**. It counts the same request twice through the
[token-counting endpoint](https://platform.claude.com/docs/en/build-with-claude/token-counting),
which is free and rate-limited separately from message creation:

| Call | `context_management` | What comes back |
|---|---|---|
| 1 | omitted | `input_tokens` — the request untouched |
| 2 | your policy | `input_tokens` after editing, plus `context_management.original_input_tokens` |

The delta is the saving. No `messages.create`, no tokens generated, no bill.

From the research note:
[`research/2026-08-11-context-editing-preview.md`](../../research/2026-08-11-context-editing-preview.md).
Background: [`knowledge/context-editing.md`](../../knowledge/context-editing.md),
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## What's here

| File | What it is |
|------|-----------|
| `policy.py` | A `clear_tool_uses_20250919` edit that is well-formed by construction. Validates in `__post_init__`, serialises in `to_edit()`. No I/O, no `anthropic` import. |
| `transcript.py` | The synthetic fixture: `rounds` complete tool-use round trips with fixed-size results. Pure and deterministic. |
| `preview.py` | The pure core. Declares the `CountTokens` and `EditPolicy` interfaces it needs, turns two `TokenCount`s into one `PreviewReport`. |
| `main.py` | The imperative shell. The only file that reads the env var, imports the SDK (lazily), or prints. |
| `test_preview.py` | Offline self-test: 25 assertions, no key, no network, no SDK installed. |
| `requirements.txt` | `anthropic==0.121.0` — for the **live run only**. |

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/context-editing-preview
python test_preview.py
```

Expected output:

```
ok  an unset optional is omitted, not emitted as null
ok  clear_at_least is in input tokens and keep is in tool uses
ok  to_config wraps the edit in the API's edits list
ok  exclude_tools is normalised to a tuple at construction
ok  keep < 0 is rejected at construction
ok  a trigger value below 1 is rejected at construction
ok  clear_at_least below 1 is rejected at construction
ok  a blank excluded tool name is rejected at construction
ok  an unknown trigger kind is rejected at construction
ok  a bare str of excluded tools is rejected at construction
ok  an applied edit reports 45000 tokens saved and 64.3%
ok  an unapplied edit reports 0 saved rather than raising or guessing
ok  the same request is counted twice, once plain, once edited
ok  two counts that disagree about the original raise, not report
ok  an impossible report is unconstructible, so no negative saving
ok  a zero-token original gives 0.0%, not a ZeroDivisionError
ok  a negative token count is rejected at the boundary
ok  the adapter sends the beta and the policy's exact edit dict
ok  an absent context_management object becomes original=None
ok  an API error propagates instead of becoming a zero-saving report
ok  every tool_use is answered by exactly one matching tool_result
ok  the same arguments always build the same transcript
ok  a transcript with no rounds or empty results is rejected
ok  the report prints the pasteable config and the delta
ok  an unapplied edit is explained, not silently reported as 0

All 25 self-tests passed with no key and no network.
```

Verifiable, not hand-copied: from
[`examples/readme-transcript-check`](../readme-transcript-check/), run
`python3 check_transcript.py ../context-editing-preview -- python3 test_preview.py`
to compare this block against the real thing.

The last two assertions in the suite are the ones that make "no network" a fact
rather than a claim: `"anthropic" not in sys.modules` after every test has run
(the core, the adapter and the renderer were all exercised without the SDK ever
being imported), and a sub-second wall-clock bound.

## Run it live (needs a key; still costs nothing)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python main.py
```

It builds a 20-round synthetic transcript (41 messages, 1200-character tool
results), previews it under `keep=3` with a `{"type": "tool_uses", "value": 5}`
trigger, and prints the report plus the exact `context_management` object it
sent, so you can paste that straight into your own `messages.create`.

Without `ANTHROPIC_API_KEY` set it prints one line to stderr and **exits 1** —
deliberately non-zero, because a preview that could not run is not a preview
reporting no saving.

### Live transcript: NOT YET CAPTURED

No `ANTHROPIC_API_KEY` was available in the environment where this example was
built, so the live numbers are **not** reproduced here. Nothing has been
invented to fill the gap: there is no fabricated token count anywhere in this
directory. When someone runs it with a key, paste the real stdout under an
"Expected output" heading here and add the `check_transcript.py` invocation for
it, exactly as the self-test block above has.

What *was* verified without a key (see "How this was verified" below) is the
whole path up to the wire: the request the SDK serialises, the beta header it
sends, and the parsing of a real `BetaMessageTokensCount` response back into a
report. What is unverified is only what Anthropic's servers do with it.

## The policy, and what the API actually accepts

```python
from policy import ClearToolUsesPolicy

ClearToolUsesPolicy(keep=3, trigger_kind="tool_uses", trigger_value=5).to_config()
# {"edits": [{"type": "clear_tool_uses_20250919",
#             "trigger": {"type": "tool_uses", "value": 5},
#             "keep": {"type": "tool_uses", "value": 3}}]}
```

Two units that are not interchangeable, and are easy to get backwards: `keep` is
counted in **tool uses** only, `clear_at_least` in **input tokens** only. Neither
is a caller choice, so neither is a constructor parameter — they are constants in
`policy.py`.

The demo uses the `tool_uses` trigger because it fires deterministically on a
small transcript. **Production loops usually want the `input_tokens` form**,
which is the API's default at 100k:

```python
ClearToolUsesPolicy(keep=3, trigger_kind="input_tokens", trigger_value=100_000)
```

## Caveats that decide whether this is worth turning on

- **`clear_at_least` is all-or-nothing.** If the API cannot clear at least that
  many tokens, it applies *no edit*, not a partial one. An over-ambitious floor
  silently yields zero saving — which is exactly why the report distinguishes
  `applied: False` from "saved 0 tokens" and explains both possible causes.
- **Clearing invalidates the cached prompt prefix.** That is the whole reason
  `clear_at_least` exists: you want to clear enough to be worth paying the cache
  write again. This preview measures the *saving*, not the cache cost, so a
  positive number here is a necessary but not sufficient reason to enable it.
- **Pairing is preserved by default.** Only `tool_result` bodies are cleared; the
  preceding `tool_use` block stays, so the model keeps the record that it made
  the call and with what input. `clear_tool_inputs: true` drops the inputs too —
  not exposed by this example's policy type.
- **Your client keeps the full history.** The edit is per-request and
  server-side; you never sync your local messages list to it.
- **`count_tokens` does not use prompt caching at all**, so the preview measures
  raw prefix size and cannot mislead you with a cache hit.
- **Token counts are tokenizer-specific.** Claude 4.7+ models use a newer
  tokenizer that reports roughly 30% more tokens for the same text, so `MODEL` in
  `main.py` changes every number printed. It is one constant for that reason.

## The asymmetry that will bite you

The `count_tokens` response's `context_management` carries **only**
`original_input_tokens`:

```json
{ "input_tokens": 25000, "context_management": { "original_input_tokens": 70000 } }
```

It has no `applied_edits`. That is a *different model* from the generation
response, whose `BetaContextManagementResponse.applied_edits` carries
`cleared_tool_uses` and `cleared_input_tokens` per edit. So on the free preview
path you learn **how much** was cleared, never **how many** tool uses.

`make_counter()` in `main.py` is the one place that knows this. It maps the SDK
response into the core's `TokenCount`, so nothing downstream can reach for a
field that does not exist on this endpoint.

## How this was verified without an API key

1. `python test_preview.py` — 25 assertions, exit 0, SDK never imported.
2. A throwaway `anthropic==0.121.0` install, with `main.make_counter` pointed at
   a **local HTTP stub** standing in for `api.anthropic.com`. This is not
   shipped; it was a one-off check that the adapter survives contact with the
   real SDK. It proved:
   - the request goes to `/v1/messages/count_tokens?beta=true`;
   - the header is `anthropic-beta: context-management-2025-06-27,token-counting-2024-11-01`
     (the SDK adds the second one itself);
   - the serialised body's `context_management` is byte-for-byte the dict
     `to_config()` produced;
   - the plain call omits the `context_management` key entirely;
   - a canned `{"input_tokens": 1300, "context_management": {"original_input_tokens": 7000}}`
     parses into `BetaMessageTokensCount` and through the adapter into a correct
     report.

   **It caught a real bug.** The adapter originally called
   `client.messages.count_tokens(..., betas=[...])`, which raises
   `TypeError: unexpected keyword argument 'betas'` — `betas` exists only on the
   beta namespace, `client.beta.messages`. The fake client in `test_preview.py`
   now deliberately exposes *only* `.beta`, so that bug cannot come back.

## Deviations from the research note's Layer-3 sketch

Three, all toward the repo's correctness posture:

- **`PreviewReport.tokens_saved` and `.percent_saved` are computed properties,
  not stored fields.** The note listed all five as fields. Invariant 4
  ("`tokens_saved == original - edited`, never negative") is then something a
  constructor could violate; as properties it holds by construction and there is
  nothing to keep in sync.
- **`preview()` takes an `EditPolicy` Protocol**, declared in `preview.py`,
  rather than `policy.ClearToolUsesPolicy` concretely. Same call signature the
  note specified, but the core depends on the capability (`to_config()`) instead
  of importing the one strategy module — a future `clear_thinking_20251015`
  policy would drop in unchanged.
- **`preview()` really does make both counts** (the note's Layer 1 says so, and
  its A4/A5 could have been satisfied by one). The second count's
  `original_input_tokens` is absent whenever no edit was applied, so without the
  plain count the "original" in that case would have to be inferred from the
  very number it is compared against. With both, they must agree — and
  `InconsistentCount` is raised if they do not, rather than picking one.

## Open questions the note left, and their status

- **Does `count_tokens` omit `context_management` or send `null` when the trigger
  does not fire?** Still open — needs a live run. It does not matter to this
  code: the SDK models the field as `Optional[...] = None`, so both wire forms
  arrive at the adapter as `None`, and `test_adapter_maps_a_missing_context_management_to_none`
  pins our handling either way.
- **Is `keep=0` legal?** Still open. `policy.py` rejects `keep < 0`, not
  `keep < 1`, because the SDK types it as a bare `int` with no minimum and the
  docs state no floor. If a live run 400s on `keep=0`, tighten it to `< 1` and
  say so here.
- **Is `clear_tool_uses_20250919` supported on `claude-haiku-4-5`?** Still open.
  Docs say all supported models; not independently confirmed for this id. A 400
  naming the model propagates as an exception with a traceback — it is never
  swallowed into a zero-saving report.
- **What is the placeholder text left behind?** Still open and unobservable from
  this endpoint, which returns counts only.

## Explicitly out of scope

Running a real agent loop; the `compact_20260112` strategy and its
`pause_after_compaction` flow (a different beta, `compact-2026-01-12`, and its
own cycle); `clear_thinking_20251015`; `clear_tool_inputs`; client-side pruning
of your own message list; any billed `messages.create` call; and measuring the
cache-write cost that clearing incurs.
