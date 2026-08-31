# Prompt caching across a long tool loop

## Question

Where do `cache_control` breakpoints go in a Messages API message list that
*grows every turn* (the kind a hand-written tool loop produces), and how do you
*prove* the saving instead of assuming it?

## Findings

Primary sources read in full on 2026-08-29:

- [Prompt caching](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
  (Claude Platform Docs, undated "current" page)
- [Tool use with prompt caching](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching)
  (Claude Platform Docs, undated "current" page)
- [Claude: How prompt caching actually works](https://www.mager.co/blog/2026-04-29-claude-prompt-caching/)
  (mager.co, dated 2026-04-29)
- [How to Add Prompt Caching to an Anthropic SDK App and Measure the Hit Rate](https://startdebugging.net/2026/04/how-to-add-prompt-caching-to-an-anthropic-sdk-app-and-measure-the-hit-rate/)
  (startdebugging.net, dated 2026-04-29)

Search-snippet level only (could not load full page — HN returned HTTP 429):

- [HN 47034131](https://news.ycombinator.com/item?id=47034131) — comment title
  "The cache gets read at every token generated, not at every turn on the
  conversation" (date not confirmed)
- [HN 46290620](https://news.ycombinator.com/item?id=46290620) — "Prompt caching
  for cheaper LLM tokens" (date not confirmed)
- [spring-ai issue #6261](https://github.com/spring-projects/spring-ai/issues/6261)
  — "Place a `cache_control` breakpoint on the last tool result message during
  tool-calling rounds"

Internal: [[context-editing]], [[anthropic-models]], [[tool-use-loop]].

### How caching works (mechanics that drive placement)

- **Prefix reuse, not memory.** The API reuses an *identical* request prefix
  across `tools` → `system` → `messages`, in that order. A change at one level
  invalidates that level and everything after it. Modifying any tool definition
  invalidates the whole cache. (prompt-caching docs; tool-use-with-prompt-caching
  docs)
- **Breakpoints are explicit markers on content blocks.** You attach
  `"cache_control": {"type": "ephemeral"}` to a block. The write is a hash of the
  entire prefix *up to and including that block*. (prompt-caching docs)
- **Cap: 4 `cache_control` breakpoints per request.** Confirmed on both docs
  pages ("up to 4 cache breakpoints"). Matches the backlog's "4 at last check".
  The `claude-api` skill is **not installed on this machine** (searched `/`;
  only `graphify` is under `~/.claude/skills`), so this is confirmed against
  live docs, not the skill.
- **20-block lookback window.** "The system checks at most 20 positions per
  breakpoint, counting the breakpoint itself as the first." If no matching cache
  entry is found within 20 blocks of a breakpoint, the search stops and you pay a
  fresh write. (prompt-caching docs)
- **Minimum cacheable prefix** (prompt-caching docs table, 2026-08-29):
  Opus 5 / Fable 5 = 512; Opus 4.8 / **Sonnet 5** / Sonnet 4.6 / Sonnet 4.5 =
  **1,024**; **Haiku 4.5 = 4,096**; Haiku 3.5 = 2,048. Shorter prefixes are
  silently processed *without* caching — no error. (Note: startdebugging.net says
  "Sonnet 4.6 requires 2,048"; the official table says 1,024. Third-party number
  looks stale — see open questions. Build a prefix well above both.)
- **TTL.** Default 5 minutes; `{"type": "ephemeral", "ttl": "1h"}` for 1 hour.
  Docs say **no beta header** is required for either now. Lifetime is measured
  from the *start* of the request that writes/reads the entry, and generation
  time counts against it — a slow streamed response eats into the window.
- **Automatic caching.** Passing top-level `cache_control={"type":"ephemeral"}`
  on `messages.create` lets the API place one breakpoint on the last cacheable
  block and move it forward as the conversation grows; it consumes one of the 4
  slots. (prompt-caching docs Python example.) Whether the current SDK surfaces
  this as a real kwarg vs. docs shorthand is unverified — see open questions.
  This proposal uses **explicit** block-level markers, which have been stable for
  well over a year.

### Where the breakpoints go in a growing tool loop

The docs give the rule directly: **put `cache_control` on the last block whose
prefix is identical across requests** — i.e. the last block that will *not*
change on the next turn.

For a hand-written tool loop, each iteration appends an `assistant` message
(with `tool_use`) then a `user` message (with `tool_result`), and then calls the
API again. So:

1. **Tools breakpoint** — `cache_control` on the *last tool* in the `tools`
   array. Caches the entire tool-definitions prefix. Static; set once.
2. **System breakpoint** — `cache_control` on the *last `system` block*.
   `system` must be an **array of blocks**, not a bare string, to attach it.
   Static; set once.
3. **Rolling messages breakpoint** — `cache_control` on the last content block of
   the *previous* turn (the last `tool_result` / assistant text that is now
   frozen). Each turn this marker moves forward to the new frozen tail. On the
   request that sets it, it is a cache *write*; on the next request that same
   block is mid-history and unchanged, so it is a cache *read*.
   ("...put a `cache_control` on the second-to-last turn, so each new iteration
   writes a small delta and reads the much larger prefix" — search summary of
   technspire/futureagi; spring-ai #6261 is the same advice.)
4. **Anchor messages breakpoint (optional, 4th slot)** — as history grows past
   the 20-block lookback window, a single tail breakpoint can no longer "see"
   the head of the conversation to extend it. Keep a second, older breakpoint
   near the head so the head prefix stays cached independently. (prompt-caching
   docs: "As the conversation grows past 20 blocks, add a second breakpoint to
   maintain cache hits." tool-use-with-prompt-caching docs, same.)

Server tool results (web search, code execution) get an **automatic** breakpoint
on the tool result before the next loop iteration — but only if the request
already has at least one `cache_control` marker, and it always uses the 5-minute
TTL. Client tools (our case) get no automatic breakpoint; you place them.

### Proving the saving (`usage` fields)

`response.usage` carries three input counters (prompt-caching docs, verbatim):

- `cache_creation_input_tokens` — tokens *written* to a new cache entry.
- `cache_read_input_tokens` — tokens *read* from cache for this request.
- `input_tokens` — only the tokens *after the last cache breakpoint*, i.e. not
  the whole prompt.
- Identity: `total_input = cache_read + cache_creation + input_tokens`.
- 1-hour TTL adds a nested `cache_creation` object with
  `ephemeral_5m_input_tokens` / `ephemeral_1h_input_tokens`.

So the demo is: **turn 1** writes the prefix (`cache_creation_input_tokens > 0`,
`cache_read_input_tokens == 0`); **turn 2**, sent within 5 minutes with the same
tools + system and a grown message list, reads it back
(`cache_read_input_tokens ≈ turn-1 creation`, `cache_creation_input_tokens`
small — just the delta).

**Pricing multipliers on the base input rate** (prompt-caching docs):
5-minute cache write = **1.25×**; 1-hour cache write = 2×; cache read (either
TTL) = **0.10×**. So a cached token re-read costs 10% of a fresh one; the write
costs a 25% premium once. Break-even for the 5-minute cache is under two reads.

**This cannot be previewed for $0.** `count_tokens` deliberately does not run
caching logic and returns no cache fields ([[context-editing]] records this:
"`count_tokens` deliberately does not use caching logic"). The runner needs one
real (cheap) generation pair.

### Interaction with server-side context editing (the backlog's motivation)

[[context-editing]] already notes: **clearing tool results invalidates the
cached prefix from the edit point forward** — that is the entire reason
`clear_at_least` exists (clear enough that the forced re-write is worth it). So
context editing and prompt caching are in direct tension in a long loop, and
nothing in the lab measures it. This increment builds the *caching* half
(placement policy + saving proof); wiring the two together and measuring the
conflict is a clean follow-up, not this cycle.

### Practitioner cache-killers (from mager.co, startdebugging.net, search)

- A timestamp / "current date & time" / random ID in the system prompt →
  every request is a fresh write, zero reads. (mager.co; a search anecdote cited
  "170,000 tokens written and 0 read every request".)
- Tool reordering — tool order is part of the hash; a framework that sorts tools
  alphabetically or iterates a dict in nondeterministic order breaks the cache
  from the first moved tool. (mager.co; startdebugging.net)
- Model alias drift — `claude-sonnet-5` vs a dated snapshot are different cache
  namespaces. (search summary)
- Any human-in-the-loop pause > 5 minutes evicts the 5-minute entry → full
  re-prefill at 1.25×. (search summary)
- Toggling `tool_choice`, `disable_parallel_tool_use`, images present/absent,
  or thinking params invalidates the messages cache. (tool-use-with-prompt-caching
  docs table)

## Build proposal

### Layer 1 — Intent

A pure function that inserts `cache_control` breakpoints into a growing
tool-loop message list (rolling tail breakpoint + optional head anchor,
respecting the documented cap of 4), plus a minimal two-turn runner that
**proves** the saving by reporting `cache_creation_input_tokens` on turn 1
against `cache_read_input_tokens` on turn 2 of one real, cheap generation pair.

**Out of scope:** automatic caching (top-level `cache_control` kwarg); the 1-hour
TTL; the interaction with server-side context editing (documented above,
measured in a later cycle); streaming; MCP toolsets; more than two
message-array breakpoints; any attempt to preview for $0 (impossible here).

### Layer 2 — Behavioral spec

New example directory: **`examples/prompt-caching-tool-loop/`** (checked
2026-08-29: no such dir on `main`, no open PR, no local/remote branch — the
closest neighbours are `context-editing-preview/` and `server-side-compaction/`).

Functional-core / imperative-shell split, matching `context-editing-preview/`:

**`placement.py` (pure — no `anthropic` import, no I/O, no env, no clock):**

`place_breakpoints(messages, *, budget=MAX_BREAKPOINTS) -> Placement`

- **Inputs:** `messages` is a sequence of mappings each with `role: str` and
  `content: str | list[dict]`. `budget` is how many of the 4 total breakpoints
  are available for the `messages` array (caller subtracts the tools and system
  breakpoints it set itself).
- **Output:** `Placement(messages: list[dict], marker_count: int)` — a **deep
  copy** of the input with `cache_control` dicts inserted, plus the count of
  markers inserted into `messages`.
- **Placement rules:**
  - `budget` is clamped to `[0, MAX_BREAKPOINTS]` (4).
  - `budget <= 0` or `len(messages) == 0` → return the input deep-copied,
    `marker_count == 0`, unchanged.
  - **Rolling marker:** `cache_control` on the last content block of
    `messages[-1]`. Always placed when `budget >= 1` and there is ≥1 message.
  - **Anchor marker:** `cache_control` on the last content block of
    `messages[0]`, placed only when `budget >= 2`, `len(messages) >= 2`, and the
    total content-block count across `messages` exceeds `LOOKBACK_BLOCKS` (20) —
    below that, one rolling marker already chains turn-to-turn and a second is
    wasted.
  - A marked message whose `content` is a bare `str` is normalised to
    `[{"type": "text", "text": <original>, "cache_control": {"type": "ephemeral"}}]`.
    Messages that are **not** marked keep their original `content` object
    untouched.
  - `cache_control` value is exactly `{"type": "ephemeral"}` — a module
    constant `EPHEMERAL`.
- **Invariants:**
  1. `0 <= marker_count <= min(budget, 4)`.
  2. Marker positions are a subset of `{first message's last block, last
     message's last block}`; never a duplicate on the same block.
  3. Input is not mutated (caller can assert deep-equality against a pre-call
     copy).
  4. Idempotent: `place_breakpoints(place_breakpoints(m, budget=b).messages,
     budget=b)` has the same `marker_count` and the same marker positions.
  5. Every inserted marker equals `EPHEMERAL`.
- **Failure modes (raised at the boundary, per Protocol §4):**
  - `ValueError` if `budget < 0`.
  - `TypeError` if a message is not a mapping, lacks `role`/`content`, or
    `content` is neither `str` nor `list`.
  - `ValueError` if a `content` list element is a dict with no `"type"` key.

**`report.py` (pure — saving math + rendering):**

`summarize(turn1, turn2, *, base_usd_per_mtok) -> Saving` and
`render(saving) -> str`.

- `turn1` / `turn2` are small value objects carrying
  `cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens`
  (ints, `>= 0`; `ValueError` otherwise).
- Constants defined once: `CACHE_WRITE_5M_MULTIPLIER = 1.25`,
  `CACHE_READ_MULTIPLIER = 0.10`, `TOKENS_PER_MTOK = 1_000_000`,
  `USD_PRECISION = 6` (mirrors `server-side-compaction/cost.py`).
- Computes, for the 2-turn run:
  - `written = turn1.cache_creation_input_tokens`
  - `read = turn2.cache_read_input_tokens`
  - `read_cost = read * base * CACHE_READ_MULTIPLIER / MTOK`
  - `read_cost_if_uncached = read * base / MTOK`
  - `saved_on_read = read_cost_if_uncached - read_cost`
  - `write_premium = written * base * (CACHE_WRITE_5M_MULTIPLIER - 1) / MTOK`
  - `net_saving = saved_on_read - write_premium`
- `render` returns a newline-joined block (no trailing newline) showing
  written / read / the read fraction `read / max(written, 1)` / the four dollar
  figures.
- Pure; failure mode `ValueError` on any negative field or `base < 0`.

**`main.py` (imperative shell — the only file that imports `anthropic`, reads
`ANTHROPIC_API_KEY`, or writes a stream; SDK import lazy inside `main()` so the
tests need no dependency):**

- `MODEL = "claude-sonnet-5"` in one constant (1,024-token minimum prefix, the
  lowest of the current non-Haiku models; Haiku 4.5 would need a 4× bigger demo
  prefix — see `knowledge/anthropic-models.md`). `BASE_USD_PER_MTOK = 2.0`
  alongside it, with a comment pointing at the models note.
- Builds a **byte-stable** demo context:
  - `SYSTEM`: a list with one text block ~2,500 tokens of fixed lorem/policy
    text (comfortably above every candidate threshold, including the stale
    third-party 2,048 figure), `cache_control` on it.
  - `TOOLS`: two client tools (reuse the `calculator` schema from
    `minimal-agent-loop/` plus one trivial second tool), `cache_control` on the
    last one. No timestamps, no random ids, fixed order.
- Turn 1: `messages = [{"role": "user", "content": <fixed task>}]`; call
  `client.messages.create(model=MODEL, max_tokens=512, system=SYSTEM,
  tools=TOOLS, messages=messages)`.
- Append the assistant response and a **canned** `tool_result` (the demo does not
  need the model to actually be right — it needs a second, longer request with
  the same prefix). Run `place_breakpoints(messages, budget=2)` and send turn 2
  immediately (well inside the 5-minute TTL).
- Adapt each `response.usage` into `report`'s value object in one small function
  (`_usage_of(response) -> report.TurnUsage`), the single place the SDK response
  shape is touched.
- Print `report.render(report.summarize(...))`.
- **Assertions (this is "it works"):** `turn1.cache_creation_input_tokens > 0`
  **and** `turn2.cache_read_input_tokens > 0` **and**
  `turn2.cache_read_input_tokens >= 0.5 * turn1.cache_creation_input_tokens`.
  On failure, raise `SystemExit` with a message pointing at the cache-killer
  list in the README (prefix too short / wrong model / >5 min between turns /
  tools reordered). No key → print one line, `return 0`, make no network call.

**`test_placement.py` + `test_report.py` (offline — no key, no network, stdlib
only, matching the repo convention):**

Assert the layer-2 criteria directly:

- 1-message list, `budget=2` → exactly 1 marker, on that message's last block,
  value `{"type": "ephemeral"}`.
- 6-message tool-loop transcript (fixture), `budget=2`, block count ≤ 20 →
  exactly 1 marker, on `messages[-1]`'s last block (no anchor below lookback).
- Same transcript padded past 20 blocks, `budget=2` → exactly 2 markers, on
  `messages[0]` and `messages[-1]`.
- `budget=1` on the padded transcript → exactly 1 marker (rolling), never the
  anchor.
- `budget=0` → 0 markers, output deep-equals input.
- `budget=9` → clamped, never more than 4 (here 2) markers.
- Input-not-mutated: deep-copy before, assert equal after.
- Idempotency: apply twice, identical marker count and positions.
- String→block normalisation on a marked message; unmarked message's `content`
  is the same object.
- `budget=-1` → `ValueError`; message without `content` → `TypeError`; content
  block dict without `"type"` → `ValueError`.
- `report.summarize`: `written=2000, read=2000, base=2.0` →
  `saved_on_read == round(2000 * 2.0 / 1e6 * 0.9, 6)`,
  `write_premium == round(2000 * 2.0 / 1e6 * 0.25, 6)`,
  `net_saving == saved_on_read - write_premium` (> 0); `render` output contains
  both figures and is non-empty. Negative field → `ValueError`.

**`requirements.txt`:** `anthropic==1.2.0` (latest on PyPI, released
2026-08-27 — the `0.121.0` pin in `context-editing-preview/` is now two majors
behind; the builder should confirm `messages.create` still returns
`usage.cache_creation_input_tokens` / `cache_read_input_tokens` unchanged, which
the docs example dated 2026-08-29 still shows).

**`README.md`:** one-sentence intent; the four-breakpoint placement table
(tools / system / rolling / anchor) with the 20-block-lookback rationale; the
`usage`-field identity and the 1.25× / 0.10× multipliers; an explicit
"**costs one real generation pair — a fraction of a cent on `claude-sonnet-5`;
it cannot be previewed for $0 because `count_tokens` reports no cache fields**"
note in the style of `mcp-connect-claude-code/README.md` and
`skill-script-execution/README.md`; the cache-killer checklist; a pasted
transcript of a real run; and a "Related" pointer to `context-editing-preview/`
noting the unmeasured tension.

### Layer 3 — Interfaces (stubs, no bodies)

```python
# placement.py
from __future__ import annotations
import dataclasses
from collections.abc import Mapping, Sequence

EPHEMERAL: dict[str, str] = {"type": "ephemeral"}
MAX_BREAKPOINTS = 4          # documented cap (prompt-caching docs, 2026-08-29)
LOOKBACK_BLOCKS = 20         # documented lookback window per breakpoint

@dataclasses.dataclass(frozen=True)
class Placement:
    messages: list[dict]     # deep copy of input, cache_control inserted
    marker_count: int        # markers inserted into the messages array (0..min(budget,4))

def place_breakpoints(
    messages: Sequence[Mapping[str, object]],
    *,
    budget: int = MAX_BREAKPOINTS,
) -> Placement: ...

def _normalize_content(content: object) -> list[dict]: ...   # str -> [text block]; list -> copy
def _mark_last_block(blocks: list[dict]) -> None: ...        # attach EPHEMERAL to blocks[-1]
def _count_blocks(messages: Sequence[Mapping[str, object]]) -> int: ...
```

```python
# report.py
from __future__ import annotations
import dataclasses

CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10
TOKENS_PER_MTOK = 1_000_000
USD_PRECISION = 6

@dataclasses.dataclass(frozen=True)
class TurnUsage:
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    input_tokens: int
    def __post_init__(self) -> None: ...   # ValueError on any negative

@dataclasses.dataclass(frozen=True)
class Saving:
    written: int
    read: int
    read_fraction: float
    read_cost_usd: float
    read_cost_if_uncached_usd: float
    saved_on_read_usd: float
    write_premium_usd: float
    net_saving_usd: float

def summarize(turn1: TurnUsage, turn2: TurnUsage, *, base_usd_per_mtok: float) -> Saving: ...
def render(saving: Saving) -> str: ...   # newline-joined, no trailing newline
```

```python
# main.py
MODEL = "claude-sonnet-5"          # see knowledge/anthropic-models.md
BASE_USD_PER_MTOK = 2.0            # Sonnet 5 base input rate, 2026-08-29
API_KEY_ENV = "ANTHROPIC_API_KEY"
EXIT_OK, EXIT_NO_KEY, EXIT_NO_CACHE_HIT = 0, 1, 2

def build_system() -> list[dict]: ...            # one ~2500-token text block + EPHEMERAL
def build_tools() -> list[dict]: ...             # 2 tools, EPHEMERAL on the last
def _usage_of(response: object) -> "report.TurnUsage": ...
def run(client, *, model: str, base_rate: float) -> "report.Saving": ...  # 2 create calls + asserts
def main() -> int: ...
```

### "It works" (acceptance)

1. `python test_placement.py` and `python test_report.py` pass with no key, no
   network, no third-party import.
2. `python main.py` with no key prints one line and exits 0 without a network
   call.
3. `python main.py` with a key makes exactly two `messages.create` calls and
   prints a report where turn 2's `cache_read_input_tokens` is > 0 and at least
   half of turn 1's `cache_creation_input_tokens`; process exits 0. If the cache
   did not hit, it exits `EXIT_NO_CACHE_HIT` with the cache-killer pointer.
4. The README's pasted transcript matches what `python main.py` prints (allowing
   for the token counts, which the README shows as `<n>` placeholders or notes
   as run-dependent — see `doc-transcript-drift` knowledge note).

## Open questions

- **Exact minimum-prefix for Sonnet 5.** Official docs table says 1,024;
  startdebugging.net (2026-04-29) says 2,048 for Sonnet 4.6. Building a
  ~2,500-token prefix sidesteps the disagreement, but the note should not claim
  1,024 as certain.
- **Top-level `cache_control` kwarg.** The docs Python example passes
  `cache_control={"type": "ephemeral"}` directly to `messages.create`
  ("automatic caching"). Not verified against `anthropic==1.2.0`'s actual
  signature. The proposal avoids it (explicit block markers only), so this is a
  README footnote, not a blocker.
- **SDK v1.2.0 response shape.** `context-editing-preview/` pins `0.121.0`;
  v1.2.0 (2026-08-27) is two majors newer. The docs example dated 2026-08-29
  still shows `response.usage.cache_creation_input_tokens` /
  `cache_read_input_tokens`, so the field names are almost certainly stable, but
  the builder must confirm on first run.
- **`claude-api` skill not available.** Not installed on this machine (only
  `graphify` under `~/.claude/skills`). Model ids, prices, and the breakpoint
  cap here are from `knowledge/anthropic-models.md` and live docs read
  2026-08-29, not the skill. If the skill exists in the builder's environment,
  re-check `MODEL`, `BASE_USD_PER_MTOK`, and `MAX_BREAKPOINTS` against it.
- **Anchor-marker precision.** The proposal anchors on `messages[0]` when the
  block count exceeds 20. A more faithful "keep the *previous* rolling position
  as the anchor" needs one integer of caller state between turns; deferred as a
  documented extension so the cycle stays one day.
- **Does `str` vs single-text-block `content` change the cache hash?** Assumed
  equivalent (same tokens). If not, normalising only marked messages (as
  specified) is the safe choice anyway.
