# Context editing against prompt caching: what the clear costs, and when it pays back

`clear_tool_uses_20250919` deletes old `tool_result` bodies from a long tool
loop, and Anthropic's own docs say plainly what it costs you:

> **Tool result clearing:** Invalidates cached prompt prefixes when content is
> cleared. To account for this, clear enough tokens to make the cache
> invalidation worthwhile. Use the `clear_at_least` parameter to ensure a
> minimum number of tokens is cleared each time. **You'll incur cache write
> costs each time content is cleared, but subsequent requests can reuse the
> newly cached prefix.**

"Clear enough tokens to make it worthwhile" is a threshold with no number
attached to it. Neither the docs nor the 2026-03-20 context-engineering cookbook
says how many turns "subsequent requests" has to mean before the re-cached
prefix has paid for the write. **This example measures both halves of that trade
on one transcript and prints the answer as a number of turns.**

From the research note:
[`research/2026-09-07-context-editing-cache-tradeoff.md`](../../research/2026-09-07-context-editing-cache-tradeoff.md).
Background: [`knowledge/context-editing.md`](../../knowledge/context-editing.md),
[`knowledge/prompt-caching.md`](../../knowledge/prompt-caching.md),
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## What's here

| File | What it is |
|------|-----------|
| `compose.py` | The pure core. `clearing_boundary(messages, keep_tool_uses=...) -> int`, `place_breakpoints_for_clearing(messages, budget=..., keep_tool_uses=...) -> Placement`, `clearing_config(...) -> dict`. No `anthropic` import, no I/O, no env, no clock. |
| `payback.py` | The pure trade arithmetic. `summarize(clearing_turn, removed=..., base_usd_per_mtok=...) -> Tradeoff` and `render(tradeoff) -> str`. The price is passed in, never baked in. |
| `transcript.py` | The deterministic synthetic tool loop, lifted from [`context-editing-preview`](../context-editing-preview/). `build_transcript(rounds, result_chars)`. |
| `main.py` | The imperative shell. The only file that imports the SDK (lazily, inside `main()`), reads the key, or prints. Two free counts, two billed generations, three gates. |
| `test_compose.py` | Offline self-test: 28 assertions on the boundary, the placement and the shell's static prefix. |
| `test_payback.py` | Offline self-test: 25 assertions on the arithmetic, both SDK adapters, the whole run against a fake client, and the no-key path. |
| `requirements.txt` | `anthropic==1.4.0` — for the **live run only**. |

## The trade, in one formula

Two numbers, from one clearing turn:

- **`removed`** — tokens the clear deleted from the message list. Free to
  measure: `count_tokens` accepts `context_management`, so
  `original_input_tokens - input_tokens` is the answer at $0. The billed
  response also reports it as `applied_edits[0].cleared_input_tokens`, and this
  example checks the two against each other.
- **`rewritten`** — `cache_creation_input_tokens` on the turn the clear fired.
  This is the cache write the invalidation forced, and it is the number that
  **cannot** be previewed: `count_tokens` runs no caching logic and reports no
  cache fields at all.

Price them with the documented 5-minute multipliers on the base input rate —
write `1.25x`, read `0.10x`:

```
one-time cost of the invalidation  =  rewritten x (1.25 - 0.10) x base
saving on every later turn         =  removed   x  0.10         x base

                                        rewritten x 1.15      rewritten
payback_turns = cost / saving      =  --------------------- = 11.5 x ---------
                                        removed   x 0.10             removed
```

The base rate cancels. **Payback is a pure token ratio** — it is the same at
$1/MTok and $10/MTok, which is why `clear_at_least` (a token knob) is the only
lever on it. Two worked examples, both asserted in `test_payback.py`:

| `removed` | `rewritten` | `payback_turns` | Reading |
|---:|---:|---:|---|
| 160,000 | 20,000 | **1.438** | clear 8x what you re-write and the next two turns have paid for it |
| 12,000 | 10,000 | **9.583** | clear barely more than you re-write and it takes ten turns to break even |

That is the docs' qualitative warning as a number, and it is the whole case for
`clear_at_least`: it is the parameter that keeps `removed >> rewritten`. Set it
too low and you are paying for cache writes that ten turns of loop will not earn
back — set it too high and the edit is skipped entirely, because
`clear_at_least` is all-or-nothing.

**Two assumptions the model makes**, both of which flatter it *less* than
reality:

1. The cleared region was **below a cache breakpoint and being re-read every
   turn** at `0.10x`. True for deep history, which is what this transcript
   clears. If those tokens were above the last breakpoint (the live, uncached
   tail), the per-turn saving is at the full `1.0x` input rate and payback
   arrives about ten times sooner.
2. Future turns are otherwise the same size. A transcript that keeps growing
   would also inflate future *writes*, which this ignores.

## What the clearing turn's counters mean

Prefix reuse is ordered `tools` -> `system` -> `messages`, and a change at one
level invalidates that level and everything after it. A `clear_tool_uses` edit
rewrites `tool_result` bodies, so it changes **only** the `messages` array:

| Counter on the clearing turn | What it should be | Why |
|---|---|---|
| `cache_read_input_tokens` | the `tools` + `system` prefix | a messages-level edit is *below* them in the prefix order, so their breakpoints still hit |
| `cache_creation_input_tokens` | the surviving message prefix | every messages-level entry below the first cleared block is now a different prefix and has to be written again — this is `rewritten` |
| `input_tokens` | a handful | the remainder *after* the last breakpoint, not the whole prompt |
| `context_management.applied_edits[0].cleared_input_tokens` | what the edit deleted | this is `removed`; `cleared_tool_uses` says how many round trips it flattened |

The three `usage` counters partition the prompt:
`total = input_tokens + cache_creation_input_tokens + cache_read_input_tokens`.

**Cold-start caveat, stated up front.** Turn A of a first run is the first
request with this prefix, so nothing is cached yet: expect
`cache_read_input_tokens = 0` and a `cache_creation_input_tokens` that covers
`tools` + `system` **as well as** the message prefix. On this transcript the
static half is roughly 1,600 tokens of an estimated ~3,900-token write, so
`rewritten` — and therefore the printed payback — is an **over**-estimate by
that much on a cold run. Isolating the messages-only figure needs a run where
the static prefix is already warm and the message prefix is not; that is a third
billed call, and it is out of scope here. See "What a live run must confirm".

## Clearing-aware placement: the anchor moves

[`prompt-caching-tool-loop/placement.py`](../prompt-caching-tool-loop/placement.py)
places two `messages` breakpoints: a **rolling** marker on the last block of
`messages[-1]`, and — once the history outgrows the 20-block lookback window —
an **anchor** on `messages[0]`, so the head stays cached when the tail marker
can no longer see that far back.

Turn clearing on and the head anchor becomes the wrong place. `messages[0]`
survives the clear, but **everything between it and the `keep` boundary is
rewritten to placeholder text on every clearing turn**. An anchor at the head
therefore spans a region that keeps changing: a guaranteed miss, and a wasted
slot out of four.

`compose.clearing_boundary` is the fix, and it is a pure, deterministic rule a
client can compute from its own message list:

> The anchor goes on the last block of the first message that **survives**
> clearing — the message holding the `keep`-th-from-last `tool_use` block.

On this example's transcript (12 round trips, `keep=3`, so `messages` is 25
long) that is `messages[19]`, round 9's assistant turn:

| Turn | Message list | Boundary | Anchor | Rolling |
|---|---|---|---|---|
| A | 25 messages, 12 tool uses | `messages[19]` | last block of `messages[19]` | last block of `messages[24]` |
| B | 27 messages, 13 tool uses | `messages[21]` | last block of `messages[21]` | last block of `messages[26]` |

Turn B's prompt is byte-identical to turn A's through `messages[19]` — and
everything the anchor covers is at or above turn B's own boundary, so it has
already been flattened to the same placeholder text on both turns. That is what
makes the region turn A wrote re-readable on turn B, and both facts are asserted
offline in `test_compose.py`
(`test_the_anchored_region_survives_the_next_round`). A head anchor would
instead have had to bridge the nine tool results sitting between `messages[0]`
and the boundary — exactly the span the clear rewrites every time.

Two degrade cases, because a rule that only works in the common case is a bug
waiting for a Tuesday:

| `clearing_boundary` returns | What placement does |
|---|---|
| `0` — no more tool uses than `keep`, so nothing clears | anchors on `messages[0]`, i.e. the parent's behaviour, unchanged |
| `len(messages)` — `keep=0`, so nothing survives intact | skips the anchor; there is no stable region to hold one |
| the rolling marker's own message | skips the anchor; one block marked twice is one breakpoint, and `marker_count` must not claim two |

## Run the self-tests (no API key, no network, no dependencies)

```bash
cd examples/context-editing-cache-tradeoff
python3 test_compose.py
```

Expected output:

```
ok  10 pairs, keep=3 -> the survivor boundary is messages[15]
ok  keep >= the tool-use count leaves the boundary at 0
ok  keep=0 puts the boundary at len(messages), the no-survivor signal
ok  a transcript with no tool use has its boundary at 0 for any keep
ok  each extra kept tool use moves the boundary back one round trip
ok  two tool uses in one message consume two of the keep budget
ok  budget=2, keep=3 -> rolling tail plus an anchor on messages[15]
ok  a boundary of 0 falls back to the head anchor, as the parent does
ok  a boundary past the end places the rolling marker and no anchor
ok  a boundary colliding with the rolling marker is not counted twice
ok  below the 20-block window only the rolling marker is placed
ok  budget=1 marks the rolling tail and skips the anchor
ok  budget=0 places nothing and an oversized budget is clamped
ok  an empty message list places nothing
ok  the input list is deep-copied, not aliased or mutated
ok  placing over an already-placed list yields the same two markers
ok  a growing loop keeps exactly two markers, never accumulating
ok  every marker is a fresh dict, never the module constant
ok  a negative or non-int keep or budget raises at the boundary
ok  a malformed message raises from either entry point
ok  one bad message at the end rejects the whole list
ok  clearing_config is dict-equal to the documented edit shape
ok  clearing_config rejects keep=0, trigger=0 and non-int values
ok  the anchored prefix is byte-identical one round later
ok  9 tool results sit between the head and the anchor, all rewritten on every clear
ok  system and tools each carry exactly one byte-stable breakpoint
ok  2 static + 2 message breakpoints is exactly the documented cap
ok  the turn-B transcript extends the turn-A one byte for byte

All 28 self-tests passed with no key and no network.
```

Verifiable, not hand-copied: from
[`examples/readme-transcript-check`](../readme-transcript-check/), run
`python3 check_transcript.py ../context-editing-cache-tradeoff -- python3 test_compose.py`
to compare that block against the real thing. (The checker takes one marked
block per README, so only this one carries the marker; the second suite's output
below is shown for reading.)

`python3 test_payback.py` then prints:

```
ok  160,000 removed against 20,000 rewritten pays back in 1.438 turns
ok  12,000 removed against 10,000 rewritten takes 9.583 turns
ok  payback is identical at $1, $2, $5 and $10 per MTok
ok  summarize takes rewritten from cache_creation_input_tokens
ok  removed=0 reports an infinite payback, not a ZeroDivisionError
ok  total prompt = input + cache_creation + cache_read
ok  a negative or non-int token count raises at construction
ok  a negative base rate raises instead of inverting the report
ok  render prints both counts, both dollar figures and the payback
ok  an infinite payback renders as 'never', not 'inf turns'
ok  the usage adapter reads the three anthropic 1.x counters
ok  a null counter is 0 and a renamed one raises AttributeError
ok  the applied_edits adapter reads cleared tokens and cleared uses
ok  a response without applied_edits raises NoInvalidation
ok  the run makes two free counts and exactly two billed generations
ok  both turns send the same tools, system, edit and beta
ok  every billed request carries exactly the four allowed breakpoints
ok  the report prints the payback this run's own counters imply
ok  a clearing turn that wrote nothing raises before turn B is billed
ok  a preview disagreeing with the bill raises before turn B is billed
ok  a 1% gap between the free and billed figures is tolerated
ok  a re-cached prefix nobody read back raises NoRecache
ok  a preview reporting no edit stops the run before anything is billed
ok  the plain count and the reported original must agree
ok  no key prints one line, exits 0, and never imports the SDK

All 25 self-tests passed with no key and no network.
```

Both suites end by asserting `"anthropic" not in sys.modules` and a sub-second
wall clock — that is what makes "no network" a fact rather than a claim.

## Run it live (needs a key, and this one **costs money**)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 main.py
```

**This costs one real generation pair — an estimated two to three cents on
`claude-sonnet-5`. The tokens the clear removes preview for $0; the cache-write
cost the clear forces does not.** That asymmetry is the reason the example
spends anything at all: `count_tokens` accepts `context_management` and will
tell you `removed` for free, but it deliberately runs no caching logic, so
`rewritten` exists only on a billed response.

Where the estimate comes from (character counts divided by four, not a
tokenizer — the exact figures land in the transcript below when a run is
captured): turn A sends roughly 3,900 tokens post-edit, essentially all of it a
cold cache write at `1.25x` on a $2/MTok model, about $0.010; turn B re-reads
most of that at `0.10x` and writes its delta, about $0.004; and up to
`MAX_TOKENS` (512) of output per turn at $10/MTok is up to another $0.010. The
two `count_tokens` calls are free. The program prints what the *clearing* part
of that was worth, not the whole bill.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | the run worked — or `ANTHROPIC_API_KEY` was unset, in which case it prints one line to stderr and makes **no** network call |
| 2 | `EXIT_NO_INVALIDATION`: the clear did not fire, or fired and forced no cache write |
| 3 | `EXIT_NO_RECACHE`: turn B read back less than `MIN_READ_FRACTION` (50%) of what turn A wrote |
| 4 | `EXIT_PREVIEW_MISMATCH`: the free and billed measurements of the same edit differ by more than `TOLERANCE` (2%) |

A missing key exits **0** here, like [`prompt-caching-tool-loop`](../prompt-caching-tool-loop/)
and unlike [`context-editing-preview`](../context-editing-preview/), which exits
1. That one's live path is free, so declining to run it is worth flagging; this
one spends real money, so "no key, nothing spent" is the expected outcome on a
machine without credentials, not a failure.

### Live transcript: NOT YET CAPTURED

**No `ANTHROPIC_API_KEY` was available in the environment this example was built
in, so the live run has not been made and there is no transcript to paste. No
numbers below are measured, and none have been invented to fill the gap.**

Everything that could be verified without a key was: both self-test suites, and
the whole shell end to end against a fake client — two free counts, exactly two
billed generations, the same `tools`/`system`/`context_management`/`betas` on
both, four breakpoints and never a fifth, both SDK response adapters, and all
three gates. What a live run adds is the one thing none of that can cover: what
Anthropic's servers actually do with the request.

To capture it, run the documented command and paste real stdout in place of this
block. The labels, the order and the multipliers are fixed by `main.render()`
and `payback.render()` and are asserted in the self-tests; only the counts are
run-dependent. The shape is:

```
Context editing against prompt caching: clear_tool_uses_20250919
  model         claude-sonnet-5
  transcript    12 tool-use rounds, 1600-char results (25 messages)
  policy        keep=3 tool uses, trigger=5 tool uses
  breakpoints   2 static (tools, system) + 2 in messages; anchor on messages[19], the first message that survives the clear

Free preview - count_tokens, $0:
  original      <run-dependent> input tokens
  edited        <run-dependent> input tokens
  removed       <run-dependent> tokens (<run-dependent>%)

Turn A - the clearing turn (billed):
  cache_read_input_tokens      <run-dependent>
  cache_creation_input_tokens  <run-dependent>   <- the prefix the clear forced back to a cold write
  input_tokens                 <run-dependent>
  applied_edits[0]             cleared <run-dependent> tool uses, <run-dependent> input tokens

Turn B - one round later, same policy (billed):
  cache_read_input_tokens      <run-dependent>   <- <run-dependent>% of turn A's write, back at the 0.1x read rate
  cache_creation_input_tokens  <run-dependent>
  input_tokens                 <run-dependent>

The clearing / caching trade
  ... (payback.render: removed, rewritten, both dollar figures, payback turns)
```

Once captured, that block is a dated record of one run, not a reproducible
fixture: re-running it costs money and returns different counts every time, so
unlike the self-test transcript above it is **not** machine-checked by
[`readme-transcript-check`](../readme-transcript-check/). See
[`knowledge/doc-transcript-drift.md`](../../knowledge/doc-transcript-drift.md).

## What a live run must confirm

Five open questions this example is built to answer, and what the answer looks
like. None of them can be settled offline; none of them are guessed at here.

1. **Does the clearing turn keep the `tools`/`system` cache while losing only
   `messages`?** Every Anthropic source says yes. A *cold* first run cannot show
   it — turn A has nothing cached to read — so the discriminating run is one
   where the static prefix is already warm and the message prefix is not (change
   `RESULT_CHARS` between two runs and compare turn A's
   `cache_read_input_tokens` against the static prefix size). If that read
   collapses to zero too, `rewritten` is not attributable to the messages level
   and the report needs splitting.
2. **The third-party claim that clearing runs "after prompt cache lookup ...
   without destroying prompt cache prefixes"** (Agno's docs and at least one SEO
   blog) contradicts Anthropic's docs, the cookbook, and
   [`knowledge/context-editing.md`](../../knowledge/context-editing.md). A cold
   pair still discriminates: turn B's raw message list *extends* turn A's, so if
   clearing did not touch the cached prefix, turn B would read back essentially
   **all** of turn A's write (ratio ~1.0). If clearing does invalidate, turn B's
   read stops at the anchor — an estimated ~0.6 of the write on this transcript
   — because the clear rewrote round 9's result *below* the rolling marker.
   Record whichever it is in `knowledge/context-editing.md`.
3. **How much of `rewritten` is the static prefix** on a cold turn A (see the
   cold-start caveat above). The cheapest fix is a third free `count_tokens`
   call over `tools` + `system` alone, which would let the report subtract it;
   deliberately not added here, because the note scoped the preview at two
   counts.
4. **Is `applied_edits` present on the billed response and absent on
   `count_tokens`?** The shipped [`context-editing-preview`](../context-editing-preview/)
   depends on the second half; this example depends on the first. Both adapters
   raise rather than defaulting, so a wrong answer is loud.
5. **Does `anthropic==1.4.0` still report the five fields this reads?**
   `usage.cache_creation_input_tokens`, `usage.cache_read_input_tokens`,
   `usage.input_tokens`, `applied_edits[*].cleared_input_tokens` and
   `.cleared_tool_uses`. A rename raises `AttributeError` with a traceback
   rather than reporting a free clear.

## When this run fails

Exit 2 (`EXIT_NO_INVALIDATION`) means the clearing turn wrote nothing to the
cache. In order of how often they bite:

- **an identical request ran less than five minutes ago**, so the edited prefix
  was already cached and this turn read it instead of writing it. This is the
  one that bites during development: wait out the 5-minute TTL and re-run.
- **the trigger did not fire** — fewer tool uses than `TRIGGER` (5).
- **the cached prefix was under the model's minimum** (1,024 tokens on
  `claude-sonnet-5`). Short prefixes are silently not cached and no error is
  reported anywhere, which is why `build_system()` refuses to build a system
  block below `MIN_SYSTEM_CHARS`.
- if none of those apply, the run has corroborated the third-party "no
  invalidation" claim in question 2 above, and that is a finding worth writing
  down.

Exit 3 (`EXIT_NO_RECACHE`) means turn A's forced write was never read back, so
there is nothing to pay it back:

- the anchor is in the wrong place, so the cached region spans content the next
  clear rewrites — precisely what `compose.clearing_boundary` exists to prevent;
- more than five minutes passed between the two turns;
- the model id, the `tools` array or the `system` block changed between them —
  the prefix is hashed byte-wise, so a re-sorted tool list is a different prefix.

Exit 4 (`EXIT_PREVIEW_MISMATCH`) means the free `count_tokens` subtraction and
the billed `cleared_input_tokens` disagree by more than 2%, or the two free
counts disagree about the original size. Every figure in the report is derived
from those, so the run reports the disagreement instead of picking one.

## Deviations from the research note's Layer-3 sketch

1. **The `system` block is ~1,460 tokens, not "a few hundred".** The note's own
   acceptance criteria require the `tools` + `system` prefix to be cacheable
   (criterion 3 expects the clearing turn to *read* it back) and require turn B
   to read back at least 50% of turn A's write. Both need prefixes above
   `claude-sonnet-5`'s 1,024-token minimum: after the clear, the message prefix
   up to the anchor is nine placeholder round trips, an estimated ~900 tokens —
   below the floor on its own, comfortably above it with a ~1,600-token static
   prefix in front. A "few hundred token" system would most likely have shipped
   an example whose live path exits 3. The cost of the extra ~1,300 tokens is
   about half a cent per run.
2. **`client.beta.messages.create`, not `client.messages.create`.** The
   non-beta namespace has no `betas` parameter and raises `TypeError` rather
   than sending the header — the same constraint `context-editing-preview` hit
   on `count_tokens`.
3. **`_first_applied_edit` sits behind `_cleared_input_tokens`**, because the
   report also prints `cleared_tool_uses` and both must come off one validated
   edit object rather than two independent reaches into the response.
4. **The anchor is skipped when it collides with the rolling marker.** The
   parent got this for free from its `len(messages) >= 2` guard (index 0 is
   never index -1); a boundary anchor can land anywhere, so the rule is now
   explicit. Without it `marker_count` would report two markers on one block.
5. **Turn B's extra round is rebuilt with `build_transcript(ROUNDS + 1)`**
   rather than hand-appended, and `run()` refuses to continue unless the longer
   transcript extends the shorter one byte for byte. That invariant is what
   makes turn B a re-read of turn A's prefix rather than a fresh request.

## Explicitly out of scope

Per the research note: running a real agent (the model never executes a tool —
the `tool_result`s are canned); `clear_thinking_20251015` and `compact_20260112`;
the 1-hour TTL and its `2x` write multiplier; streaming; more than two
`messages`-level breakpoints; client-side pruning; `clear_at_least` and
`exclude_tools` as *sent* parameters (the README argues for the first, the demo
does not send it); and any attempt to preview the cache-write cost for $0, which
is impossible by construction.

## Related

- [`examples/context-editing-preview/`](../context-editing-preview/) — the free
  half on its own: what a clearing policy would remove, for $0, via
  `count_tokens`. This example lifts its `transcript.py` and its
  validate-at-the-boundary posture.
- [`examples/prompt-caching-tool-loop/`](../prompt-caching-tool-loop/) — the
  caching half on its own: the rolling/head breakpoint placement this example's
  `compose.py` extends, and the `TurnUsage` / `render` shape `payback.py`
  follows.
- [`knowledge/context-editing.md`](../../knowledge/context-editing.md) — the
  strategy's fields, the `count_tokens` / `create` response asymmetry, and where
  the payback formula is recorded.
- [`knowledge/prompt-caching.md`](../../knowledge/prompt-caching.md) — the
  multipliers, the 20-block lookback, the four-breakpoint cap, and the cache
  killers.
