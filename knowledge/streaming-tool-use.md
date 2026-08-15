# Streaming the tool-use loop

The streaming sibling of [[tool-use-loop]]: same loop mechanics
(dispatch → echo assistant turn → `tool_result` → repeat), but a `tool_use`
block's `input` no longer arrives whole — it's assembled from an SSE event
sequence, and nothing hands you the finished block for free unless you use the
SDK's own accumulator (`stream.get_final_message()`).

## Event flow

`message_start` (empty `content`) → per content block: `content_block_start`
→ one-or-more `content_block_delta` → `content_block_stop` (each block has an
`index` matching its final position in `content`) → one-or-more
`message_delta` (top-level changes: `stop_reason`, cumulative `usage`) →
`message_stop`. `ping` events and any event with an unrecognized `.type` can
appear anywhere and must be ignored (forward-compat policy).

## The asymmetry that matters

- **Text delta:** `content_block_delta.delta = {"type": "text_delta", "text":
  "..."}` — literal text, just append.
- **Tool-use delta:** `content_block_start.content_block.input` starts as an
  **empty dict**, not absent. Each delta is `{"type": "input_json_delta",
  "partial_json": "..."}` — a raw **string fragment**, not a value.
  Concatenate every `partial_json` for that index in arrival order; the result
  is only guaranteed to be valid JSON once `content_block_stop` fires for that
  index. Parse with `json.loads` **at `content_block_stop`**, never
  speculatively mid-stream — a fragment can split mid-string, so partial
  concatenations are not reliably parseable JSON. (Anthropic's models today
  only interleave at key/value boundaries — "one complete key and value
  property at a time" — but a single value can still span multiple deltas, so
  don't assume delta boundaries align with any JSON token.)
- `content_block_stop` itself carries no content, only an `index` — the
  client must have already reconstructed the block from `content_block_start`
  + accumulated deltas.

## Python SDK shape (source-verified, not the docs prose)

`with client.messages.stream(...) as stream:` is a context manager; iterate it
directly (`for event in stream:`) for raw typed events. Confirmed attributes:
`content_block_start.content_block.{type,id,name,input}`;
`content_block_delta.delta.{type, text|partial_json}`; `message_delta.delta.
{stop_reason, stop_sequence}` + `.usage`. Raw event classes for offline
construction: `RawMessageStartEvent`, `RawContentBlockStartEvent`,
`RawContentBlockDeltaEvent`, `RawContentBlockStopEvent`,
`RawMessageDeltaEvent`, `RawMessageStopEvent` — but a plain `SimpleNamespace`
with matching attributes is enough for tests (same trick as the non-streaming
loop's fake `Message`), no SDK import required.

## Testing without a key

Same discipline as [[tool-use-loop]]: the accumulator that turns an event
sequence into `{stop_reason, content}` is a pure function — feed it a
hand-transcribed SSE sequence (the docs publish full verbatim ones) as a list
of `SimpleNamespace` fakes and assert the reconstructed `tool_use.input`
matches. Then fake `client.messages.stream()`'s context-manager protocol to
drive the same dispatch loop end-to-end offline.

## Adjacent, not the same thing

- **Fine-grained tool streaming** (`eager_input_streaming`, opt-in per tool) —
  changes partial-JSON delivery to skip server-side buffering for lower
  latency. Not investigated yet; a real feature layered on top of the base
  mechanism above.
- **Stream error recovery** — capturing a partial response after a dropped
  connection and resuming (Claude 4.5 and earlier: replay as an assistant-turn
  prefix; 4.6+: replay as a user-turn "continue from here" instruction). A
  separate concern from accumulation.

Source: [streaming messages docs](https://platform.claude.com/docs/en/build-with-claude/streaming)
(fetched 2026-08-16, examples use `claude-opus-5`/`claude-sonnet-5` so current)
+ `anthropic-sdk-python`'s streaming module (source-verified attribute names).

Related: [[tool-use-loop]], [[anthropic-python-sdk]]
