# Streaming the hand-written tool loop

## Question

`examples/minimal-agent-loop/` writes the tool-use loop by hand against
`messages.create()`. What changes if the same loop is driven by
`messages.stream()` instead, where a `tool_use` block's `input` arrives as a
sequence of `input_json_delta` fragments (`partial_json` strings) that must be
accumulated and JSON-parsed before the tool can actually be called — and can
that accumulation be proven correct offline, the same way the non-streaming
loop is?

## Findings

**Primary source, current as of this cycle:** the official streaming docs at
[platform.claude.com/docs/en/build-with-claude/streaming](https://platform.claude.com/docs/en/build-with-claude/streaming)
(fetched 2026-08-16; examples use `claude-opus-5`/`claude-sonnet-5`, consistent
with `knowledge/anthropic-models.md`'s 2026-08-12 pricing check, so the page is
current — no staleness flag needed). I read the full page, not a snippet; SSE
event bodies below are copied verbatim from it. No `claude-api` skill is
installed in this environment (`~/.claude/skills/` only has `graphify/`), so I
went straight to the primary docs instead, per the instruction to prefer
primary sources.

### Event flow (confirmed against the docs' literal SSE transcripts)

Every stream is: one `message_start` (a `Message` with empty `content`), then
per content block a `content_block_start` → one-or-more `content_block_delta`
→ `content_block_stop` (each block carries an `index` matching its final
position in `content`), then one-or-more `message_delta` events (top-level
changes — `stop_reason`, cumulative `usage`), then `message_stop`. `ping`
events can appear anywhere and must be ignored; unknown event types must be
ignored too (forward-compat policy, same page).

### Text vs. tool-use deltas — the key asymmetry

- **Text block:** `content_block_start` opens with `content_block: {"type":
  "text", "text": ""}`; each `content_block_delta` is `{"type": "text_delta",
  "text": "..."}` — literal text to append.
- **Tool-use block:** `content_block_start` opens with `content_block:
  {"type": "tool_use", "id": "toolu_...", "name": "get_weather", "input":
  {}}` — `input` starts as an **empty object**, not absent. Each subsequent
  `content_block_delta` is `{"type": "input_json_delta", "partial_json":
  "..."}` — a **string fragment of JSON**, not a value. The doc's own example
  (verbatim):

  ```
  content_block_start: {"type":"tool_use","id":"toolu_01T1...","name":"get_weather","input":{}}
  content_block_delta: {"type":"input_json_delta","partial_json":""}
  content_block_delta: {"type":"input_json_delta","partial_json":"{\"location\":"}
  content_block_delta: {"type":"input_json_delta","partial_json":" \"San"}
  content_block_delta: {"type":"input_json_delta","partial_json":" Francisc"}
  content_block_delta: {"type":"input_json_delta","partial_json":"o,"}
  content_block_delta: {"type":"input_json_delta","partial_json":" CA\"}"}}
  content_block_stop: {"index":1}
  ```

  Concatenating every `partial_json` in arrival order for that index yields
  `{"location": "San Francisco, CA"}` — valid JSON only once complete. The
  docs explicitly say: accumulate the string deltas and parse once
  `content_block_stop` arrives (or use a partial-JSON parser mid-stream if you
  need incremental values — not needed for this increment, since a tool can't
  be called on a half-formed input anyway).
- Doc's own caveat: "current models only support emitting one complete key and
  value property from `input` at a time" — chunks won't interleave across
  keys, but a single value can still split mid-string, so accumulation must
  never assume delta boundaries align with JSON tokens.
- `content_block_stop` carries **no content** — it only signals "this index is
  done." The full block must be reconstructed client-side from the
  `content_block_start` + accumulated deltas; nothing hands you the finished
  block for free outside the SDK's own accumulator.

### Python SDK shape (confirmed from SDK source, not memory)

`with client.messages.stream(...) as stream:` is a context manager; iterating
it directly (`for event in stream:`) yields the raw typed events — the docs'
own "streaming request with thinking" Python example does exactly this and
branches on `event.type`. Event attributes, confirmed against
`anthropic-sdk-python`'s `_messages.py` streaming module:

- `content_block_start`: `.type`, `.index`, `.content_block` (`.type`, `.id`,
  `.name`, `.input` for `tool_use`).
- `content_block_delta`: `.type`, `.index`, `.delta` (`.delta.type` is
  `"text_delta"` → `.delta.text`, or `"input_json_delta"` → `.delta.partial_json`).
- `content_block_stop`: `.type`, `.index`.
- `message_start`: `.message` (a `Message` with `.content == []`).
- `message_delta`: `.delta.stop_reason`, `.delta.stop_sequence`; `.usage`.
- `message_stop`: `.type` only.
- Raw event classes for offline construction: `RawMessageStartEvent`,
  `RawContentBlockStartEvent`, `RawContentBlockDeltaEvent`,
  `RawContentBlockStopEvent`, `RawMessageDeltaEvent`, `RawMessageStopEvent` —
  Pydantic models, but for this repo's established offline-test style
  (`SimpleNamespace` blocks in `test_agent.py`), plain `SimpleNamespace`
  objects with matching attributes are sufficient and require no SDK import,
  same trick already used for `messages.create()` responses.
- The SDK also offers `stream.get_final_message()` (accumulates for you) and
  `stream.text_stream` (text only). Neither is used here — the whole point,
  matching `minimal-agent-loop`'s stated scope, is to hand-write the
  accumulation so the mechanics are visible, not to lean on the SDK helper.

### Adjacent but out of scope

The docs also mention **fine-grained tool streaming** (`eager_input_streaming`,
per-tool opt-in, linked from the streaming page to
`/docs/en/agents-and-tools/tool-use/fine-grained-tool-streaming`) — it changes
partial-JSON delivery to avoid server-side buffering for lower latency. I did
not fetch that page; it's a distinct, more advanced feature layered on top of
the base `input_json_delta` mechanism above, and is explicitly out of scope
for this increment (default streaming behavior is what needs proving first).

**Error recovery** (resuming an interrupted stream by replaying partial
content as a continuation message) is documented on the same page but is a
separate concern from accumulation and is not part of this increment either.

## Build proposal

### Intent

Prove, offline and deterministically, that a hand-written accumulator
correctly reconstructs `tool_use` blocks (id, name, and a fully-parsed `input`
dict) and text blocks from a raw Anthropic streaming event sequence — then
drive the same tool-dispatch loop as `examples/minimal-agent-loop/` off of
that accumulator instead of a already-complete `Message`. Out of scope:
fine-grained tool streaming (`eager_input_streaming`), stream error
recovery/resumption, multi-tool registries (one tool, reusing the existing
safe calculator pattern), and anything requiring the SDK's own
`get_final_message()`/`text_stream` helpers — the point is to see the raw
mechanics, same framing as the example it extends.

### Behavioral spec

**Inputs:** an ordered iterable of raw stream events, each with `.type` and
type-specific fields as enumerated in Findings above (either real SDK events
from a live `client.messages.stream()`, or `SimpleNamespace` fakes in tests).

**Output:** a single object exposing `.stop_reason` (from the last
`message_delta`) and `.content` — a list of blocks in index order, each either
`{"type": "text", "text": <full string>}` or `{"type": "tool_use", "id":
<str>, "name": <str>, "input": <dict>}` — structurally interchangeable with
what `minimal-agent-loop.agent.run_agent` already consumes from a non-streaming
`response.content`, so the existing dispatch-and-`tool_result` logic needs no
changes, only a new event source.

**Invariants:**
- Blocks are assembled strictly by `index`; a `content_block_delta` or
  `content_block_stop` for an index with no matching `content_block_start`
  is a bug and must raise, not silently no-op.
- `input_json_delta.partial_json` fragments are concatenated in arrival order
  per index and parsed with `json.loads` **only** at that index's
  `content_block_stop` — never parsed speculatively mid-stream (partial JSON
  can be invalid JSON at any point before the block closes).
- `ping` events and any event whose `.type` is not one of the six known types
  are ignored, not errors (forward-compat, per the docs' "Other events" note).
- The accumulator is a pure function/class: no I/O, no network — it only
  consumes an iterable and returns a value, so it is testable with a plain
  Python list of fakes.

**Failure modes:**
- Malformed/incomplete JSON at `content_block_stop` (e.g. a truncated stream)
  raises `json.JSONDecodeError` — not swallowed into an empty dict, since a
  tool must never be called with silently-wrong input.
- An event for an unopened index, or two `content_block_start`s for the same
  index without an intervening `content_block_stop`, raises `ValueError`
  naming the index — fail loud per repo protocol §4, don't guess.
- `max_turns` cap reused unchanged from `minimal-agent-loop` (same runaway-loop
  protection, now applied across streamed turns).

**Acceptance criteria (the self-test, no key, no network):**
1. Replaying the doc's own verbatim tool-use SSE sequence (the
   `get_weather`/`San Francisco, CA` example above, transcribed as
   `SimpleNamespace` events) through the accumulator yields a `tool_use` block
   with `id="toolu_01T1x1fJ34qAmk2tNTrN7Up6"`, `name="get_weather"`,
   `input={"location": "San Francisco, CA"}` — byte-for-byte reconstruction
   from the same fragments the real API sent.
2. Replaying a text-only sequence yields the concatenated text and
   `stop_reason` from the trailing `message_delta`.
3. A scripted two-turn streaming run (tool-use turn → tool dispatch → final
   text turn), through a fake `client.messages.stream()` context manager that
   yields one of these event lists per turn, produces the same final answer
   string and the same `tool_result`/`tool_use_id`-matching behavior already
   asserted in `minimal-agent-loop/test_agent.py` — proving the streaming loop
   is behaviorally equivalent to the non-streaming one, not just structurally
   similar.
4. A malformed-partial-JSON case (final concatenation is not valid JSON)
   raises `json.JSONDecodeError` rather than returning a wrong or empty input.
5. `python test_agent.py` runs these fully offline and prints a pass line per
   case, matching the existing example's convention.

### Interfaces (stubs only — builder fills bodies)

```python
# accumulator.py — pure, no I/O
from dataclasses import dataclass, field

@dataclass
class AccumulatedMessage:
    stop_reason: str | None
    content: list[dict]  # each {"type": "text", "text": str} or
                          # {"type": "tool_use", "id": str, "name": str, "input": dict}

def accumulate(events) -> AccumulatedMessage:
    """Consume a raw Anthropic stream event sequence and return the
    reconstructed message. Raises ValueError on out-of-order block events,
    json.JSONDecodeError on unparseable tool_use input at content_block_stop.
    """
    ...

# agent_streaming.py — thin shell, reuses minimal-agent-loop's TOOLS/TOOL_FUNCTIONS shape
def run_agent_streaming(client, user_message: str, *, max_turns: int = 5, verbose: bool = False) -> str:
    """Same contract as minimal_agent_loop.agent.run_agent, but drives the loop
    from client.messages.stream() + accumulate() instead of client.messages.create().
    """
    ...
```

### Where it goes

New directory `examples/streaming-tool-loop/` (confirmed unclaimed: not in
`ls examples/`, no open PR, no other branch touches it as of 2026-08-16).
Files, mirroring `minimal-agent-loop/`'s layout: `accumulator.py`,
`agent.py` (the streaming loop + the same safe calculator tool, duplicated
locally since examples are self-contained — no cross-example imports exist
anywhere in the repo), `test_agent.py`, `requirements.txt`, `README.md`
(link back to this note, plus the SSE transcript above as the fixture the
first acceptance test replays). Live entry point (`python agent.py`) follows
`minimal-agent-loop`'s exact pattern: no-key → print a note and exit 0;
with-key → stream a real call and print tool calls as they're dispatched plus
the final answer.

## Open questions

- Whether to also expose the raw event loop (`for event in stream:`) in the
  live path for visibility (printing deltas as they arrive) or just call
  `accumulate()` once per turn and print the result — the docs' own examples
  do both depending on the use case; leaving this as a builder call since it
  doesn't affect the offline-testable contract.
- Fine-grained tool streaming (`eager_input_streaming`) and stream error
  recovery are real, documented features but deliberately deferred — good
  candidates for a future backlog item once this base accumulator exists to
  build on.
- I could not verify the `claude-api` skill's guidance directly (not installed
  in this environment) and relied on the primary platform docs + SDK source
  instead; if that skill is present in the builder's environment, it's worth a
  quick cross-check against these findings before implementation.
