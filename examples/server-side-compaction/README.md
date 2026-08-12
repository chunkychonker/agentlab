# Server-side compaction: one paused round, and the bill `usage` hides

`compact_20260112` is context editing's summarize-don't-prune sibling. Same
`context_management` parameter, different beta (`compact-2026-01-12`), and a
different deal: instead of dropping old `tool_result` bodies for free, the server
spends **a whole extra sampling iteration** writing a summary of your
conversation, then continues from it.

Two consequences that the docs state but their example code does not survive:

- **`usage.input_tokens` and `usage.output_tokens` no longer describe your bill.**
  They exclude the compaction iteration entirely. In the docs' own worked example
  the top-level numbers say 46,234 tokens while the real total is 207,500 — the
  compaction pass alone read 180,000 tokens. Any cost tracker written before
  compaction under-reports by that whole pass.
- **A compaction block can come back with `content: null`**, meaning the model
  failed to produce a valid summary. Both Anthropic's and AWS's example snippets
  do `block["content"][:200]` and raise `TypeError` on it.

This example makes **one** paused compaction call and turns the response into a
report that shows the summary, the true per-iteration bill, and the continuation
message list that carries the conversation across the boundary. Before it spends
anything it counts the transcript for free and refuses the call if the trigger
cannot fire.

From the research note:
[`research/2026-08-12-server-side-compaction.md`](../../research/2026-08-12-server-side-compaction.md).
Background: [`knowledge/compaction.md`](../../knowledge/compaction.md),
[`knowledge/context-editing.md`](../../knowledge/context-editing.md),
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).
The pruning sibling is [`examples/context-editing-preview/`](../context-editing-preview/).

## What's here

| File | What it is |
|------|-----------|
| `policy.py` | A `compact_20260112` edit that is well-formed by construction: the 50,000-token floor and the blank-`instructions` trap are rejected in `__post_init__`. No I/O, no `anthropic` import. |
| `preflight.py` | Two numbers and a go/no-go. Counted tokens vs. the trigger, plus the shortfall when it will not fire. |
| `report.py` | The pure core. Reads a wire-shaped response dict into a `CompactionReport` (including the null-summary case) and builds the continuation message list. |
| `cost.py` | Tokens to dollars at a per-MTok rate passed in. No pricing table baked into a library. |
| `transcript.py` | The synthetic fixture: `turns` alternating exchanges of fixed-size prose, ending on a user turn. Pure and deterministic. |
| `main.py` | The imperative shell. The only file that reads the env var, imports the SDK (lazily), or prints. |
| `test_compaction.py` | Offline self-test: 32 assertions, no key, no network, no SDK installed. Also holds the response fixtures. |
| `verify_fixture_schema.py` | Validates those fixtures against the real SDK's `BetaMessage`. Needs the SDK, still no key and no network. |
| `requirements.txt` | `anthropic==0.121.0` — for the live run and the schema check only. |

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/server-side-compaction
python3 test_compaction.py
```

Expected output:

```
ok  the edit is exactly type, trigger and pause_after_compaction
ok  instructions appear only when set, never as null
ok  to_config wraps the edit in the API's edits list
ok  a trigger below the 50,000-token floor is rejected at construction
ok  the floor value itself constructs
ok  empty and whitespace-only instructions are both rejected
ok  instructions=None constructs and sends nothing
ok  a transcript above the trigger is a go, with no shortfall
ok  a transcript below the trigger is a no-go, with the shortfall
ok  exactly at the trigger is one token short, not ready
ok  the bill sums usage.iterations (207500), not the top level (46234)
ok  a compaction block with null content is reported, not sliced
ok  with no compaction the bill comes from the top-level usage
ok  a re-applied compaction block bills nothing extra
ok  the last compaction block is the one reported
ok  an empty iterations array raises instead of billing zero
ok  a malformed response raises instead of reporting an empty result
ok  a blank summary is unconstructible, so '' cannot pass for failure
ok  paused without triggered is not a representable report
ok  the continuation appends one assistant message, verbatim
ok  an assistant message with no content is refused
ok  cost is input and output priced separately, per million tokens
ok  a negative count or price is rejected rather than priced
ok  the transcript alternates roles and ends on a user turn
ok  the same arguments always build the same transcript
ok  a transcript with no turns or unlabellable turns is rejected
ok  the shell sends the beta and the policy's exact edit dict
ok  the free preflight count sends no beta and no edits
ok  an API error propagates instead of becoming an empty report
ok  the render prints the pasteable config, the true bill and the summary
ok  a failed compaction is explained, not printed as an empty summary
ok  a no-go preflight prints the shortfall and refuses to spend

All 32 self-tests passed with no key and no network.
```

Verifiable, not hand-copied: from
[`examples/readme-transcript-check`](../readme-transcript-check/), run
`python3 check_transcript.py ../server-side-compaction -- python3 test_compaction.py`
to compare this block against the real thing.

The last two assertions in the suite make "no network" a fact rather than a
claim: `"anthropic" not in sys.modules` after every test has run (core, both of
the shell's adapters, and the renderer were all exercised without the SDK ever
being imported), and a sub-second wall-clock bound.

## Check the fixtures against Anthropic's schema (needs the SDK, still no key)

Offline tests are only as good as their fixtures, and a fixture we invented that
our own parser reads correctly proves nothing. So every fixture the self-test
uses is also parsed through the real `anthropic.types.beta.BetaMessage`, dumped
back out by the SDK, and re-read — the report must come out identical:

```bash
pip install -r requirements.txt
python3 verify_fixture_schema.py
```

which prints:

```
anthropic 0.121.0
ok  'compaction' is a real BetaStopReason (8 in the union)
ok  'compact-2026-01-12' is absent from AnthropicBetaParam's 33 literals, so a typo is not a type error
ok  DOCS_USAGE_RESPONSE parses as BetaMessage and reads back identically (207500 tokens billed)
ok  FAILED_COMPACTION_RESPONSE parses as BetaMessage and reads back identically (56040 tokens billed)
ok  NO_COMPACTION_RESPONSE parses as BetaMessage and reads back identically (8250 tokens billed)
ok  REAPPLIED_COMPACTION_RESPONSE parses as BetaMessage and reads back identically (12300 tokens billed)

All 4 fixtures validated against the SDK, with no key and no network.
```

(That block is not marked as expected output because it names a version and a
literal count, both of which are properties of the installed SDK rather than of
this example. Run it and read it; it is not a drift-checked transcript.)

## Run it live (needs a key, and this one **costs money**)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 main.py
```

It builds a 121-message synthetic transcript (60 exchanges of 2,000 characters,
~240,000 characters in all), counts it for free, and — only if that count exceeds
the trigger — makes one `messages.create` with `pause_after_compaction: true`.

**Estimated cost: ~$0.15 per run.** One compaction iteration over a ~55–60k-token
prompt at Sonnet 5's $2/MTok input is about $0.11–0.12, plus a small summary at
$10/MTok output. `pause_after_compaction` is what keeps it to one iteration: the
turn stops as soon as the summary exists, so you never pay for the answer you
were not going to read. The program prints the number it actually cost.

Without `ANTHROPIC_API_KEY` it prints one line to stderr and **exits 1**. If the
preflight count comes in at or below the trigger it prints the shortfall and
**exits 2**, before any billed call — a call that cannot fire is not a cheap
experiment, it is a full-price generation with no compaction in it.

### Live transcript: NOT YET CAPTURED

No `ANTHROPIC_API_KEY` was available in the environment where this example was
built, so the live numbers are **not** reproduced here. Nothing has been invented
to fill the gap: there is no fabricated summary text and no fabricated token count
anywhere in this directory. The numbers that do appear in the fixtures
(180000/3500, 45000/1234) are the ones the Anthropic and Bedrock docs publish in
their own worked example, and they are labelled as fixtures; the summary text in
those fixtures literally begins `<summary>SYNTHETIC FIXTURE, not model output:`.

When someone runs it with a key, paste the real stdout under an "Expected output"
heading here and add the `check_transcript.py` invocation for it, exactly as the
self-test block above has. The three open questions below are what that run
answers.

What *was* verified without a key: every offline assertion above, the fixtures
against the SDK's own schema, and the whole shell composed end to end against a
stubbed SDK module (preflight → policy → create → `model_dump` → report → render,
in all three paths: compaction, failed compaction, and below-trigger). What is
unverified is only what Anthropic's servers do with the request.

## The policy, and what the API actually accepts

```python
from policy import CompactionPolicy

CompactionPolicy(trigger_tokens=50_000, pause_after_compaction=True).to_config()
# {"edits": [{"type": "compact_20260112",
#             "trigger": {"type": "input_tokens", "value": 50000},
#             "pause_after_compaction": True}]}
```

Four fields in `BetaCompact20260112EditParam`, and three traps in them:

- **`trigger` has exactly one shape.** `{"type": "input_tokens", "value": int}`.
  There is no `tool_uses` trigger for compaction — the escape hatch that let the
  context-editing example fire deterministically on a tiny transcript does not
  exist here, which is why this demo has to build a genuinely large one.
- **The value must be at least 50,000.** Both docs pages say so; the SDK types it
  as a bare `int`, so the floor is server-enforced only. `CompactionPolicy`
  rejects 49,999 at construction, so this example never finds out whether the
  server 400s or silently clamps.
- **`instructions` replaces the default prompt, it does not append.** The default
  (quoted verbatim only on the Bedrock page) ends *"You must wrap your summary in
  a `<summary></summary>` block"*, so a custom prompt silently drops that
  formatting contract along with the continuity framing. A blank string is
  therefore not "use the default" — it is "summarise with no instructions", and
  `""` and `"   "` are both rejected.

`instructions` is omitted from the request when unset rather than sent as `null`,
because those are different requests and only the second means "use the default".
`pause_after_compaction` is always sent: `False` is a real choice, not an absent
one.

## `claude-haiku-4-5` cannot do this

This repo's standing default for cheap runnable examples is `claude-haiku-4-5`
([`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md)). Haiku is
**absent** from the compaction docs' supported-model list, so `MODEL` here is
`claude-sonnet-5` — the cheapest model that does support it, at $2/$10 per MTok.
That is a deliberate deviation from the repo default and the reason this example
is not free like its sibling.

(The pricing page now says the scheduled 2026-09-01 rise to $3/$15 "will not
occur", so $2/$10 is the standard price rather than an introductory one.)

## Detecting that compaction happened

Not from `response.context_management`. `BetaContextManagementResponse.applied_edits`
unions only the two *clear* edit responses — it never mentions compaction at all.
The three real signals, all of which `read_response` consults:

1. a `{"type": "compaction"}` block in `response.content`;
2. `response.stop_reason == "compaction"` (a real member of `BetaStopReason`,
   though the docstring on `BetaMessage.stop_reason` in 0.121.0 forgets to list
   it);
3. a `usage.iterations` entry with `type: "compaction"`.

When several compaction blocks are present the **last** one is reported: the docs
state that it reflects the final state of the prompt.

## The billing trap

```
usage.input_tokens   45000   ← excludes the compaction iteration
usage.output_tokens   1234   ← excludes the compaction iteration
usage.iterations
  compaction        180000 in / 3500 out
  message            23000 in /  1000 out
                    ───────────────────────
  really billed     207500 tokens, not 46234
```

`CompactionReport.total_billed_tokens` sums `usage.iterations` when that key is
present and the top-level fields when it is absent — never a mix. Both branches
matter: `iterations` is present **only when a new compaction fires**. Re-applying
an existing compaction block on a later turn costs nothing extra and leaves the
top-level numbers accurate, so `iterations is None` is a legal, meaningful state
rather than an error. An `iterations` array that is present but *empty* is
neither, and raises.

Iterations that are neither `compaction` nor `message` (`advisor_message`,
`fallback_message`) are folded into the message totals, so the four counters
always sum to the whole array.

## `content: null` means the compaction failed

From `beta_compaction_block.py`'s own docstring, which neither docs page
mentions:

> When content is None, it indicates the compaction failed to produce a valid
> summary (e.g., malformed output from the model). Clients may round-trip
> compaction blocks with null content; the server treats them as no-ops.

So the report has `summary: str | None`, and `None` is a documented outcome
rather than a missing value. Nothing in `report.py` indexes into it. The opposite
case is also handled: an *empty* summary string is malformed (the param type says
"Empty string content is not allowed"), so a blank summary cannot be constructed
at all and cannot be mistaken for the failure case.

## Continuation: you keep the history, the server drops it

```python
continuation = continuation_messages(messages, response["content"])
# messages + [{"role": "assistant", "content": <the response's blocks, verbatim>}]
```

Append the response and re-request; the API drops every block *before* the
compaction block on its side. Your client never syncs its own list — the same
property as context editing, with the same corollary: you cannot observe the
effect by printing your local messages.

The block must be round-tripped, not rebuilt. It carries
`encrypted_content` — "opaque metadata from prior compaction, to be round-tripped
verbatim" — and a hand-assembled block that drops it is a silent correctness bug.
Both docs pages happen to preserve it by round-tripping the whole `content`
array; this example does the same thing on purpose, and the test asserts it.

## Cost of the technique, not just of the call

The recurring practitioner objection to compaction is not about this API: it is
that summarization is *stop-the-world and lossy*. The agent freezes while a
summary is written, then resumes from a compressed, imperfect version of what it
knew. `pause_after_compaction` does not remove that step — it makes it explicit,
which is arguably the honest framing. What the API does give you is the summary
text and the per-iteration bill, so the trade is measurable instead of guessed at.

## Open questions this build could not settle

- **What does `usage` look like under `pause_after_compaction: true`?** Every
  documented `iterations` example shows a compaction entry followed by a message
  entry. A paused turn has no answer iteration, so a single compaction entry is
  expected — unverified. The report treats a missing message iteration as zeros
  rather than an error, so either answer is handled.
- **Does `count_tokens` on the continuation list actually drop the
  pre-compaction blocks?** The docs say it "applies existing compaction blocks",
  which implies yes, but the only worked example is a non-paused flow. Measuring
  the achieved saving for free after the fact depends on this; nothing in the
  offline criteria does.
- **Is the 50,000-token floor a 400 or a silent clamp?** Rejected at
  construction, so this example never finds out — which is the point.
- **Does `claude-sonnet-5` use the newer 4.7+ tokenizer?** The pricing page names
  neither way for it. It only changes how much synthetic text is needed to clear
  50k tokens, which is exactly what the free preflight measures.

## Explicitly out of scope

The follow-on generation call after the pause (the continuation messages are
built and printed, not sent); multi-turn or repeated compaction; streaming
(`compaction_delta`); custom `instructions` beyond accepting and validating the
field; `clear_tool_uses_20250919` (shipped in
[`context-editing-preview`](../context-editing-preview/)); and prompt-cache
accounting, which compaction certainly disturbs and this example does not
measure.
