# Prompt caching across a long tool loop: where the breakpoints go, and what they saved

A hand-written tool loop sends a message list that **grows every turn** — one
`assistant` message with a `tool_use`, one `user` message with a `tool_result`,
then another `messages.create` over the whole thing. Prompt caching pays for
exactly that shape, but only if the `cache_control` breakpoints sit on blocks
that will not change on the next turn, and only if the prefix in front of them
is byte-identical every time.

This example is two things:

1. `placement.py` — a pure function that inserts the breakpoints into a growing
   message list (a **rolling** marker on the frozen tail, plus a **head anchor**
   once the history outgrows the 20-block lookback window), inside the API's
   documented cap of four.
2. `main.py` + `report.py` — a two-turn runner that **proves** the saving by
   reporting turn 1's `cache_creation_input_tokens` against turn 2's
   `cache_read_input_tokens`, and prices the difference.

From the research note:
[`research/2026-08-29-prompt-caching-tool-loop.md`](../../research/2026-08-29-prompt-caching-tool-loop.md).
Background: [`knowledge/prompt-caching.md`](../../knowledge/prompt-caching.md),
[`knowledge/context-editing.md`](../../knowledge/context-editing.md),
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## What's here

| File | What it is |
|------|-----------|
| `placement.py` | The pure core: `place_breakpoints(messages, budget=...) -> Placement`. Deep-copies, validates at the boundary, inserts at most `budget` markers. No `anthropic` import, no I/O, no env, no clock. |
| `report.py` | The pure saving math: `summarize(turn1, turn2, base_usd_per_mtok=...) -> Saving` and `render(saving) -> str`. The price itself is *not* here — it is passed in. |
| `main.py` | The imperative shell. The only file that imports the SDK (lazily), reads the key, or prints. Builds the byte-stable prefix, makes the two billed calls, asserts the cache hit. |
| `test_placement.py` | Offline self-test: 23 assertions on placement and the two static breakpoints. |
| `test_report.py` | Offline self-test: 17 assertions on the arithmetic, the `usage` adapter, the two-turn run against a fake client, and the no-key path. |
| `requirements.txt` | `anthropic==1.2.0` — for the **live run only**. |

## Where the four breakpoints go

The rule the docs give is one sentence: **put `cache_control` on the last block
whose prefix is identical across requests.** In a tool loop that means four
markers, and the API allows exactly four:

| # | Marker | On what | Set by | Moves? |
|---|--------|---------|--------|--------|
| 1 | tools | the **last** tool in the `tools` array | `main.build_tools()` | never |
| 2 | system | the **last** block of `system` (an array of blocks, not a bare string — a string has nowhere to attach a marker) | `main.build_system()` | never |
| 3 | rolling | the last content block of `messages[-1]` — the `tool_result` that is frozen from the next turn onward | `placement.place_breakpoints` | every turn |
| 4 | anchor | the last content block of `messages[0]` | `placement.place_breakpoints` | never, once placed |

Marker 3 is the one that earns its keep: on the request that sets it, it is a
cache **write** of a small delta; on the next request the same block is
mid-history and unchanged, so it is a cache **read** of everything before it.

Marker 4 exists because of the **20-block lookback window**: from each
breakpoint the API checks at most 20 content-block positions backward for a
usable cache entry, counting the breakpoint itself as the first. Once the
conversation is longer than that, a single tail breakpoint can no longer *see*
the head of the conversation to extend it, and you start paying a fresh write
every turn. So `place_breakpoints` adds the anchor only when the total block
count exceeds `LOOKBACK_BLOCKS` — below that, one rolling marker already chains
turn to turn and a second buys nothing.

`budget` is how many of the four are left for the message array. `main.py`
spends two on the static prefix and passes `MAX_BREAKPOINTS - 2`:

```python
import placement

placed = placement.place_breakpoints(messages, budget=2)
placed.marker_count          # 1 below 20 blocks, 2 above it
client.messages.create(model=..., system=SYSTEM, tools=TOOLS,
                       messages=placed.messages, max_tokens=512)
```

The input list is never mutated (it is deep-copied), and re-placing is a no-op.
Every `cache_control` already on the input is stripped from the copy before
placement — so a loop can call it each turn on a list that *grows between calls*
and still send exactly the markers this policy placed. Without that strip the
moved tail's old marker orphans behind it, and a five-turn loop sends five
breakpoints against a cap of four. The flip side: this module owns
`cache_control` inside `messages`, so a marker you set yourself — including the
out-of-scope `ttl: "1h"` form — does not survive.

## Proving the saving: three counters, one identity

A `messages.create` response reports three input counters, and they **partition**
the prompt. `input_tokens` is not the prompt size; it is only the remainder
after the last breakpoint:

```
total prompt = input_tokens + cache_creation_input_tokens + cache_read_input_tokens
```

So the demo is:

| | `cache_creation_input_tokens` | `cache_read_input_tokens` | `input_tokens` |
|---|---|---|---|
| **turn 1** | the whole tools+system prefix (a **write**) | 0 | the first user message |
| **turn 2** | just the delta this turn added | ≈ turn 1's write (a **read**) | the tail after the last marker |

Priced off the model's base *input* rate:

| What | Multiplier |
|---|---|
| 5-minute cache **write** | 1.25× (a 25% premium, paid once) |
| 1-hour cache write | 2× (out of scope here) |
| cache **read**, either TTL | 0.10× |
| uncached input | 1× |

Break-even is under two reads. `report.py` computes exactly that: what the read
cost at 0.10×, what it would have cost fresh, the difference, the 25% premium on
the write, and the net.

**This cannot be previewed for $0.** `count_tokens` deliberately does not run
caching logic and returns no cache fields at all, so the only way to see a
`cache_read_input_tokens` is to generate. Hence one real, cheap pair.

## Run the self-tests (no API key, no network, no dependencies)

```bash
cd examples/prompt-caching-tool-loop
python3 test_placement.py
```

Expected output:

```
ok  a single message is marked on its last block, and only there
ok  a short loop spends one breakpoint, not two, below the lookback
ok  past 20 blocks the head is anchored as well as the tail
ok  20 blocks is one marker, 21 is two - the window boundary is exact
ok  a budget of one goes to the rolling marker, never the anchor
ok  a budget of zero returns an unmarked copy, not the original list
ok  an empty message list places nothing and does not raise
ok  a budget above the documented cap of 4 is clamped, not obeyed
ok  placing breakpoints leaves the caller's message list untouched
ok  re-placing over an already-marked list is a no-op
ok  a growing loop re-marks the moved tail without accumulating
ok  a marked str becomes a text block; an unmarked one is not rewritten
ok  an unmarked block list is deep-copied, so no later edit reaches back
ok  each marker is a fresh copy of EPHEMERAL, not the constant itself
ok  a negative budget raises ValueError instead of clamping to zero
ok  a float or bool budget raises TypeError
ok  a message that is not {role, str|list} raises TypeError
ok  an untyped, empty or non-mapping content block raises at the boundary
ok  every message is validated, not only the ones about to be marked
ok  the system prefix is one marked block above the minimum prefix size
ok  the tools breakpoint sits on the last tool, and only there
ok  system and tools rebuild byte-for-byte identically, in a fixed order
ok  a full request spends exactly the four breakpoints the API allows

All 23 self-tests passed with no key and no network.
```

Verifiable, not hand-copied: from
[`examples/readme-transcript-check`](../readme-transcript-check/), run
`python3 check_transcript.py ../prompt-caching-tool-loop -- python3 test_placement.py`
to compare that block against the real thing. (The checker takes one marked
block per README, so only this one carries the marker; the second suite's output
below is shown for reading.)

`python3 test_report.py` then prints:

```
ok  a 2000-token hit at $2/MTok saves $0.0036 and cost a $0.0010 premium
ok  net saving is the read saving minus the once-paid write premium
ok  a cache write that is never read reports a loss, not a zero
ok  a run with no cache at all reports 0.0, not a crash
ok  total prompt = input + cache_creation + cache_read
ok  a negative or non-int token counter raises at construction
ok  a negative base rate raises instead of inverting the report
ok  render prints both counts, the rate, and all five dollar figures
ok  the adapter reads the three input counters the 1.x SDK reports
ok  a null counter is 0; a renamed or non-int one fails loudly
ok  two calls, one byte-identical tools+system prefix, a grown message list
ok  the rolling breakpoint lands on turn 2's frozen tail, and only there
ok  every tool_use block is answered, so the second turn is well-formed
ok  a tool-free reply still produces a longer second request
ok  the report pairs turn 1's write with turn 2's read
ok  a run that paid twice and cached nothing raises CacheMiss
ok  no key: one line on stderr, exit 0, no SDK import and no call

All 17 self-tests passed with no key and no network.
```

Both suites end by asserting `"anthropic" not in sys.modules` and a sub-second
wall clock — that is what makes "no network" a fact rather than a claim.

## Run it live (needs a key, and this one **costs money**)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 main.py
```

Two `messages.create` calls on `claude-sonnet-5`, seconds apart, over an
identical ~2,600-token `system` block and two tool definitions.
**Estimated cost: one to two cents.** The measured run below wrote 3,667 tokens
to the cache — more than the `system` block alone, because the cached prefix is
`tools` + `system` — at 1.25×, and read them back at 0.10× on a $2/MTok model:
about $0.0092 written plus $0.0007 read. Output dominates the rest of the bill:
up to `MAX_TOKENS` (512) per turn at $10/MTok is up to another $0.0102. The
program prints what the *caching* part of that was worth, not the whole bill.

Exit codes:

| Code | Meaning |
|---|---|
| 0 | the run worked — or `ANTHROPIC_API_KEY` was unset, in which case it prints one line to stderr and makes **no** network call |
| 2 | `EXIT_NO_CACHE_HIT`: both calls were made and turn 2 did not read back the prefix. Prints the checklist below |

A missing key exits **0** here, unlike [`context-editing-preview`](../context-editing-preview/)
and [`server-side-compaction`](../server-side-compaction/), which exit 1. Their
live path is free or previewable, so declining to run is worth flagging; this one
spends real money, so "no key, nothing spent" is the expected outcome on a
machine without credentials, not a failure.

### Live transcript: captured 2026-08-31

Real stdout from the documented command (`ANTHROPIC_API_KEY=... python3
main.py`) against `claude-sonnet-5` on 2026-08-31. One real two-turn run;
nothing here is invented, rounded, or hand-edited:

```
Prompt caching across a two-turn tool loop
  base input rate      $2.00/MTok

  turn 1 wrote         3667 tokens (cache_creation_input_tokens)
  turn 2 read          3667 tokens (cache_read_input_tokens)
  read / written       1.0

  that read cost       $0.000733 (0.1x base)
  uncached it would    $0.007334 (1.0x base)
  saved on the read    $0.006601
  write premium        $0.001834 (0.25x base, paid once)
  net saving           $0.004767
```

`read / written` is 1.0: every one of the 3,667 tokens turn 1 wrote came back on
turn 2, so the prefix survived intact. A partial hit below `MIN_READ_FRACTION`
would have raised `CacheMiss` and exited 2 instead of printing this, so the
report existing at all is the evidence that the experiment worked. The labels,
the order and the multipliers are fixed by `render()` and are asserted in
`test_report.py`; only the counts are run-dependent.

Unlike the two self-test transcripts above, this block is **not** machine-checked
by [`readme-transcript-check`](../readme-transcript-check/): re-running it costs
money and returns different counts every time. It is a dated record of one run,
not a reproducible fixture.

What was verified without a key: both self-test suites, and the whole shell
end to end against a fake client — two calls, a byte-identical `tools`+`system`
prefix across them, the rolling breakpoint on turn 2's tail and nowhere else,
every `tool_use` answered, the `usage` adapter, and the `CacheMiss` guard. What
the live run above adds is the one thing none of those can cover: what
Anthropic's servers actually do with the request.

## Cache killers

Everything in the prefix is hashed byte-wise, so these all read as "0 tokens
read, full price paid" with **no error anywhere**:

- **A timestamp, "current date & time", or a request id in the system prompt.**
  Every request is then a fresh write. This is the single most common cause;
  one report had 170,000 tokens written and 0 read on every single request.
- **A prefix under the model's minimum.** 1,024 tokens on `claude-sonnet-5` and
  the rest of the Sonnet line, 512 on Opus 5 / Fable 5, 4,096 on Haiku 4.5.
  Shorter prefixes are processed *without* caching and no error is returned.
  The system block here is ~2,600 tokens for that reason, and `build_system()`
  raises rather than shipping a shorter one.
- **Tool reordering.** Tool order is part of the hash. A framework that sorts
  tools alphabetically, or iterates a dict nondeterministically, breaks the
  cache from the first moved tool onward.
- **Model alias drift.** `claude-sonnet-5` and a dated snapshot id are different
  cache namespaces.
- **A pause longer than the TTL.** Five minutes by default, measured from the
  *start* of the request that writes the entry — and generation time counts
  against it, so a long streamed response eats into the window. Any
  human-in-the-loop step longer than that pays a full re-prefill at 1.25×.
- **Parallel identical requests.** A cache entry only becomes readable once the
  first response *begins streaming*. Fan out N identical requests at once and
  all N pay full price.

### What invalidates what

Invalidation is tiered, in the order `tools` → `system` → `messages`. A change
at one level invalidates that level and everything after it:

| Change | `tools` | `system` | `messages` |
|---|---|---|---|
| tool definitions added, removed or reordered | rebuilt | rebuilt | rebuilt |
| model switched | rebuilt | rebuilt | rebuilt |
| `tool_choice` changed | kept | kept | rebuilt |
| images added or removed | kept | kept | rebuilt |
| thinking / effort parameters changed | model-specific | model-specific | rebuilt |
| message content changed | kept | kept | rebuilt from that point |

"Model-specific" is the documented wording, not a hedge: the thinking config and
`output_config.effort` are rendered into the prompt, so changing either *always*
invalidates the message blocks, and additionally invalidates the tools and system
caches on models that render the configuration ahead of them. Treat it as a full
rebuild unless you have measured your model.

The practical consequence: **appending to `messages` is cheap, touching `tools`
is not.** A loop that rebuilds its tool list per turn from a set, or flips
`tool_choice` mid-run, pays for the whole prefix again every time.

## Related: this is in direct tension with context editing

[`context-editing-preview`](../context-editing-preview/) shows the opposite
lever — `clear_tool_uses_20250919` prunes old `tool_result` bodies server-side to
shrink the prompt. But **clearing invalidates the cached prefix from the edit
point forward**, which is exactly why `clear_at_least` exists: clear enough that
the forced re-write is worth paying for. A long loop running both is trading a
smaller prompt against a colder cache, and **nothing in this lab measures that
trade yet**. Wiring the two together and reporting the net is the obvious next
increment; it is deliberately not in this one.

## Deviations from the research note's Layer-3 sketch

Three, all toward the repo's correctness posture:

- **`Saving` stores three fields, not eight.** The note listed `written`,
  `read`, `read_fraction` and five dollar figures as fields. Stored, they can
  drift out of sync with each other; a `Saving` whose `net_saving_usd` did not
  equal `saved - premium` would be constructible. Only `written`, `read` and
  `base_usd_per_mtok` are stored; every other figure is a computed property, so
  the arithmetic relations hold by construction. The reading API the note
  specified (`saving.net_saving_usd`, …) is unchanged.
- **A missing key exits 0, not 1.** The note's Layer-3 sketch said
  `EXIT_NO_KEY = 1`; its Layer-2 spec and acceptance criterion 2 both say exit 0.
  The acceptance criterion won — see the exit-code table above for why it is the
  right answer for a demo that spends money.
- **`run()` raises `CacheMiss`, and `main()` turns that into `EXIT_NO_CACHE_HIT`.**
  The note said `run()` should raise `SystemExit` directly. `SystemExit` carries
  either a message or a code, not both, and the note also wants exit code 2; a
  named exception at the seam gives both, and keeps `run()` free of printing.

Two boundary checks are stricter than the note asked for, in the same spirit:
a non-`int` `budget` is a `TypeError` (`budget=True` reads as a flag), and an
empty `content` list is a `ValueError` (there is no last block to mark, and the
API rejects it anyway).

## Footnotes

- **Automatic caching is real, and deliberately unused.**
  `client.messages.create(..., cache_control={"type": "ephemeral"})` is a
  documented top-level parameter: the API places one breakpoint on the last
  cacheable block and moves it forward as the conversation grows, consuming one
  of the four slots. This example uses explicit block-level markers instead, so
  the placement policy is visible, testable and pure — but if you want the
  rolling marker and nothing else, the kwarg is the shorter road.
- **The 1-hour TTL is out of scope.** `{"type": "ephemeral", "ttl": "1h"}`
  writes at 2× instead of 1.25× and reads at the same 0.10×. Neither TTL needs a
  beta header any more. When it is in use, `usage.cache_creation` carries a
  nested `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens` breakdown;
  `report.py` reads only the flat `cache_creation_input_tokens`.
- **Server tool results** (web search, code execution) get an *automatic*
  breakpoint on the tool result before the next loop iteration — but only if the
  request already carries at least one `cache_control` marker, and always at the
  5-minute TTL. Client tools, which is this example, get nothing automatic.
