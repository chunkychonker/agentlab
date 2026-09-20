# Context editing preview: what would a context edit do to my transcript?

Server-side context editing prunes old material out of a request before the
model sees it — `tool_result` bodies under `clear_tool_uses_20250919`, prior
assistant `thinking` blocks under `clear_thinking_20251015`. The catch is that
**nothing local changes** — your client keeps the full, unmodified history, and
the edit happens per-request on Anthropic's side. So you cannot find out what it
did by printing your own messages list, and the obvious way to find out is to
pay for a generation.

This example finds out for **$0**. It counts the same request twice through the
[token-counting endpoint](https://platform.claude.com/docs/en/build-with-claude/token-counting),
which is free and rate-limited separately from message creation:

| Call | `context_management` | What comes back |
|---|---|---|
| 1 | omitted | `input_tokens` — the request untouched |
| 2 | your policy | `input_tokens` after editing, plus `context_management.original_input_tokens` |

The delta is the saving. No `messages.create`, no tokens generated, no bill.

From the research notes:
[`research/2026-08-11-context-editing-preview.md`](../../research/2026-08-11-context-editing-preview.md)
(`clear_tool_uses_20250919`) and
[`research/2026-09-08-context-editing-clear-thinking-preview.md`](../../research/2026-09-08-context-editing-clear-thinking-preview.md)
(`clear_thinking_20251015`).
Background: [`knowledge/context-editing.md`](../../knowledge/context-editing.md),
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## What's here

| File | What it is |
|------|-----------|
| `policy.py` | Both edits, each well-formed by construction: `ClearToolUsesPolicy` and `ClearThinkingPolicy`. Validate in `__post_init__`, serialise in `to_edit()`. No I/O, no `anthropic` import. |
| `preview.py` | The pure core, shared by both strategies. Declares the `CountTokens` and `EditPolicy` interfaces it needs, turns two `TokenCount`s into one `PreviewReport`. |
| `transcript.py` | Fixture for the tool strategy: `rounds` complete tool-use round trips with fixed-size results. Pure and deterministic. |
| `main.py` | Imperative shell for `clear_tool_uses_20250919`. Reads the env var, imports the SDK (lazily), prints. |
| `test_preview.py` | Offline self-test for it: 25 assertions, no key, no network, no SDK installed. |
| `thinking_transcript.py` | Fixture for the thinking strategy: `turns` assistant turns, each `[thinking, text]`, no tools. Pure and deterministic. |
| `preview_thinking.py` | Imperative shell for `clear_thinking_20251015`. Sibling of `main.py`; different model, different fixture, same core. |
| `test_preview_thinking.py` | Offline self-test for it: 25 assertions, same no-key, no-network, no-SDK bar. |
| `requirements.txt` | `anthropic==0.121.0` — for the **live runs only**. |

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/context-editing-preview
python test_preview.py           # clear_tool_uses_20250919
python test_preview_thinking.py  # clear_thinking_20251015
```

`test_preview.py` — Expected output:

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

The `clear_thinking` suite is documented in
[its own section](#the-second-strategy-clear_thinking_20251015) below.

## Run `clear_tool_uses_20250919` live (needs a key; still costs nothing)

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

## The tool-use policy, and what the API actually accepts

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

## The second strategy: `clear_thinking_20251015`

Same beta, same free double-count, a different thing cleared: prior assistant
**thinking** blocks instead of tool results. Added 2026-09-08 from
[`research/2026-09-08-context-editing-clear-thinking-preview.md`](../../research/2026-09-08-context-editing-clear-thinking-preview.md).

```bash
cd examples/context-editing-preview
python test_preview_thinking.py   # offline: no key, no network, no SDK
python preview_thinking.py        # live: needs a key, still costs nothing
```

### The edit is much smaller than `clear_tool_uses_20250919`

```python
from policy import ClearThinkingPolicy

ClearThinkingPolicy(keep=2).to_config()
# {"edits": [{"type": "clear_thinking_20251015",
#             "keep": {"type": "thinking_turns", "value": 2}}]}
```

Two fields, one of them optional. **There is no `trigger`** — and no
`clear_at_least`, `exclude_tools`, or `clear_tool_inputs` either. The strategy
fires unconditionally on every request, which changes what `applied: False`
means: for `clear_tool_uses` it can mean a threshold went unmet, here it can
only mean there was nothing left to clear.

`keep` has three forms, and one optional field holds all of them:

| Constructed as | Serialises to | Means |
|---|---|---|
| `ClearThinkingPolicy()` | `{"type": "clear_thinking_20251015"}` | omit `keep`; take the model default |
| `ClearThinkingPolicy(keep=2)` | `…, "keep": {"type": "thinking_turns", "value": 2}` | keep the 2 most recent thinking turns |
| `ClearThinkingPolicy(keep="all")` | `…, "keep": "all"` | keep every thinking block |

The default is **model-specific**, which is why omitting `keep` is a real third
choice and not a synonym for either of the others: Opus 4.5+ / Sonnet 4.6+ keep
all prior thinking turns, earlier Opus/Sonnet and every Haiku keep only the last
one.

`keep` is counted in **thinking turns** — not tokens, not tool uses. `keep=0` is
rejected at construction (the docs require `value > 0`), and so is `keep=True`,
which Python would otherwise serialise happily as `"value": true` because `bool`
is an `int` subclass. The SDK also accepts an object form `{"type": "all"}`
alongside the bare string; they mean the same thing, and one spelling for one
meaning is enough here.

### It must run on a model that keeps prior-turn thinking

This is the constraint that decides whether the preview measures anything at
all. Thinking blocks from *previous* assistant turns count toward input tokens
only on models that keep all prior turns. On the rest, **the API strips them
before counting** — so there is nothing left for the edit to clear, and the
number you get back is zero for a reason that has nothing to do with your
policy.

| Model | Prior-turn thinking | What this preview would report |
|---|---|---|
| `claude-sonnet-5` (also Opus 4.5+, Sonnet 4.6+) | kept, and counted | a real saving |
| `claude-haiku-4-5` — the model `main.py` uses | stripped before counting | ~0, misleadingly |

So `preview_thinking.py` pins `MODEL = "claude-sonnet-5"` instead of reusing
`main.py`'s Haiku. Counting is free on every model, so this is a correctness
choice, not a cost one — the same disqualification `compact_20260112` runs into.

Two smaller differences `make_thinking_counter` absorbs: every count carries
`thinking={"type": "adaptive"}` (5-series models reject a manual
`budget_tokens`), and the fixture is tool-free, so the adapter **omits** `tools`
rather than sending `[]`.

### The thinking blocks in the fixture are synthetic

Real `signature` values come back only from `messages.create`, which this
example never calls — that is the whole point of a $0 preview. So every thinking
block in `thinking_transcript.py` carries a placeholder that says so in its own
text.

The bet is that `count_tokens` checks thinking blocks for *structure*
(`type` / `thinking` / `signature` present) and not for a valid signature. The
evidence: the token-counting docs' own worked example counts a visibly truncated
signature and returns a number, and every documented
`invalid_request_error` about a modified signature traces back to
`messages.create`. That is a best read, **not** a live confirmation.

If the bet is wrong, the live count 400s and the error propagates with a
traceback — it never arrives as a report of zero saving. The offline self-test,
which is the whole acceptance surface, is unaffected either way. Do not send
this fixture to `messages.create`: that endpoint does verify signatures.

### Self-test output

`check_transcript.py` refuses a README with two blocks marked `Expected output`
(guessing which one a caller meant is how a checker starts lying), so this
directory spends its one marker on `test_preview.py` above. This block is
therefore hand-copied and *not* machine-checked — `python
test_preview_thinking.py` is the source of truth:

```
ok  an omitted keep leaves the edit as type alone, not keep: null
ok  a turn count serialises to {type: thinking_turns, value: N}
ok  keep='all' serialises to the bare string the docs use
ok  to_config wraps the edit in the API's edits list
ok  the thinking strategy ships behind the one existing beta
ok  keep=0 is rejected at construction
ok  keep=-1 is rejected at construction
ok  a keep string other than 'all' is rejected at construction
ok  a fractional keep is rejected at construction
ok  keep=True is rejected rather than counted as one turn
ok  every assistant turn is one thinking block then one text block
ok  the same arguments always build the same thinking transcript
ok  a transcript with no turns or empty thinking is rejected
ok  the fixture holds no tool_use or tool_result blocks to clear
ok  an applied edit reports 22000 tokens saved and 36.7%
ok  an unapplied edit reports 0 saved rather than raising or guessing
ok  the same core counts the thinking fixture plain, then edited
ok  the adapter sends the beta, adaptive thinking, and the edit dict
ok  an empty tool list is omitted from the request, not sent as []
ok  a non-empty tool list is still sent
ok  an absent context_management object becomes original=None
ok  an API error propagates instead of becoming a zero-saving report
ok  the demo model is one that keeps prior-turn thinking
ok  the report prints the pasteable config and the delta
ok  an unapplied edit is explained as an empty clear, not a trigger

All 25 self-tests passed with no key and no network.
```

### Live thinking transcript: NOT YET CAPTURED

No `ANTHROPIC_API_KEY` was available in the environment where this was built, so
the live numbers are **not** reproduced here and nothing has been invented to
fill the gap. There is no fabricated token count anywhere in this directory.
Running `python preview_thinking.py` with a key on `claude-sonnet-5` is what
settles both open questions at once, for $0: whether `count_tokens` accepts the
synthetic signatures, and how much `keep=2` actually saves on eight thinking
turns.

What *was* verified offline is the whole path up to the wire, plus the wire
shapes themselves against the pinned SDK — see "How this was verified" below.

## Caveats that decide whether this is worth turning on

- **`clear_at_least` is all-or-nothing.** If the API cannot clear at least that
  many tokens, it applies *no edit*, not a partial one. An over-ambitious floor
  silently yields zero saving — which is exactly why the report distinguishes
  `applied: False` from "saved 0 tokens" and explains both possible causes.
- **Clearing invalidates the cached prompt prefix.** That is the whole reason
  `clear_at_least` exists: you want to clear enough to be worth paying the cache
  write again. This preview measures the *saving*, not the cache cost, so a
  positive number here is a necessary but not sufficient reason to enable it.
  `clear_thinking_20251015` has no `clear_at_least`, so that trade is entirely
  yours to manage: keeping thinking blocks preserves the cache, clearing them
  invalidates it from the point where the clearing occurs.
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
3. `python test_preview_thinking.py` — 25 more assertions, exit 0, SDK never
   imported. Its `FakeClient` exposes only `.beta` for the same reason.
4. For `clear_thinking_20251015`, one further offline check: the pinned
   `anthropic==0.121.0` wheel was downloaded and unpacked (no install, no
   network call to Anthropic) and its generated types read directly. That
   settled three build-time questions without a request:
   - `types/beta/beta_clear_thinking_20251015_edit_param.py` already exists in
     `0.121.0` and is exactly `{type: Required, keep?}`, with
     `Keep = BetaThinkingTurnsParam | BetaAllThinkingTurnsParam | Literal["all"]`
     and both of `BetaThinkingTurnsParam`'s fields `Required`. `claude-sonnet-5`
     is in `types/model.py` and `context-management-2025-06-27` is in
     `types/anthropic_beta_param.py`. **So no SDK bump is bundled with this
     feature** — the pin stays where `clear_tool_uses` left it.
   - `beta.messages.count_tokens` takes `thinking: BetaThinkingConfigParam`
     directly (`resources/beta/messages/messages.py:1846`), so the adapter
     passes it as a keyword argument rather than smuggling it through
     `extra_body`.
   - `BetaThinkingConfigAdaptiveParam` is `{"type": "adaptive", display?}`, so
     the adaptive form the 5-series requires is expressible in this pin.

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
  policy would drop in unchanged. It since did: `ClearThinkingPolicy` reuses
  `preview()`, `PreviewReport`, `TokenCount`, `EditPolicy` and `CountTokens`
  with no edit to `preview.py` at all.
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
  this endpoint, which returns counts only. The docs describe one for cleared
  tool results and say nothing about cleared thinking.
- **Does `count_tokens` accept a synthetic thinking-block signature?** Still
  open; it needs one $0 live call. See "The thinking blocks in the fixture are
  synthetic" above for the evidence either way and for why it does not gate
  anything: a rejection surfaces as a traceback, and the offline suite is
  unaffected.
- **Does `count_tokens` accept `"tools": []`?** Still open, and deliberately not
  found out: `make_thinking_counter` omits the key when the list is empty, the
  same rule `to_edit()` follows for an unset `keep`.

## Explicitly out of scope

Running a real agent or thinking loop; the `compact_20260112` strategy and its
`pause_after_compaction` flow (a different beta, `compact-2026-01-12`, and its
own cycle); `clear_tool_inputs`; combining `clear_thinking_20251015` and
`clear_tool_uses_20250919` in one request (the docs require the thinking edit to
be listed first in `edits` when you do); obtaining real signed thinking blocks;
client-side pruning of your own message list; any billed `messages.create` call;
and measuring the cache-write cost that clearing incurs.
