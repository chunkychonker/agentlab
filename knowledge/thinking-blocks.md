# Thinking blocks: the round-trip contract

`thinking` / `redacted_thinking` content blocks are generated output that sits
beside the canonical `text`/`tool_use` blocks. The rule that bites: in a
**tool-use turn** (and multi-turn), you must send every thinking block from the
assistant message **back to the API complete and unmodified**, in original order,
alongside the `tool_use` block it accompanied. Modified/reordered/partially-dropped
thinking blocks are rejected with a **400**. (Docs, "Preserving thinking blocks",
fetched 2026-09-02.)

- The `signature` field is an encrypted copy of the full reasoning; the server
  decrypts it to reconstruct context. Preserve it byte-for-byte.
- A tool-use loop is **one assistant turn**: `[thinking] + [tool_use]` →
  `[tool_result]` → `[text]`. In **manual** mode the API also enforces that the
  final assistant turn *begins* with a thinking block; adaptive mode drops that.
- Filtering blocks by `type == "thinking"` when echoing the turn back **silently
  drops `redacted_thinking`** and breaks the protocol — filter for both, or echo
  `content` verbatim (what [[tool-use-loop]] / the streaming loop already do).
- `redacted_thinking` = `{"type":"redacted_thinking","data":"<opaque>"}`,
  returned when reasoning is safety-redacted. Round-trip `data` unchanged.
- The one edit the API tolerates: text you put in the empty `thinking` field of a
  `display:"omitted"` block is ignored, not rejected.
- You don't prune old thinking yourself — the API filters prior-turn blocks and
  bills only what it shows the model. Which prior-turn blocks survive is
  per-model (Haiku 4.5 and earlier: "keep last turn only", auto-stripped).
  Override with `clear_thinking_20251015` ([[context-editing]]).

## Streaming shape

`thinking` block: `content_block_start` with `content_block =
{"type":"thinking","thinking":"","signature":""}` → one-or-more
`content_block_delta` with `delta.type == "thinking_delta"` (`.thinking` string
fragment, append like text) → **one** `content_block_delta` with
`delta.type == "signature_delta"` (`.signature`, just before stop) →
`content_block_stop`.

- `display:"omitted"` (default on Opus 5 / Sonnet 5 / Opus 4.7–4.8): **no
  `thinking_delta`s** — block opens, one `signature_delta`, closes.
  `thinking == ""` is a legal finished value; don't treat zero deltas as
  truncation.
- `display:"summarized"` (default Sonnet 4.6, Haiku 4.5, earlier): real
  `thinking_delta` text streams.
- `redacted_thinking` streams as a delta-less `content_block_start` (full `data`)
  + `content_block_stop` — same shape as a `fallback` block. Not in an official
  streaming trace; corroborated by
  [maximhq/bifrost#5093](https://github.com/maximhq/bifrost/issues/5093).
- Seen in the wild: an Anthropic **service-side** bug where `signature_delta`
  carried an `index` pointing at a `tool_use` block
  ([looplj/axonhub#1105](https://github.com/looplj/axonhub/issues/1105)) — worth
  a defined loud failure for "signature_delta on a non-thinking index".
- The `usage` `thinking_tokens` breakdown appears only on the final
  `message_delta` when streaming.

## Model support (manual vs adaptive)

- Manual: `thinking:{type:"enabled", budget_tokens:N}`, `N >= 1024` and
  `N < max_tokens`. Only mode on Sonnet 4.5 / Opus 4.5 / **Haiku 4.5** / earlier.
  `tool_choice` limited to `auto` or `none`. **Deprecated on 4.6, 400 on 4.7+.**
- Adaptive: `thinking:{type:"adaptive"}` (+ `output_config.effort`), no
  `budget_tokens`. Required on 4.7+ / 5-series. Interleaves automatically.
- The **streaming event shapes are identical** across modes, so a stream
  accumulator is mode-agnostic; only the request's `thinking=` kwarg is
  model-specific.
- Haiku 4.5 has no interleaved thinking (fine for a single leading thinking
  block per turn).

Sources (fetched 2026-09-02): [thinking](https://platform.claude.com/docs/en/build-with-claude/thinking),
[extended-thinking](https://platform.claude.com/docs/en/build-with-claude/extended-thinking),
[streaming](https://platform.claude.com/docs/en/build-with-claude/streaming).
First applied in `research/2026-09-02-streaming-thinking-accumulator.md`
(extending `examples/streaming-tool-loop/`).

Related: [[streaming-tool-use]], [[tool-use-loop]], [[context-editing]], [[anthropic-models]]
