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

From the research note:
[`research/2026-08-16-streaming-tool-loop.md`](../../research/2026-08-16-streaming-tool-loop.md).

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
ok  10 broken event sequences each raise ValueError
ok  loop enforces max_turns and raises when exceeded
ok  calculator safely rejects forbidden/malformed input

All 7 self-tests passed.
```

The third line is the load-bearing one for the loop: a fake `messages.stream()`
context manager serves one recorded event list per turn (tool-use turn, then final
text turn), and the test asserts the loop (a) ran the calculator on the
**reassembled** input `{"expression": "4839 * 1284"}`, (b) sent back a
`tool_result` whose `tool_use_id` matches the `tool_use` block, and (c) returned
the final text — the same three assertions `minimal-agent-loop`'s test makes, so
the streaming loop is shown behaviorally equivalent, not merely similar.

## Run it live (needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 agent.py
```

It asks *"What is 4839 * 1284, and is that more than five million?"*, prints the
answer text token-by-token as it streams, prints the calculator being dispatched,
then the final answer. Without `ANTHROPIC_API_KEY` set, `agent.py` prints a
one-line note and exits 0 — it never crashes.

Model id is the constant `MODEL` at the top of `agent.py` (default
`claude-haiku-4-5`, the cheapest current model). See
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## How the accumulator behaves

Blocks are assembled strictly **by index**, into either an open builder or a
finished dict — never both — so "a delta for a block that was never started" is a
lookup miss rather than a flag someone has to remember to check.

- `text` blocks append `delta.text`.
- `tool_use` blocks *store `partial_json` fragments as strings* and call
  `json.loads` exactly once, at that index's `content_block_stop`. Never
  speculatively: a fragment can split mid-string, so an intermediate
  concatenation is not reliably parseable.
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
| Delta type that does not match its block (`text_delta` into a `tool_use`, or vice versa) | `ValueError` naming the index |
| Tool input that parses to a non-object (`[1, 2]`) | `ValueError` naming the index |
| Stream ends with a block still open, or a gap in the indices | `ValueError` naming the index |

An empty fragment list is treated as a truncated stream, not as `{}` — a
`tool_use` block that closes having sent no JSON at all is indistinguishable from
one whose deltas were lost, and guessing `{}` is exactly the silent-wrong-input
failure the spec forbids.

## Two notes on the shape

**Blocks are plain dicts**, not SDK objects. `minimal-agent-loop`'s loop reads
`block.type` off the SDK's response objects; here the loop reads `block["type"]`,
because the accumulator builds the blocks itself. The dicts are already in the
API's wire shape, so the assistant turn can be echoed straight back into
`messages` with no conversion.

**Printing the stream is the shell's job.** The research note left it open
whether the live path should also expose the raw event loop. It does, via a
one-screen pass-through generator (`_echo_text_deltas`) that prints `text_delta`
fragments as they fly past and yields every event onward unchanged. Wrapping the
iterator instead of teaching the accumulator to print keeps `accumulate()` pure
and the offline contract identical.

## Scope

One tool, one loop, base streaming behavior only. Explicitly out of scope, and
left for later backlog items: fine-grained tool streaming
(`eager_input_streaming`), stream error recovery/resumption after a dropped
connection, multi-tool registries, `thinking` blocks (a `content_block_start`
for one raises rather than being dropped), and the SDK's own
`get_final_message()` / `text_stream` helpers.
