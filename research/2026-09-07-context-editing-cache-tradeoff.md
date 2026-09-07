# Measuring the context-editing vs prompt-caching trade

Date: 2026-09-07

## Question

When a long tool loop runs `clear_tool_uses_20250919` *and* prompt caching at
the same time, the clear invalidates the cached message prefix and forces a
fresh cache write. What is the net — tokens the clear removes against tokens
re-billed as a cold write — and how many post-clear turns does it take for the
re-cached prefix to pay that write back?

## Findings

### Primary sources (read 2026-09-07)

- [Context editing — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)
  (undated "current" page)
- [Manage tool context — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context)
  (undated "current" page)
- [Context engineering: memory, compaction, and tool clearing — Claude Cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)
  (**dated 2026-03-20**; Anthropic-authored; `anthropic` 0.84.0, `claude-sonnet-4-6`)
- [Prompt caching — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
  (undated "current" page; re-confirmed against the 2026-08-29 read in
  `research/2026-08-29-prompt-caching-tool-loop.md`)
- [anthropic on PyPI — JSON metadata](https://pypi.org/pypi/anthropic/json)
  (fetched 2026-09-07: latest `1.4.0`; a WebSearch snippet said `1.3.0` /
  2026-09-01 — treat the newest `1.x` as the pin and confirm at build time)

Internal, already in the lab: [[context-editing]], [[prompt-caching]],
`examples/context-editing-preview/`, `examples/prompt-caching-tool-loop/`,
`research/2026-08-11-context-editing-preview.md`,
`research/2026-08-29-prompt-caching-tool-loop.md`.

### 1. The tension is real and Anthropic states it plainly

The context-editing docs, verbatim:

> **Tool result clearing:** Invalidates cached prompt prefixes when content is
> cleared. To account for this, clear enough tokens to make the cache
> invalidation worthwhile. Use the `clear_at_least` parameter to ensure a
> minimum number of tokens is cleared each time. **You'll incur cache write
> costs each time content is cleared, but subsequent requests can reuse the
> newly cached prefix.**

The 2026-03-20 cookbook repeats it as the "one trade-off to understand" about
clearing, and adds concrete clearing magnitudes from a research agent — a
message list going **~128,740 → ~43,060 tokens (67% reduction)** for
`keep=1`, and firings that free **~163,817 tokens** each — but then, in its own
words, gives **no** "statement or calculation about how many turns it takes for
the re-cached prefix to pay back the cache-write cost." That gap is exactly what
this increment fills.

### 2. What is invalidated, and what is not

Prefix reuse runs `tools` → `system` → `messages`, and "a change at one level
invalidates that level and everything after it" (prompt-caching docs). A
`clear_tool_uses` edit changes only the `messages` array (it swaps old
`tool_result` bodies for placeholder text). So on the turn the clear fires:

- the **`tools` and `system`** breakpoints still hit — `cache_read_input_tokens`
  should be roughly their combined size;
- every **`messages`-level** breakpoint below the first cleared block misses —
  `cache_creation_input_tokens` should be roughly the surviving message prefix
  that now has to be re-hashed and re-written.

This split is measurable from one response's `usage` and is the core of the
report.

### 3. The free half and the billed half (the #25 / #35 asymmetry)

- **Tokens the clear removes** can be previewed for **$0**. `count_tokens`
  accepts `context_management` (`betas=["context-management-2025-06-27"]`) and
  returns `original_input_tokens` and post-edit `input_tokens`; the difference
  is what the clear removed. `examples/context-editing-preview/` already ships
  and health-checks this exact path. Note the asymmetry recorded in
  [[context-editing]]: `count_tokens` returns **no** `applied_edits`, so the
  free half must subtract, not read a field.
- **The cache-write cost that clear forces** cannot be previewed —
  `count_tokens` runs no caching logic and reports no cache fields
  ([[prompt-caching]]). That number needs one real generation, same constraint
  `examples/prompt-caching-tool-loop/` hit. So this example, like that one,
  spends roughly one-to-two cents on `claude-sonnet-5` when a key is present and
  says so loudly.

The billed generation response *does* carry
`context_management.applied_edits[0].cleared_input_tokens` and
`cleared_tool_uses` (docs confirm the shape). Cross-checking that against the
free preview's subtraction is a cheap consistency assertion
(`examples/context-editing-preview/preview.py` already has the
`InconsistentCount` precedent).

### 4. The payback model

Let `removed` = tokens the clear deletes from the message list, `rewritten` =
`cache_creation_input_tokens` on the clearing turn (the message prefix re-write
the invalidation forces). Using the documented 5-minute multipliers on the base
input rate — write `1.25×`, read `0.10×` ([[prompt-caching]]):

- **One-time cost of the invalidation** ≈ `rewritten × (1.25 − 0.10) × base` =
  `rewritten × 1.15 × base` — the premium of a cold write over what re-reading
  those same tokens would have cost.
- **Saving per later turn** ≈ `removed × 0.10 × base` — every subsequent turn no
  longer re-reads the removed tokens at the cached-read rate. (Conservative: a
  transcript that keeps growing would also inflate future writes, which this
  ignores. Assumes the cleared region sat *below* a breakpoint and was being
  re-read every turn — true for deep history, not for the live tail.)
- **Payback** = one-time cost / per-turn saving = `11.5 × rewritten / removed`
  turns.

So `clear_at_least` is the knob that keeps `removed ≫ rewritten` and payback
near 1–2 turns; set it too low (clear barely more than you re-write) and payback
stretches to ~10 turns. That is the docs' qualitative warning turned into a
number — and the number nobody has published.

### 5. Placement: the head anchor is wrong once clearing is on

`examples/prompt-caching-tool-loop/placement.py` places two `messages`
breakpoints: a **rolling** marker on the frozen tail, and — once history exceeds
the 20-block lookback — an **anchor** on `messages[0]` so the head stays cached
when the tail marker can't see that far back.

With `clear_tool_uses` active, `messages[0]` (the opening user turn) survives,
but everything between it and the `keep` boundary becomes placeholder text on
every clearing turn. An anchor at the head therefore spans a region that keeps
changing — a guaranteed miss each clear, and a wasted slot. The fix is a pure,
deterministic rule the client can compute from its own list: **put the anchor on
the last block of the first message that survives clearing** (the start of the
`keep`-th-from-last `tool_use` pair). That region is stable turn-over-turn once
the clear has fired, so the post-clear head re-caches once and reads back after.
This "clearing-aware placement" is the one genuinely new piece of logic in the
increment; everything else is composed from the two parent examples.

### 6. Practitioner reception

Thin and mostly stale. The launch thread
[HN 45479006 "Managing context on the Claude Developer Platform"](https://news.ycombinator.com/item?id=45479006)
(~Oct 2025, **possibly stale**) had "cache invalidation is the hidden cost" as a
recurring complaint, with no one offering a cheap way to quantify it — the same
observation `research/2026-08-11-context-editing-preview.md` recorded. A few
third-party pages (e.g. Agno's docs, one SEO blog) paraphrase context editing as
running "after prompt cache lookup … without destroying prompt cache prefixes,"
which directly contradicts Anthropic's own docs and the 2026-03-20 cookbook.
The increment settles it empirically: if the clearing turn shows
`cache_creation_input_tokens ≈ 0` and a full `cache_read`, the third-party claim
holds; every Anthropic source and prior lab note predicts the opposite.
(`mcp__hn-search__*` was not in this cycle's tool set; HN here is
WebSearch-sourced, not an Algolia query.)

## Build proposal

### Layer 1 — Intent

Ship `examples/context-editing-cache-tradeoff/`: over one synthetic growing tool
loop, compose a `clear_tool_uses_20250919` edit with clearing-aware
`cache_control` placement, and report — from one cheap generation pair plus a
$0 `count_tokens` preview — the net of tokens the clear removes against tokens
re-billed as a cold cache write, expressed as **payback turns**.

**Out of scope:** running a real agent (the model never actually executes a
tool; canned `tool_result`s); `clear_thinking_20251015` and `compact_20260112`;
the 1-hour TTL; streaming; more than two `messages`-level breakpoints;
client-side pruning; any attempt to preview the cache-write cost for $0
(impossible — see finding 3). Re-implementing the parents' full validation
surface — this example lifts only the tested helpers it needs.

**Name check (2026-09-07):** no `examples/context-editing-cache-tradeoff/` on
`main`; `ls examples/` shows only `context-editing-preview/` and
`prompt-caching-tool-loop/` as neighbours; `git branch -a` has no matching
branch; open PRs are #36 and #39 (both pipeline plumbing, unrelated).

### Layer 2 — Behavioral spec

Functional-core / imperative-shell split, matching both parents.

**`compose.py` (pure — no `anthropic`, no I/O, no env, no clock):**

- `clearing_boundary(messages, *, keep_tool_uses: int) -> int`
  - **Input:** a validated message list (mappings with `role` + `content`);
    `keep_tool_uses >= 0`.
  - **Output:** index of the first message that survives a `clear_tool_uses`
    edit with `keep = keep_tool_uses` — i.e. the message holding the
    `keep_tool_uses`-th-from-last `tool_use` block.
  - **Rules:** `0` when there are `<= keep_tool_uses` tool uses (nothing would be
    cleared); `len(messages)` when `keep_tool_uses == 0` (everything cleared).
  - **Failure:** `ValueError` if `keep_tool_uses < 0`; `TypeError` for a
    message that is not a mapping or lacks `role`/`content` (reuse the parent's
    `_validated_copy` shape).

- `place_breakpoints_for_clearing(messages, *, budget: int, keep_tool_uses: int) -> Placement`
  - Same rolling-tail marker as `prompt-caching-tool-loop/placement.py`
    (`EPHEMERAL` on the last block of `messages[-1]`, when `budget >= 1`).
  - Anchor marker (`budget >= 2` **and** total block count `> LOOKBACK_BLOCKS`):
    on the last block of `messages[clearing_boundary(messages, keep_tool_uses=…)]`
    — **not** `messages[0]`. When `clearing_boundary` returns `0` (nothing
    cleared yet) it degrades to the parent's head-anchor behaviour; when it
    returns `len(messages)` there is no distinct survivor to anchor and the
    anchor is skipped.
  - Deep-copies the input, strips any pre-existing `cache_control`, idempotent
    over a list that grows between calls — the parent's contract, unchanged.
  - **Output:** `Placement(messages: list[dict], marker_count: int)` with
    `0 <= marker_count <= min(budget, MAX_BREAKPOINTS)`.

- `clearing_config(*, keep_tool_uses: int, trigger_tool_uses: int) -> dict`
  - Returns `{"edits": [{"type": "clear_tool_uses_20250919", "trigger":
    {"type": "tool_uses", "value": trigger_tool_uses}, "keep": {"type":
    "tool_uses", "value": keep_tool_uses}}]}`. A local minimal serialiser — the
    `tool_uses` trigger form only, since the `input_tokens` default (100k) never
    fires on a demo transcript. `ValueError` if either value `< 1`
    (`keep_tool_uses == 0` is legal for the *boundary* helper but not a policy
    this demo sends).

**`payback.py` (pure — the net math + one renderer):**

- `TurnUsage` — three non-negative int counters
  (`cache_creation_input_tokens`, `cache_read_input_tokens`, `input_tokens`);
  `TypeError` on non-int, `ValueError` on negative. (Same as the parent's.)
- `Tradeoff(removed: int, rewritten: int, base_usd_per_mtok: float)` — frozen;
  every figure below derived, none stored:
  - `invalidation_cost_usd` = `rewritten × (WRITE_5M_MULTIPLIER −
    READ_MULTIPLIER) × base / MTOK`
  - `saving_per_future_turn_usd` = `removed × READ_MULTIPLIER × base / MTOK`
  - `payback_turns` — `invalidation_cost_usd / saving_per_future_turn_usd` when
    the denominator `> 0`, else `math.inf` (a clear that removed nothing never
    pays back — not a `ZeroDivisionError`). Equals `11.5 × rewritten / removed`.
  - `ValueError`/`TypeError` posture identical to `report.py` in the parent.
- `summarize(clearing_turn: TurnUsage, *, removed: int, base_usd_per_mtok: float) -> Tradeoff`
  — `rewritten = clearing_turn.cache_creation_input_tokens`.
- `render(t: Tradeoff) -> str` — newline-joined, no trailing newline; shows
  `removed`, `rewritten`, the two dollar figures, and `payback_turns`.

**`transcript.py` (pure):** the synthetic growing transcript. Reuse
`examples/context-editing-preview/transcript.py` almost verbatim
(`build_transcript(rounds, result_chars)` → `1 + 2·rounds` messages, unique
`tool_use_id`s, fixed-size result bodies, deterministic filler). The cleared
region must sit *below* the rolling breakpoint — it does, it is deep history.

**`main.py` (imperative shell — only file importing `anthropic`, reading
`ANTHROPIC_API_KEY`, or writing a stream; SDK import lazy in `main()`):**

- Constants in one place: `MODEL = "claude-sonnet-5"`,
  `BASE_USD_PER_MTOK = 2.0` (see `knowledge/anthropic-models.md`),
  `BETA = "context-management-2025-06-27"`, `ROUNDS = 12`, `RESULT_CHARS = 1600`,
  `KEEP = 3`, `TRIGGER = 5`, `STATIC_BREAKPOINTS = 2` (tools + system),
  `MESSAGES_BUDGET = MAX_BREAKPOINTS - STATIC_BREAKPOINTS`, `MAX_TOKENS = 512`,
  `MIN_READ_FRACTION = 0.5`, `TOLERANCE = 0.02`.
- Small stable `system` (one text block, a few hundred tokens) and two client
  tools, `cache_control` on the last of each — enough that `tools + system +
  message prefix` clears Sonnet 5's 1,024-token floor with huge margin.
- **Free preview (runs whenever a key is present; $0):** `count_tokens` the full
  transcript twice — plain, then with `context_management=clearing_config(...)`
  and `betas=[BETA]` — `removed_preview = original_input_tokens − input_tokens`.
- **Billed proof (key present ⇒ ~1–2 cents; no key ⇒ print one line, exit 0, no
  network — the `prompt-caching-tool-loop` contract exactly):**
  - Turn A (clearing turn): `messages.create` with the full transcript, both
    `context_management` and `place_breakpoints_for_clearing(...)` markers, plus
    the static `system`/`tools` markers. Capture `usage` and
    `context_management.applied_edits[0].cleared_input_tokens` /
    `cleared_tool_uses`.
  - Turn B: append one canned assistant `tool_use` + user `tool_result`,
    re-place breakpoints, resend with the same `context_management`. Capture
    `usage.cache_read_input_tokens`.
  - Adapt each `response.usage` in one `_usage_of(response) -> payback.TurnUsage`
    — the single place the SDK response shape is touched (`AttributeError`
    loudly on a renamed counter, per the parent).
- Print: the preview line; a small table of turn A's counters (tools+system
  `read` vs message-prefix `creation`); `applied_edits` figures; turn B's
  `read`; then `payback.render(payback.summarize(turnA, removed=cleared_input_tokens, base=BASE))`.

**Invariants**

1. `compose` never mutates its input; output is a deep copy; re-running yields
   the same marker positions and count over the same or a grown list.
2. `0 <= marker_count <= min(budget, MAX_BREAKPOINTS)`; anchor position is
   `clearing_boundary(...)` (or `0` in the degrade case), never both.
3. `clearing_config(...)` is dict-equal to the documented edit shape.
4. Every `payback` figure is derived from `(removed, rewritten, base)`; no
   negative saving; `removed == 0` ⇒ `payback_turns == inf`, no exception.
5. The core imports no `anthropic`, reads no env; the shell's SDK import is lazy.

**Failure modes**

- No `ANTHROPIC_API_KEY`: one line to stderr, `return 0`, no network call.
- Clearing turn did not invalidate (`cache_creation_input_tokens == 0`), or
  `applied_edits` absent, or turn B read back `< MIN_READ_FRACTION` of turn A's
  write: exit non-zero (`EXIT_NO_INVALIDATION` / `EXIT_NO_RECACHE`) with a
  diagnostic pointing at the README's cache-killer list and the third-party
  "no invalidation" claim this would corroborate.
- Free-preview `removed_preview` and billed `cleared_input_tokens` diverge by
  more than `TOLERANCE`: report loudly (do not silently pick one).
- Any `anthropic.APIError` (unsupported model, rejected beta, rate limit)
  propagates with a traceback — never a zero-net report.

**Acceptance criteria ("it works")**

1. `python test_compose.py` and `python test_payback.py` pass with **no key, no
   network, no third-party import**.
2. `python main.py` with no key prints one line and exits 0 with no network call.
3. `python main.py` with a key: prints the $0 preview (`removed_preview > 0`),
   makes exactly three billed calls (one `count_tokens` is free; turns A and B
   are `messages.create`), and prints a report where turn A's
   `cache_creation_input_tokens > 0` **and** turn A's `cache_read_input_tokens`
   is within ~20% of the tools+system size (they survived) **and**
   `applied_edits[0].cleared_input_tokens > 0` **and** turn B's
   `cache_read_input_tokens >= MIN_READ_FRACTION ×` turn A's creation. Exits 0.
4. `removed_preview` and `applied_edits[0].cleared_input_tokens` agree within
   `TOLERANCE`; otherwise the run says so and exits non-zero.
5. `payback_turns` in the printed report equals
   `round(11.5 × rewritten / removed, 3)` for the run's own numbers.
6. Offline tests assert the layer-2 criteria directly, including:
   - `clearing_boundary`: 10 pairs (`messages` len 21), `keep=3` → `15`;
     `keep>=10` → `0`; `keep=0` → `21`.
   - `place_breakpoints_for_clearing`: transcript padded past `LOOKBACK_BLOCKS`,
     `budget=2`, `keep=3` → exactly 2 markers, anchor on
     `messages[clearing_boundary]`'s last block (asserted ≠ `messages[0]`);
     block count `<= LOOKBACK_BLOCKS` → 1 marker (rolling only); `keep` large
     enough that boundary is `0` → anchor falls back to `messages[0]`.
   - deep-copy / no-mutation / idempotence (apply twice).
   - `clearing_config(keep_tool_uses=3, trigger_tool_uses=5)` dict-equals the
     documented shape; `keep_tool_uses=0` → `ValueError`.
   - `payback`: `removed=160_000, rewritten=20_000, base=2.0` →
     `payback_turns == round(11.5*20_000/160_000, 3)` (≈ `1.438`);
     `removed=12_000, rewritten=10_000` → ≈ `9.583`; `removed=0` → `inf`;
     negative field → `ValueError`; `render` output non-empty, contains the
     payback figure, no trailing newline.
7. `README.md` carries: one-sentence intent; the `usage`-counter table for the
   clearing turn (tools/system read vs messages re-write); the payback formula
   `payback_turns ≈ 11.5 × rewritten / removed` with the two worked examples and
   the `clear_at_least` lesson; the clearing-aware placement rationale; an
   explicit "**costs one real generation pair — a fraction of a cent on
   `claude-sonnet-5`; the tokens the clear removes preview for $0 but the
   cache-write cost the clear forces does not**" note in the style of
   `examples/mcp-connect-claude-code/README.md`; a pasted real-run transcript
   with run-dependent counts marked (`knowledge/doc-transcript-drift.md`); and a
   "Related" section pointing at `examples/context-editing-preview/`,
   `examples/prompt-caching-tool-loop/`, `knowledge/context-editing.md`,
   `knowledge/prompt-caching.md`.
8. `requirements.txt` pins the newest `anthropic` 1.x (`==1.4.0` at research
   time; builder confirms `usage.cache_creation_input_tokens` /
   `cache_read_input_tokens` and `context_management.applied_edits[*].cleared_input_tokens`
   on first run).

### Layer 3 — Interfaces (stubs, no bodies)

```python
# compose.py
from __future__ import annotations
import dataclasses
from collections.abc import Mapping, Sequence

EPHEMERAL: dict[str, str] = {"type": "ephemeral"}
MAX_BREAKPOINTS = 4
LOOKBACK_BLOCKS = 20
STRATEGY = "clear_tool_uses_20250919"
BETA = "context-management-2025-06-27"

@dataclasses.dataclass(frozen=True)
class Placement:
    messages: list[dict]
    marker_count: int

def clearing_boundary(
    messages: Sequence[Mapping[str, object]], *, keep_tool_uses: int
) -> int: ...

def place_breakpoints_for_clearing(
    messages: Sequence[Mapping[str, object]], *, budget: int, keep_tool_uses: int
) -> Placement: ...

def clearing_config(*, keep_tool_uses: int, trigger_tool_uses: int) -> dict: ...
```

```python
# payback.py
from __future__ import annotations
import dataclasses, math

CACHE_WRITE_5M_MULTIPLIER = 1.25
CACHE_READ_MULTIPLIER = 0.10
TOKENS_PER_MTOK = 1_000_000
USD_PRECISION = 6
TURNS_PRECISION = 3

@dataclasses.dataclass(frozen=True)
class TurnUsage:
    cache_creation_input_tokens: int
    cache_read_input_tokens: int
    input_tokens: int
    def __post_init__(self) -> None: ...   # TypeError non-int, ValueError negative

@dataclasses.dataclass(frozen=True)
class Tradeoff:
    removed: int
    rewritten: int
    base_usd_per_mtok: float
    # derived: invalidation_cost_usd, saving_per_future_turn_usd, payback_turns

def summarize(
    clearing_turn: TurnUsage, *, removed: int, base_usd_per_mtok: float
) -> Tradeoff: ...

def render(t: Tradeoff) -> str: ...
```

```python
# main.py
MODEL = "claude-sonnet-5"
BASE_USD_PER_MTOK = 2.0
API_KEY_ENV = "ANTHROPIC_API_KEY"
ROUNDS, RESULT_CHARS, KEEP, TRIGGER = 12, 1600, 3, 5
EXIT_OK, EXIT_NO_KEY = 0, 0            # no-key is a $0 skip, like prompt-caching-tool-loop
EXIT_NO_INVALIDATION, EXIT_NO_RECACHE, EXIT_PREVIEW_MISMATCH = 2, 3, 4

def build_system() -> list[dict]: ...
def build_tools() -> list[dict]: ...
def _usage_of(response: object) -> "payback.TurnUsage": ...
def _cleared_input_tokens(response: object) -> int: ...   # from applied_edits; loud if absent
def run(client, *, model: str, base_rate: float) -> str: ...   # preview + 2 create calls + report
def main() -> int: ...
```

Files: `README.md`, `compose.py`, `payback.py`, `transcript.py`, `main.py`,
`test_compose.py`, `test_payback.py`, `requirements.txt`.

## Open questions

- **Does the clearing turn really keep the `tools`/`system` cache while losing
  only `messages`?** Docs imply yes (`messages` is the last prefix level; a
  change there invalidates nothing above it). If the run shows tools/system
  `read` collapsing too, the report's attribution of `rewritten` needs
  splitting. Builder confirms on first run.
- **Is `applied_edits` present on the billed response but absent on
  `count_tokens`?** `knowledge/context-editing.md` and the shipped
  `context-editing-preview/` both depend on this; reconfirm.
- **Payback model assumes the cleared region was cached and re-read every turn
  at `0.10×`.** If those tokens were above the rolling breakpoint (live tail,
  uncached), the per-turn saving is at the full input rate and payback is ~10×
  faster. The synthetic transcript puts the cleared region in deep history to
  match the "old results" framing; the README states the assumption.
- **Third-party "clearing happens after cache lookup, without destroying the
  prefix" claim.** Contradicted by every Anthropic source; the run's turn-A
  counters settle it. Record the result in `knowledge/context-editing.md`.
- **SDK version.** PyPI JSON (2026-09-07) says latest `anthropic` `1.4.0`; a
  search snippet said `1.3.0` / 2026-09-01. Pin the newest `1.x`, confirm the
  three `usage` counters and `applied_edits[*].cleared_input_tokens` on first
  run.
- **`claude-api` skill not installed here** (only `graphify` under
  `~/.claude/skills/`). `MODEL`, `BASE_USD_PER_MTOK`, `BETA`, and the `1.25×` /
  `0.10×` multipliers come from `knowledge/anthropic-models.md`,
  `knowledge/context-editing.md`, `knowledge/prompt-caching.md`, and docs read
  2026-09-07 — not the skill. If it is present in the builder's env, re-check
  all four against it.

## Sources

- [Context editing — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/context-editing) (fetched 2026-09-07)
- [Manage tool context — Claude Platform Docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/manage-tool-context) (fetched 2026-09-07)
- [Context engineering: memory, compaction, and tool clearing — Claude Cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools) (dated 2026-03-20; fetched 2026-09-07)
- [Prompt caching — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching) (re-confirmed 2026-09-07)
- [anthropic — PyPI JSON metadata](https://pypi.org/pypi/anthropic/json) (fetched 2026-09-07: latest 1.4.0)
- [HN 45479006 — "Managing context on the Claude Developer Platform"](https://news.ycombinator.com/item?id=45479006) (~Oct 2025, **possibly stale**)
- Internal: `research/2026-08-11-context-editing-preview.md`,
  `research/2026-08-29-prompt-caching-tool-loop.md`,
  `knowledge/context-editing.md`, `knowledge/prompt-caching.md`,
  `examples/context-editing-preview/`, `examples/prompt-caching-tool-loop/`
