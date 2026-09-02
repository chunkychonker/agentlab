# `thinking` blocks in the streaming accumulator

## Question

What does the Anthropic stream actually send for `thinking` (and `redacted_thinking`)
blocks, and what is the smallest change to `examples/streaming-tool-loop/`'s pure
`accumulate()` that lets the hand-written streaming tool loop run with extended
thinking on and echo those blocks back verbatim without a 400?

## Findings

Sources fetched **2026-09-02** unless noted. The `claude-api` skill is **not
installed on this machine** (confirmed by `research/2026-08-29-prompt-caching-tool-loop.md`;
re-checked `~/.claude/skills/` today — only `graphify/`), so every API fact below
is from primary Anthropic docs, cited inline.

### The streaming event shapes for a thinking block

From the [streaming messages docs](https://platform.claude.com/docs/en/build-with-claude/streaming)
("Thinking delta" section and the "Streaming request with thinking" full trace)
and the [thinking docs](https://platform.claude.com/docs/en/build-with-claude/thinking)
("Streaming thinking" + its "Full streaming event trace" accordion):

```
content_block_start
  {"type":"content_block_start","index":0,
   "content_block":{"type":"thinking","thinking":"","signature":""}}

content_block_delta   (one or more)
  {"type":"content_block_delta","index":0,
   "delta":{"type":"thinking_delta","thinking":"I need to find the GCD ..."}}

content_block_delta   (exactly one, "just before the content_block_stop event")
  {"type":"content_block_delta","index":0,
   "delta":{"type":"signature_delta","signature":"EqQBCgIYAhIM1gbcDa9GJwZA2b..."}}

content_block_stop
  {"type":"content_block_stop","index":0}
```

- `content_block_start.content_block` for a thinking block has `type:"thinking"`
  and **both `thinking` and `signature` seeded to `""`** — same "empty, not
  absent" shape `tool_use` uses for `input:{}`.
- **`thinking_delta`** carries `delta.thinking` — a literal string fragment.
  Append in arrival order, exactly like `text_delta`.
- **`signature_delta`** carries `delta.signature`. The docs say a *single*
  `signature_delta` is "sent just before the `content_block_stop` event ... used
  to verify the integrity of the thinking block." Treating it as
  one-or-more-fragments-concatenated is a safe superset of "exactly one".
- Docs, verbatim: *"Each thinking block also carries a `signature` field, an
  encrypted copy of the full reasoning that you pass back unchanged in multi-turn
  and tool-use conversations."*
- Forward-compat rule is unchanged: *"new event types may be added, and your code
  should handle unknown event types gracefully."*

### `display: "omitted"` — a thinking block with no text

From the streaming docs and thinking docs: when `display:"omitted"` is set,
*"no `thinking_delta` events are sent. The thinking block opens, receives a single
`signature_delta`, and closes."* The finished block is
`{"type":"thinking","thinking":"","signature":"Eosn..."}`. So **`thinking == ""`
is a legal finished value** — the accumulator must not treat "zero thinking
deltas" as truncation the way it treats an empty `tool_use` input as a
`JSONDecodeError`. `omitted` is the default on Opus 5 / Sonnet 5 / Opus 4.7–4.8;
`summarized` (real `thinking_delta` text) is the default on Sonnet 4.6, and
**Haiku 4.5** and earlier.

### `redacted_thinking` in a stream

Non-streaming wire shape, from the thinking docs "Redacted thinking blocks"
section:

```json
{ "type": "redacted_thinking", "data": "..." }
```

*"The `data` field is opaque and encrypted ... pass `redacted_thinking` blocks
back to the API unchanged."* And a direct warning that matches a real bug class:
*"If your code filters content blocks by type (for example, `block.type ==
"thinking"`) when round-tripping responses with tool use, also include
`redacted_thinking` blocks. Filtering on `block.type == "thinking"` alone silently
drops `redacted_thinking` blocks and breaks the multi-turn protocol."*

Anthropic's streaming docs do **not** publish an explicit `redacted_thinking`
event trace. A corroborating third-party bug report
([maximhq/bifrost#5093](https://github.com/maximhq/bifrost/issues/5093), 2026)
states it plainly: *"redacted_thinking arrives complete in content_block_start"*,
*"a start-event union member with no deltas"* — i.e. a `content_block_start`
whose `content_block` is `{"type":"redacted_thinking","data":"..."}` followed
immediately by `content_block_stop`, no `content_block_delta` in between. This is
the same shape the streaming docs describe for a `fallback` content block ("a
`content_block_start` and `content_block_stop` pair with no deltas in between").
The proposal treats this shape as the contract and the README will flag its
provenance.

### Why the loop needs this: the 400

From the thinking docs, "Thinking with tool use" / "Preserving thinking blocks":

- *"when you return tool results, you must pass the thinking blocks from the
  assistant message back to the API, complete and unmodified."*
- *"**Required:** within a tool-use turn, pass thinking blocks back."*
- *"Within the latest assistant message, the sequence of consecutive `thinking`
  blocks must match what the model generated in the original request: you can't
  rearrange, edit, or partially drop them. This includes `redacted_thinking`
  blocks."*
- *"Modified thinking blocks are rejected with a 400 error. ... The one
  exception: text placed in the empty `thinking` field of an omitted block is
  ignored rather than rejected."*
- *"In extended (manual) mode, the API additionally enforces that the final
  assistant turn of a thinking-enabled request begins with a thinking block."*

A tool-use turn is one assistant turn: `[thinking] + [tool_use]` → `[tool_result]`
→ `[text]`. The existing loop in `agent.py` already appends
`message.content` **verbatim** as the assistant turn
(`messages.append({"role": "assistant", "content": message.content})`) and only
filters by type for tool *dispatch* and for the final text join — so once
`accumulate()` puts a wire-shape `thinking` dict into `content`, the loop
round-trips it correctly with **no change to the dispatch/echo logic**. The
change is almost entirely in the pure accumulator.

### Model-support reality (matters for the live run only)

From the [extended thinking docs](https://platform.claude.com/docs/en/build-with-claude/extended-thinking):

- `thinking: {type:"enabled", budget_tokens:N}` (manual mode) is *"deprecated on
  the Claude 4.6 models (requests using it still succeed). Claude 4.7 and later
  models do not support it and reject requests that use it, returning a 400."*
- *"If your model supports only extended thinking (Claude Sonnet 4.5, Claude Opus
  4.5, Claude Haiku 4.5, and earlier Claude 4 models)"* — so the example's
  current `MODEL = "claude-haiku-4-5"` is exactly a manual-mode model and a live
  run uses `thinking: {type:"enabled", budget_tokens: 1024}`.
- `budget_tokens` **min 1024** and **must be `< max_tokens`**; `agent.py`
  currently sets `max_tokens=1024`, so it must rise (e.g. 4096) for a live run.
- Manual mode tool_choice is limited to `auto` (the default the loop uses) or
  `none`.
- *"Claude Haiku 4.5 does not support interleaved thinking."* Fine — the
  increment needs only a single leading thinking block per turn, not reasoning
  *between* tool calls.
- Haiku 4.5 is a *"keep the last turn only"* model for prior-turn thinking (the
  API auto-strips older blocks). The loop is single-turn, so this never bites.
- The streaming event shapes (`thinking_delta`, `signature_delta`, the
  `{"type":"thinking","thinking":"","signature":""}` start) are **identical**
  between manual and adaptive mode, so the accumulator is thinking-mode-agnostic;
  only the live runner's `thinking=` kwarg is model-specific.

### Practitioner gotchas (HN/GitHub, 2026)

- [maximhq/bifrost#5093](https://github.com/maximhq/bifrost/issues/5093): a
  streaming path with *no `content_block_start` case for `redacted_thinking`*
  silently drops the block → upstream **400** on the next tool turn "when
  redaction occurs."
- [looplj/axonhub#1105](https://github.com/looplj/axonhub/issues/1105): an
  Anthropic **service-side** bug where a `signature_delta` arrived with an
  `index` pointing at a `tool_use` block, not a thinking block; the SDK raised
  *"Content block is not a thinking block"*. Rare, but it means "signature_delta
  for a non-thinking index" is a real sequence to have a defined (loud) answer
  for.
- Search summary (lower confidence, from vendor-adjacent guides): *"empty-content
  thinking blocks accumulating in the transcript (orphaned signatures with no
  text/tool payload) also cause 400 errors on every replay."* Reinforces: keep
  thinking blocks paired with the turn that produced them and never synthesize
  one.

### How this maps onto the existing example

`examples/streaming-tool-loop/` today:

- `accumulator.py` `_open_block()` **raises `ValueError` ("unsupported block
  type")** for `type:"thinking"`, and `test_agent.py` asserts that in
  `_bad_sequences()` ("unsupported block type"). README "Scope" lists thinking as
  out of scope, "a `content_block_start` for one raises rather than being dropped".
- `_OpenText` seeds `text=content_block.text` (always `""`) and appends
  `text_delta`s; `_OpenToolUse` ignores the start `input={}` and stores
  `partial_json` strings, parsing once at stop. A `thinking` builder is the same
  pattern: append `thinking_delta`, collect `signature_delta`, emit at stop.

## Build proposal

Layers 1–3 of the Engineering Protocol, for the builder. Extends
`examples/streaming-tool-loop/` **in place** — `accumulator.py`, `agent.py`,
`test_agent.py`, `README.md`. **No new directory.** (Checked: no open PR / branch
touches this example — open PR is #36, unrelated; `cycle/2026-08-16-streaming-tool-loop`
is the merged original.) This is a deliberate, documented expansion of a failure
mode the README currently lists as out of scope; the only callers of
`accumulate()` are this example's own `agent.py` and `test_agent.py`, both
updated in the same increment (Protocol §5 — all consumers in-tree).

### 1. Intent

Teach the pure `accumulate()` to assemble `thinking` blocks (from `thinking_delta`
+ `signature_delta` events) and `redacted_thinking` blocks (from a delta-less
`content_block_start`/`_stop` pair) into `content`, in the exact wire shape the
Messages API accepts, so the streaming tool loop can run with extended thinking
enabled and echo those blocks back verbatim on the tool-result turn without a 400.

**Out of scope:** interleaved thinking / multiple thinking blocks *between* tool
calls; `display:"updates"` progress-update semantics; adaptive-thinking model
support in the live runner; the `usage ... thinking_tokens` breakdown on the
final `message_delta`; fine-grained tool streaming; stream error recovery;
client-side pruning of prior-turn thinking; and the `thinking-binding-controls`
beta / `input_transformations`.

### 2. Behavioral spec

**Inputs** — an iterable of raw stream events (the SDK's typed events or
`SimpleNamespace` fakes), now additionally including any of:

- `content_block_start` with `content_block.type == "thinking"`, fields
  `thinking` (str, `""` at start) and `signature` (str, `""` at start).
- `content_block_delta` with `delta.type == "thinking_delta"`, field
  `delta.thinking` (str fragment).
- `content_block_delta` with `delta.type == "signature_delta"`, field
  `delta.signature` (str; normally exactly one such delta, just before stop).
- `content_block_start` with `content_block.type == "redacted_thinking"`, field
  `data` (str), followed immediately by `content_block_stop`, no deltas.

**Outputs** — `AccumulatedMessage.content`, in index order, where a block may now
also be:

- `{"type": "thinking", "thinking": <str>, "signature": <str>}`
- `{"type": "redacted_thinking", "data": <str>}`

alongside the existing `text` / `tool_use` dicts. `accumulate()`'s signature and
`AccumulatedMessage`'s shape are unchanged.

**Invariants**

- A `thinking` block's `thinking` is the in-order concatenation of every
  `thinking_delta.thinking` for that index (may be `""`).
- Its `signature` is the in-order concatenation of every `signature_delta.signature`
  for that index (normally a single fragment).
- `signature` and `data` are preserved byte-for-byte — nothing trims, normalizes,
  or re-encodes them.
- `index` ↔ position-in-`content` correspondence is unchanged (contiguous from 0).
- `accumulate()` stays pure: no I/O, no clock, no env, no SDK import.
- Existing `text` / `tool_use` / forward-compat / structural-failure behavior is
  byte-for-byte unchanged.

**Failure modes** — all loud, `ValueError` naming the index, in the module's
existing style:

- `thinking_delta` or `signature_delta` for an index that is not an open
  `thinking` block (covers axonhub#1105's server-side "signature_delta on a
  `tool_use` index").
- delta-type / block-type mismatch, extending the existing guard both ways:
  `text_delta` / `input_json_delta` into a `thinking` block; `thinking_delta` /
  `signature_delta` into a `text` or `tool_use` block.
- any `content_block_delta` for a `redacted_thinking` index (it has no deltas).
- `redacted_thinking` `content_block_start` with no `data` attribute.
- a `thinking` block still open when the stream ends → existing "unclosed block"
  `ValueError`.

**Deliberate non-failures**

- A `thinking` block that closes having received zero `thinking_delta`s is valid
  and yields `thinking == ""` (the `display:"omitted"` case) — *not* the
  truncation error an empty `tool_use` input raises.
- A `thinking` block that closes having received zero `signature_delta`s: the
  spec default is **allow `signature == ""`** and let the API boundary reject it
  if it must — the loud failure for a genuinely missing signature belongs at the
  request, not in a pure accumulator that can't know the model or `display`
  mode. Builder may instead choose to raise; whichever, document it in the
  README table.

**Acceptance criteria** (offline unless marked live)

1. Replaying the docs' verbatim thinking+text trace (thinking at index 0 with
   ≥2 `thinking_delta`s + one `signature_delta`, then text at index 1) yields
   `content == [{"type":"thinking","thinking":<joined>,"signature":<sig>},
   {"type":"text","text":<joined>}]` and `stop_reason == "end_turn"`.
2. Replaying a `display:"omitted"` trace (thinking start, one `signature_delta`,
   stop, then text) yields a `thinking` block with `thinking == ""` and the
   signature intact — no exception.
3. Replaying a `redacted_thinking` start/stop pair with no deltas yields
   `{"type":"redacted_thinking","data":<data>}` at its index.
4. A `thinking`(0)+`tool_use`(1) turn replayed through `accumulate()` puts the
   thinking dict first and the existing `tool_use` dict second; driving
   `run_agent_streaming` with a fake client over that turn + a final-text turn
   shows the **second** `stream()` call's `messages` contains the assistant turn
   with the `thinking` block **unmodified** ahead of the `tool_use`, then the
   `tool_result` — same assertion style the existing loop test uses for the
   echoed `tool_use`.
5. Each malformed sequence raises `ValueError` naming the index:
   `signature_delta` whose index is the `tool_use` block; `thinking_delta` into a
   `text` block; `text_delta` into a `thinking` block; `content_block_delta` for
   a `redacted_thinking` index; `redacted_thinking` start with no `data`.
6. Every pre-existing self-test still passes, except the `_bad_sequences()` case
   "unsupported block type" (`_block_start(0, type="thinking", ...)`) — it moves
   out of the failure list into criteria 1–2, and `_open_block`'s docstring
   (which names `thinking` as the example of an unsupported type) plus the
   README's "10 broken event sequences" count and "Scope" paragraph are updated.
7. **Live** (one cheap billed run; key required; state it in the README the way
   the example already states the live run): `python agent.py`, `MODEL =
   "claude-haiku-4-5"`, `thinking={"type":"enabled","budget_tokens":1024}`,
   `max_tokens` raised above the budget (e.g. 4096), the existing calculator
   question. The first streamed turn carries a `thinking` block then a
   `tool_use`; the loop echoes the thinking block back with the `tool_result`;
   the second turn returns final text; **no 400**. Without `ANTHROPIC_API_KEY`,
   `agent.py` prints its one-line note and exits 0 (unchanged).

### 3. Interfaces (no bodies)

```python
# accumulator.py — new named constants (magic strings defined once, per §2)
THINKING_BLOCK = "thinking"
REDACTED_THINKING_BLOCK = "redacted_thinking"
THINKING_DELTA = "thinking_delta"
SIGNATURE_DELTA = "signature_delta"


@dataclass
class _OpenThinking:
    """A thinking block still receiving thinking_delta / signature_delta fragments.

    Failure modes:
      - ValueError if apply() gets a delta whose type is neither thinking_delta
        nor signature_delta (names the index).
    finish() never fails: thinking and signature are plain string joins; an
    empty thinking field is legal (display:"omitted"). See the README table for
    the missing-signature decision.
    """
    thinking_parts: list[str] = field(default_factory=list)
    signature_parts: list[str] = field(default_factory=list)

    def apply(self, index: int, delta) -> None: ...
    def finish(self, index: int) -> dict: ...
        # -> {"type": "thinking",
        #     "thinking": "".join(self.thinking_parts),
        #     "signature": "".join(self.signature_parts)}


@dataclass
class _OpenRedactedThinking:
    """A redacted_thinking block: opaque `data` captured at content_block_start,
    no deltas expected.

    Failure modes:
      - ValueError from apply() for ANY delta (redacted_thinking has none).
      - ValueError at construction (in _open_block) if content_block has no
        `data` attribute.
    """
    data: str

    def apply(self, index: int, delta) -> None: ...   # always ValueError
    def finish(self, index: int) -> dict: ...
        # -> {"type": "redacted_thinking", "data": self.data}


def _open_block(
    index: int, content_block
) -> _OpenText | _OpenToolUse | _OpenThinking | _OpenRedactedThinking:
    # + THINKING_BLOCK          -> _OpenThinking()            (start seeds are "")
    # + REDACTED_THINKING_BLOCK -> _OpenRedactedThinking(data=<content_block.data>)
    # unknown types still raise ValueError naming the index
    ...
```

`accumulate()` body is unchanged in structure — the new block types flow through
the existing `content_block_start` / `_delta` / `_stop` branches and the existing
"unclosed" / "contiguous indices" end checks apply to them for free.

`agent.py`: add `thinking={"type": "enabled", "budget_tokens": THINKING_BUDGET}`
to the `client.messages.stream(...)` kwargs and lift `max_tokens` above the
budget (both as named constants near `MODEL`, with a one-line comment that
manual-mode `thinking` is tied to `MODEL` being a 4.5-era model). **No change**
to tool dispatch or to the assistant-turn echo. Optional nicety: extend
`_echo_text_deltas` to also print `thinking_delta.thinking` (dimmed / prefixed)
so the live run visibly streams reasoning — not required for acceptance.

`test_agent.py`: add fixtures + assertions for criteria 1–6, keep all existing
tests, update the final `All N self-tests passed.` count.

## Open questions

1. Does a `display:"summarized"` reasoning block ever close with **zero**
   `signature_delta`s? Docs say "a single `signature_delta` ... just before
   `content_block_stop`", implying always exactly one for a real reasoning block.
   Not verifiable offline; the proposal allows `signature == ""` and defers the
   loud failure to the API boundary. Builder may raise instead.
2. Can **more than one** `signature_delta` arrive for one block? Docs say "a
   single" everywhere. The proposal concatenates so one-or-more both work; a
   strict exactly-one check would also satisfy the docs.
3. The `redacted_thinking` streaming shape (start with full `data`, stop, no
   deltas) is confirmed from a third-party bug report + the docs' non-streaming
   JSON + the analogous `fallback`-block shape — **not** an official Anthropic
   streaming trace. README should say so.
4. First-turn block order on Haiku 4.5 with manual thinking + `tool_choice:auto`
   (`[thinking, tool_use]` vs `[text, thinking, tool_use]`) is model behavior;
   the accumulator handles any order given contiguous indices and the loop
   already tolerates leading text, so this only affects which fixture the live
   run happens to match.
5. Not checked live: whether `count_tokens` accepts synthetic `thinking` blocks
   (would let a future cycle preview `clear_thinking_20251015` — see the
   separate backlog item and `knowledge/context-editing.md`).
