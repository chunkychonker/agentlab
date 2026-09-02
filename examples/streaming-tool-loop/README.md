# Streaming the hand-written tool loop

The same one-tool loop as [`examples/minimal-agent-loop/`](../minimal-agent-loop/),
but driven by `client.messages.stream()` instead of `client.messages.create()`.
One thing changes, and it changes everything about the loop's plumbing: a
`tool_use` block's `input` no longer arrives as a value. It arrives as a run of
`input_json_delta` events carrying **string fragments of JSON**, none of which is
valid JSON on its own. You cannot call the tool until you have concatenated every
fragment for that block and parsed the result.

Deliberately **not** using the SDK's own accumulator (`stream.get_final_message()`)
or `stream.text_stream` — the point is to write the accumulation by hand so the
mechanics are visible, and to prove it correct offline.

The loop runs with **extended thinking on**, which is the second thing streaming
makes your problem. A thinking-enabled tool turn must go back to the API with its
`thinking` (and `redacted_thinking`) blocks *complete, unmodified and in order*,
or the next request is a 400 — and over a stream those blocks do not arrive as
values either. `thinking` comes as `thinking_delta` text fragments plus a
`signature_delta` carrying the encrypted signature; `redacted_thinking` arrives
whole in its `content_block_start` with no deltas at all. Both are assembled in
the same pure accumulator, in wire shape, so the shell can echo them straight
back.

From the research notes:
[`research/2026-08-16-streaming-tool-loop.md`](../../research/2026-08-16-streaming-tool-loop.md)
(text / `tool_use`) and
[`research/2026-09-02-streaming-thinking-accumulator.md`](../../research/2026-09-02-streaming-thinking-accumulator.md)
(thinking blocks). Background:
[`knowledge/thinking-blocks.md`](../../knowledge/thinking-blocks.md).

## What's here

| File | What it is |
|------|-----------|
| `accumulator.py` | The pure core: `accumulate(events) -> AccumulatedMessage(stop_reason, content)`. No I/O, no network, no SDK import. |
| `agent.py` | The imperative shell: the safe `calculator` tool, its schema, and `run_agent_streaming()` — the loop over streamed turns. |
| `test_agent.py` | Offline self-test: replays recorded event sequences and a fake `messages.stream()`. No key, no network. |
| `requirements.txt` | `anthropic` — only needed for the live run. |

The calculator is copied from `minimal-agent-loop` rather than imported: examples
in this repo are self-contained, and nothing here imports across example
directories. It is a **safe** calculator — it walks an `ast` tree and allows only
numbers, `+ - * / // % **` and parentheses, so `__import__('os')` returns an error
string and never executes.

## The fixture: what the API actually sends

This is the transcript the first test replays, transcribed from the streaming
docs' own `get_weather` example (quoted in the research note). Note that
`content_block_start` opens `input` as an **empty object**, and that the fragments
split mid-string:

```
content_block_start: {"type":"tool_use","id":"toolu_01T1x1fJ34qAmk2tNTrN7Up6","name":"get_weather","input":{}}
content_block_delta: {"type":"input_json_delta","partial_json":""}
content_block_delta: {"type":"input_json_delta","partial_json":"{\"location\":"}
content_block_delta: {"type":"input_json_delta","partial_json":" \"San"}
content_block_delta: {"type":"input_json_delta","partial_json":" Francisc"}
content_block_delta: {"type":"input_json_delta","partial_json":"o,"}
content_block_delta: {"type":"input_json_delta","partial_json":" CA\"}"}}
content_block_stop: {"index":1}
```

Concatenated in arrival order that is `{"location": "San Francisco, CA"}` — and it
is valid JSON **only** once the block closes. `content_block_stop` carries no
content of its own; the finished block exists only if you built it.

(The docs' full transcript opens with a text block at index 0 before this tool
block at index 1. The test fixture keeps that text block — its exact wording is
not load-bearing, but its presence is: block indices are positions in the final
`content`, so they have to be contiguous.)

A thinking block, from the same docs' thinking trace. Note that the start seeds
**both** `thinking` and `signature` to `""` — empty, not absent, the same
convention `input:{}` uses — and that the single `signature_delta` lands just
before the stop:

```
content_block_start: {"type":"thinking","thinking":"","signature":""}
content_block_delta: {"type":"thinking_delta","thinking":"I need to find the GCD"}
content_block_delta: {"type":"thinking_delta","thinking":" of 27 and 18. Let me use"}
content_block_delta: {"type":"thinking_delta","thinking":" the Euclidean algorithm."}
content_block_delta: {"type":"signature_delta","signature":"EqQBCgIYAhIM1gbcDa9GJwZA2b..."}
content_block_stop:  {"index":0}
```

A `redacted_thinking` block has no deltas at all — the `content_block_start`
carries the whole opaque `data` and the block closes immediately:

```
content_block_start: {"type":"redacted_thinking","data":"EroBCoYBGAIiQAmvSHSk1Xl1..."}
content_block_stop:  {"index":0}
```

> **Provenance.** The `thinking` trace above is from Anthropic's own streaming
> docs. The `redacted_thinking` one is **not**: Anthropic publishes the
> non-streaming JSON (`{"type":"redacted_thinking","data":"..."}`) but no
> streaming event trace for it. The delta-less start/stop shape is taken from a
> third-party bug report
> ([maximhq/bifrost#5093](https://github.com/maximhq/bifrost/issues/5093), whose
> whole subject is a proxy that dropped the block because it had no
> `content_block_start` case for it) plus the docs' identical description of a
> `fallback` block as "a `content_block_start` and `content_block_stop` pair with
> no deltas in between". Treat it as well-corroborated inference, not as an
> official trace. The `data` and `signature` values printed above are
> shape-accurate placeholders — the docs truncate the signature and never print a
> real `data` — and nothing in this example ever inspects either one.

## Run the self-test (no API key needed)

```bash
cd examples/streaming-tool-loop
python3 test_agent.py
```

Expected output:

```
ok  docs' tool_use SSE transcript rebuilds id, name and parsed input
ok  text-only stream concatenates deltas and reports stop_reason
ok  streaming loop dispatches the tool and returns the final answer
ok  truncated tool input raises JSONDecodeError instead of an empty dict
ok  15 broken event sequences each raise ValueError
ok  loop enforces max_turns and raises when exceeded
ok  thinking_delta fragments + signature_delta rebuild a thinking block
ok  display:omitted thinking block yields thinking="" without raising
ok  missing signature is allowed and split signatures join in order
ok  redacted_thinking block round-trips its opaque data with no deltas
ok  loop echoes the thinking block unmodified, ahead of the tool_use
ok  verbose echo prints thinking + text and yields every event unchanged
ok  calculator safely rejects forbidden/malformed input

All 13 self-tests passed.
```

The third line is the load-bearing one for the loop: a fake `messages.stream()`
context manager serves one recorded event list per turn (tool-use turn, then final
text turn), and the test asserts the loop (a) ran the calculator on the
**reassembled** input `{"expression": "4839 * 1284"}`, (b) sent back a
`tool_result` whose `tool_use_id` matches the `tool_use` block, and (c) returned
the final text — the same three assertions `minimal-agent-loop`'s test makes, so
the streaming loop is shown behaviorally equivalent, not merely similar.

The eleventh line is its thinking counterpart, and it is the one that stands in
for the 400 nobody wants to reproduce live: the same two-turn run, but with a
`thinking` block at index 0 ahead of the `tool_use`, asserting that the
**second** request's assistant turn still contains that block with its text and
signature byte-identical and still positioned first. It also checks the request
carried `thinking={"type":"enabled","budget_tokens":1024}` with
`budget_tokens < max_tokens`, since the reverse is a 400 before a single token is
generated.

## Run it live (needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 agent.py
```

It asks *"What is 4839 * 1284, and is that more than five million?"*, prints the
reasoning and answer text token-by-token as they stream (thinking fragments
behind a `[thinking]` marker), prints the calculator being dispatched, then the
final answer. Without `ANTHROPIC_API_KEY` set, `agent.py` prints a one-line note
and exits 0 — it never crashes.

Model id is the constant `MODEL` at the top of `agent.py` (default
`claude-haiku-4-5`, the cheapest current model). See
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

**`MODEL` and `THINKING` are coupled**, and the coupling is a hard 400 if you
break it. `THINKING = {"type": "enabled", "budget_tokens": 1024}` is *manual*
mode: it is the only mode Haiku 4.5 and the rest of the 4.5 generation support,
it is deprecated on the 4.6 models, and 4.7 and later **reject it**, requiring
`{"type": "adaptive"}` instead. So switching `MODEL` forward means switching
`THINKING` with it. `budget_tokens` must also be at least 1024 and strictly less
than `max_tokens`, which is why `MAX_TOKENS` is 4096 rather than the 1024 this
example used before thinking was enabled. Details in
[`knowledge/thinking-blocks.md`](../../knowledge/thinking-blocks.md).

Whether the first turn comes back as `[thinking, tool_use]` or with a leading
text block is model behavior, not something this code pins down: the accumulator
takes any order as long as the indices are contiguous, and the loop already
tolerates leading text.

**The live run has not been made.** Everything up to the wire is verified without
a key — the accumulator against the docs' own event shapes, the loop's echo of
the assistant turn, and the `thinking`/`max_tokens` kwargs the request carries.
What is unverified is what Anthropic's servers return for this request: that the
first streamed turn really arrives as `[thinking, tool_use]` on Haiku 4.5, and
that the echoed turn really comes back without a 400. No output has been invented
to stand in for it; when someone runs it with a key, paste the real stdout under
this heading.

## How the accumulator behaves

Blocks are assembled strictly **by index**, into either an open builder or a
finished dict — never both — so "a delta for a block that was never started" is a
lookup miss rather than a flag someone has to remember to check.

- `text` blocks append `delta.text`.
- `tool_use` blocks *store `partial_json` fragments as strings* and call
  `json.loads` exactly once, at that index's `content_block_stop`. Never
  speculatively: a fragment can split mid-string, so an intermediate
  concatenation is not reliably parseable.
- `thinking` blocks append `delta.thinking` from every `thinking_delta` and
  collect `delta.signature` from every `signature_delta`, joining both at the
  stop. The `thinking:""`/`signature:""` on the opening `content_block_start`
  are ignored, exactly as the opening `input:{}` is — the content is entirely in
  the deltas.
- `redacted_thinking` blocks take their whole `data` from the
  `content_block_start` and expect **no deltas whatsoever**; the `data` is opaque
  and is copied out byte-for-byte, never inspected or re-encoded.
- `ping` and any event whose `.type` is unrecognized are ignored (forward-compat,
  per the docs' "Other events" note).
- `stop_reason` comes from the last `message_delta` that carried one; earlier
  ones can carry `null`.

Failures are loud, because the alternative is calling a tool with silently-wrong
arguments:

| Situation | Result |
|---|---|
| Accumulated `partial_json` is not valid JSON at `content_block_stop` (truncated stream) | `json.JSONDecodeError` naming the index — never `{}` |
| Delta or stop for an index with no `content_block_start` | `ValueError` naming the index |
| Second `content_block_start` for an index already open, or already finished | `ValueError` naming the index |
| Delta type that does not match its block (`text_delta` into a `tool_use`, `thinking_delta` into a `text`, `text_delta` into a `thinking`, ...) | `ValueError` naming the index |
| `signature_delta` whose index is a `tool_use` block | `ValueError` naming the index |
| Any delta at all for a `redacted_thinking` index | `ValueError` naming the index |
| `redacted_thinking` `content_block_start` with no string `data` | `ValueError` naming the index |
| Tool input that parses to a non-object (`[1, 2]`) | `ValueError` naming the index |
| Stream ends with a block still open, or a gap in the indices | `ValueError` naming the index |

An empty fragment list is treated as a truncated stream, not as `{}` — a
`tool_use` block that closes having sent no JSON at all is indistinguishable from
one whose deltas were lost, and guessing `{}` is exactly the silent-wrong-input
failure the spec forbids.

The `signature_delta`-on-a-`tool_use`-index row is not hypothetical: it is a
real Anthropic service-side bug seen in the wild
([looplj/axonhub#1105](https://github.com/looplj/axonhub/issues/1105)), where the
signature arrived carrying the wrong index. It falls out of the existing
delta-type guard for free, and the test pins it.

### Three cases the docs do not settle

Two of these would be plausible failures and are deliberately allowed; the third
is a tolerance. In all three the docs are silent or ambiguous, so the choice is
this example's, and it is stated rather than buried:

| Situation | This example's answer | Why |
|---|---|---|
| A `thinking` block closes having received **zero `thinking_delta`s** | `thinking: ""`, no error | This is the documented `display:"omitted"` case (the default on the newest models), where the block opens, takes one `signature_delta` and closes. `""` is a legal finished value, so treating "no deltas" as truncation the way `tool_use` does would reject a normal stream. |
| A `thinking` block closes having received **zero `signature_delta`s** | `signature: ""`, no error | The docs imply exactly one always arrives, so this *is* anomalous — but a pure accumulator cannot know the model or the display mode, and the API rejects an unsigned thinking block anyway. The loud failure belongs at the boundary that can actually tell, not in the transcriber. Raising here would also be the one place this module invents a rule the wire protocol never stated. |
| More than one `signature_delta` for one block | Joined in arrival order | The docs say "a single `signature_delta`" everywhere, and joining one fragment is the identity, so this is a strict superset of the documented behavior that costs nothing and survives the docs being incomplete. A strict exactly-one check would satisfy the docs equally and was the alternative. |

Both empty-string outcomes are round-tripped as-is rather than dropped: a
half-built thinking block that the API refuses is a far better outcome than a
silently omitted one, which corrupts the turn *and* produces a 400 whose cause is
invisible.

## Two notes on the shape

**Blocks are plain dicts**, not SDK objects. `minimal-agent-loop`'s loop reads
`block.type` off the SDK's response objects; here the loop reads `block["type"]`,
because the accumulator builds the blocks itself. The dicts are already in the
API's wire shape, so the assistant turn can be echoed straight back into
`messages` with no conversion.

**Printing the stream is the shell's job.** The research note left it open
whether the live path should also expose the raw event loop. It does, via a
one-screen pass-through generator (`_echo_deltas`) that prints `text_delta` and
`thinking_delta` fragments as they fly past and yields every event onward
unchanged. Wrapping the iterator instead of teaching the accumulator to print
keeps `accumulate()` pure and the offline contract identical — the self-test
asserts the echoed event list is identical to its input, so printing can never
become transforming. Reasoning gets a `[thinking]` marker and a closing newline
because `thinking_delta` and `text_delta` fragments are otherwise one
undifferentiated run of prose; the `signature` is not prose and is never printed.

## Scope

One tool, one loop, base streaming behavior plus a single leading thinking block
per turn. `thinking` and `redacted_thinking` used to be listed here as out of
scope — `accumulate()` raised on a `content_block_start` for one — and are now
assembled; a `content_block_start` for any *other* unhandled type (e.g.
`server_tool_use`) still raises rather than being dropped.

Explicitly out of scope, and left for later backlog items: interleaved thinking
and multiple thinking blocks *between* tool calls; adaptive thinking
(`{"type":"adaptive"}`) and therefore 4.7+/5-series models in the live runner;
`display:"updates"` progress-update semantics; the `thinking_tokens` breakdown on
the final `message_delta`; client-side pruning of prior-turn thinking blocks and
`clear_thinking_20251015` (see
[`examples/context-editing-preview/`](../context-editing-preview/)); fine-grained
tool streaming (`eager_input_streaming`); stream error recovery/resumption after
a dropped connection; multi-tool registries; and the SDK's own
`get_final_message()` / `text_stream` helpers.
