# Context editing for long tool loops, and how to measure it for $0

Date: 2026-08-11 (cycle 2 of 2)

## Question

When an agent loop's transcript grows past the context budget, what does
Anthropic's server-side context editing (`clear_tool_uses_20250919`) actually
do, and can a builder verify its effect on a real transcript without paying for
a single generation?

## Backlog note: why this topic

`BACKLOG.md` had **zero** unclaimed `- [ ]` items when this cycle started. The
two "health-check findings" still reading `[building]` were both shipped in
[PR #24](https://github.com/chunkychonker/agentlab/pull/24) (merged
2026-08-11T13:29Z — the diff touches `examples/typed-tool-registry/README.md`
and `examples/tool-error-policy/{README,agent,test_agent}.py`), so re-picking
one would be pure churn of the kind PRs #5/#6/#11 already taught us to avoid.
`gh pr list --state open` is empty; the only non-`main` branches are the two
already-merged cycle branches.

Per `PIPELINE.md`, replenishment runs *after the last cycle*, so cycle 2 of 2
structurally finds the backlog drained on any night where cycle 1 takes the
last item. I therefore filed one new item and marked it `[researching]`, and
reconciled the two stale `[building]` markers to `[done #24]` — both changes
are in `BACKLOG.md`. **Left for the maintainer / a future cycle:** this
ordering is a real pipeline gap. Replenishing *before* the loop when the
backlog is short, rather than only after it, would remove the researcher's need
to file its own work. I did not change `run.sh`.

## Findings

### 1. Two distinct server-side strategies exist, and they are not the same thing

The Messages API takes a `context_management` parameter whose `edits` list
accepts three strategy types
([Context editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing),
fetched 2026-08-11):

| Strategy | Beta header | What it does |
|---|---|---|
| `clear_tool_uses_20250919` | `context-management-2025-06-27` | **Prunes**: drops old `tool_result` bodies, replacing each with placeholder text |
| `clear_thinking_20251015` | `context-management-2025-06-27` | Prunes old `thinking` blocks |
| `compact_20260112` | `compact-2026-01-12` | **Summarizes**: server-side compaction ([Compaction docs](https://platform.claude.com/docs/en/build-with-claude/compaction)) |

Anthropic's own `claude-api` skill states the distinction plainly
([`skills/claude-api/shared/tool-use-concepts.md`](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/tool-use-concepts.md),
fetched 2026-08-11): context editing "prunes — the cleared content is removed,
not replaced", and compaction is "separate from `compact_20260112`, which
requires different beta `compact-2026-01-12`".

**This note scopes to `clear_tool_uses_20250919` only.** Compaction is a
bigger, separate topic (it changes the response shape with compaction blocks
and a `pause_after_compaction` flow) and deserves its own cycle.

### 2. The exact parameter shape, verified against SDK source (not docs prose)

I installed `anthropic==0.121.0` (latest on PyPI as of today) into a throwaway
venv and read the generated types rather than trusting the docs table:

`types/beta/beta_clear_tool_uses_20250919_edit_param.py`:

```
type            Required  Literal["clear_tool_uses_20250919"]
trigger                   {"type": "input_tokens"|"tool_uses", "value": int}
keep                      {"type": "tool_uses", "value": int}
clear_at_least            {"type": "input_tokens", "value": int} | None
exclude_tools             Sequence[str] | None      # tool names never cleared
clear_tool_inputs         bool | Sequence[str] | None
```

Two things the docs table gets subtly wrong or under-specifies, both confirmed
in source:

- `clear_tool_inputs` is **not** just a `bool`. The SDK type is
  `Union[bool, SequenceNotStr[str], None]` — "Whether to clear all tool inputs
  (bool) or specific tool inputs to clear (list)".
- `trigger` is a union of two shapes. `{"type": "tool_uses", "value": N}` fires
  on a *count of tool uses*, not tokens — much easier to trigger deterministically
  in a demo than the `input_tokens` default of 100k.

The beta string is a real literal in the SDK's `AnthropicBetaParam` union
(`types/anthropic_beta_param.py:29` → `"context-management-2025-06-27"`), so a
typo is a type error, not a silent no-op.

The wrapper is trivial: `BetaContextManagementConfigParam` is just
`{"edits": [...]}`.

### 3. The measurement trick: `count_tokens` supports it, and `count_tokens` is free

This is the finding that makes a same-day, honest example possible.

The token counting docs state, verbatim
([Token counting](https://platform.claude.com/docs/en/build-with-claude/token-counting),
fetched 2026-08-11):

> Token counting is **free to use** but subject to requests per minute rate
> limits based on your usage tier.

and

> Token counting and message creation have separate and independent rate limits.
> Usage of one does not count against the limits of the other.

And the context-editing docs state:

> The token counting endpoint supports context management, allowing you to
> preview how many tokens your prompt will use after context editing is applied.

with the response shape:

```json
{ "input_tokens": 25000, "context_management": { "original_input_tokens": 70000 } }
```

Verified in the SDK: `resources/beta/messages/messages.py:1834` defines
`count_tokens` and it accepts `context_management:
Optional[BetaContextManagementConfigParam]`; the return type
`BetaMessageTokensCount` has exactly two fields — `input_tokens: int` and
`context_management: Optional[BetaCountTokensContextManagementResponse]`.

**The asymmetry that will bite the builder:** the `count_tokens` response's
`context_management` object carries **only** `original_input_tokens`. It has no
`applied_edits`. That's a different model from the *generation* response's
`BetaContextManagementResponse`, which has
`applied_edits: List[...]` where each `BetaClearToolUses20250919EditResponse`
carries `cleared_tool_uses` and `cleared_input_tokens`. So on the free preview
path you learn *how much* was cleared (by subtraction) but not *how many tool
uses*. Do not write code that reaches for `applied_edits` on a count_tokens
result — the field does not exist.

### 4. The caveats that belong in the README, not buried

All from the context-editing docs (2026-08-11):

- **`clear_at_least` is all-or-nothing.** "If the API can't clear at least the
  specified amount, the strategy will not be applied." So an over-ambitious
  `clear_at_least` silently yields zero savings rather than partial savings.
- **Pairing is preserved by default.** Only `tool_result` bodies are cleared;
  the preceding `tool_use` block stays, so the model retains the record that it
  made the call and with what input. Setting `clear_tool_inputs: true` removes
  the inputs too.
- **Clearing invalidates the prompt cache prefix.** "Tool result clearing:
  Invalidates cached prompt prefixes when content is cleared. To account for
  this, clear enough tokens to make the cache invalidation worthwhile." This is
  the whole reason `clear_at_least` exists.
- **Your client keeps the full history.** "Your client application maintains the
  full, unmodified conversation history. You do not need to sync your client
  state with the edited version." The editing is per-request and server-side —
  which is precisely why a *preview* tool is useful: nothing local changes, so
  you cannot inspect the effect by printing your own messages list.
- `count_tokens` does **not** use prompt caching at all ("token counting
  provides an estimate without using caching logic"), so the preview measures
  raw prefix size and will not mislead you with cache hits.

### 5. Practitioner reception: real, but with a consistent complaint

The launch discussion is
[HN 45479006, "Managing context on the Claude Developer Platform"](https://news.ycombinator.com/item?id=45479006)
— **about 10 months old (Oct 2025)**, so treat the specifics as possibly stale;
the sentiment is what I'm citing. The recurring substantive criticisms:

- **Cache invalidation is the hidden cost.** Multiple commenters pointed out
  that removing context breaks prompt caching, so the token saving is partly
  paid back in cache-write cost and latency. This is the same tradeoff the docs
  admit, and it argues for measuring rather than assuming.
- **Summarization degrades quality**, and models hallucinate references to
  content that was compacted away. Note this criticism lands on
  *compaction*, not on tool-result clearing — clearing is lossy but explicit,
  and leaves a placeholder plus the original `tool_use`.
- **Limited novelty** — several said they already do this client-side.

I could not reach the `hn-search` MCP tools from this cycle's tool set, so this
is WebSearch/WebFetch-sourced HN rather than an Algolia query. I am not claiming
a quantitative reception signal.

The honest synthesis: the feature's value is *empirical and workload-specific*.
"Does clearing pay for itself on my transcript?" is exactly the question a free
`count_tokens` preview answers, and exactly the question nobody in that thread
had a cheap way to answer.

### 6. Model IDs

Per `knowledge/anthropic-models.md` (verified 2026-07-27), cheap runnable
examples default to `claude-haiku-4-5`. `count_tokens` returns counts under the
tokenizer of the `model` you pass, and the docs warn that Claude 4.7+ models use
a newer tokenizer producing ~30% more tokens for the same text — so the model
constant materially changes the numbers printed. Keep it in one named constant.
Context editing is supported on all active models per the docs; I did not
independently verify Haiku 4.5 specifically, so the builder should treat a
`400` naming an unsupported model as a real possibility and fail loudly.

## Build proposal

### Layer 1 — Intent

Ship `examples/context-editing-preview/`: a tool that answers, for $0 and with
no generation, **"what would `clear_tool_uses_20250919` do to this transcript?"**
by counting the same request twice through the free token-counting endpoint —
once plain, once with `context_management` — and reporting the delta.

**Explicitly out of scope:** running a real agent loop; the `compact_20260112`
strategy; `clear_thinking_20251015`; client-side pruning of the caller's own
message list; any billed `messages.create` call; measuring cache-write cost.

**Directory name check:** `examples/context-editing-preview/` does not exist
(`ls examples/` on current `main` — 14 dirs, none matching), no open PR uses it
(`gh pr list --state open` is empty), and no branch claims it (`git branch -a`
shows only `main` plus the two merged cycle branches).

### Layer 2 — Behavioral spec

**Inputs**
- A transcript: a `list` of Messages-API message dicts containing
  `tool_use`/`tool_result` pairs. Generated synthetically by a pure helper
  (`rounds`, `result_chars`) so the example is self-contained and deterministic.
- A policy: keep-count, trigger (kind + value), and optional `clear_at_least`
  and `exclude_tools`.
- At the shell only: `ANTHROPIC_API_KEY` from the environment.

**Outputs**
- A report: `original_input_tokens`, `edited_input_tokens`, `tokens_saved`,
  `percent_saved`, and an explicit `applied: bool`.
- The exact `context_management` dict that was sent, printed, so the reader can
  paste it into their own `messages.create` call.

**Invariants**
1. `to_edit()` emits `type` first and **omits** optional keys entirely when
   unset — it never emits `"clear_at_least": null` or `"exclude_tools": null`.
2. A policy that cannot produce a legal request is unconstructible: `keep < 0`,
   `trigger value < 1`, `clear_at_least < 1`, or an empty/blank tool name in
   `exclude_tools` all raise `ValueError` **at construction**, not at call time.
   (Validate at the boundary, once — Protocol §4. The `max_attempts < 1`
   unreachable-raise bug fixed in PR #24 is the cautionary precedent: write the
   failing test first.)
3. The core never imports `anthropic` and never reads an env var. It declares a
   `CountTokens` callable interface; the shell supplies the real SDK-backed
   implementation.
4. `tokens_saved == original - edited`, and `percent_saved` is computed from
   those two only. Never negative.

**Failure modes**
- **No edit applied** (transcript below trigger, or `clear_at_least`
  unsatisfiable): the API returns a result whose `context_management` is
  absent/`None`. The report must render `applied: False`, `tokens_saved: 0`, and
  say *why it might be* (below trigger / `clear_at_least` not met) — not crash,
  and not report a negative saving. **This is the acceptance criterion most
  likely to be got wrong.**
- Missing `ANTHROPIC_API_KEY`: exit non-zero with a one-line message before any
  network call.
- API error (unsupported model, bad beta, rate limit): propagate loudly. Do not
  swallow into a zero-saving report.

**Acceptance criteria ("it works")**
- A1. `python test_preview.py` passes with **no API key and no network**.
- A2. Each of the four invalid-policy cases in invariant 2 raises `ValueError`,
  each asserted by its own test.
- A3. A policy with only `keep` and `trigger` set serialises to exactly
  `{"type": "clear_tool_uses_20250919", "trigger": {...}, "keep": {...}}` —
  asserted by dict equality, so an accidental `null` key fails the test.
- A4. Given a fake `CountTokens` returning `(edited=25000, original=70000)`, the
  report is `applied=True, tokens_saved=45000, percent_saved=64.3`.
- A5. Given a fake returning `edited=8000, context_management=None`, the report
  is `applied=False, tokens_saved=0` and does not raise.
- A6. A fake asserts the shell's call carries
  `betas=["context-management-2025-06-27"]` and
  `context_management={"edits": [<the policy dict>]}`.
- A7. `python main.py` with a key prints a real report for a 20-round synthetic
  transcript with a `{"type": "tool_uses", "value": 5}` trigger and
  `keep={"type": "tool_uses", "value": 3}`, showing `applied: True` and a
  positive saving. The README pastes that exact stdout, and
  `examples/readme-transcript-check/check_transcript.py` (shipped today in
  PR #24) is run against it so the transcript cannot silently rot.

Use the `tool_uses` trigger for the demo — `input_tokens` defaults to 100k and
would require an absurdly large synthetic transcript to fire. Mention the
`input_tokens` form in the README as the production default.

### Layer 3 — Interfaces (stubs, no bodies)

```python
# policy.py — one sentence: turns a validated clearing policy into the API's edit dict.
BETA = "context-management-2025-06-27"
STRATEGY = "clear_tool_uses_20250919"

TriggerKind = Literal["input_tokens", "tool_uses"]

@dataclass(frozen=True)
class ClearToolUsesPolicy:
    keep: int
    trigger_kind: TriggerKind
    trigger_value: int
    clear_at_least: int | None = None
    exclude_tools: tuple[str, ...] = ()
    def __post_init__(self) -> None: ...   # raises ValueError; see invariant 2
    def to_edit(self) -> dict[str, object]: ...
    def to_config(self) -> dict[str, object]: ...   # {"edits": [self.to_edit()]}

# transcript.py — one sentence: builds a synthetic tool-heavy transcript.
def build_transcript(rounds: int, result_chars: int) -> list[dict[str, object]]: ...

# preview.py — pure core; declares the interface the edge implements.
class CountTokens(Protocol):
    def __call__(
        self, *, messages: Sequence[Mapping[str, object]],
        tools: Sequence[Mapping[str, object]],
        context_management: Mapping[str, object] | None,
    ) -> "TokenCount": ...

@dataclass(frozen=True)
class TokenCount:
    input_tokens: int
    original_input_tokens: int | None    # None => no edit applied

@dataclass(frozen=True)
class PreviewReport:
    applied: bool
    original_input_tokens: int
    edited_input_tokens: int
    tokens_saved: int
    percent_saved: float

def preview(count: CountTokens, transcript, tools, policy) -> PreviewReport: ...

# main.py — imperative shell: reads the key, adapts the SDK to CountTokens, prints.
MODEL = "claude-haiku-4-5"
def main() -> int: ...
```

Files: `README.md`, `policy.py`, `transcript.py`, `preview.py`, `main.py`,
`test_preview.py`, `requirements.txt` (`anthropic==0.121.0`).

The `TokenCount` adapter is where the §3 asymmetry gets absorbed: the shell maps
`resp.context_management.original_input_tokens` (or `None`) into the core's
type, so the core never learns that `applied_edits` is missing on this endpoint.

## Open questions

- **Does `count_tokens` return `context_management: null` or omit the key
  entirely when the trigger does not fire?** The docs show the populated shape
  only; the SDK types make the field `Optional[...] = None`, which covers both
  wire forms. The builder should handle `None` and confirm the real behaviour
  on the first live run, then record it in the README. A5 tests our handling,
  not the API's choice.
- **Is `keep=0` legal?** The SDK type is a bare `int` with no constraint, and
  the docs give a default of 3 without stating a minimum. I specified rejecting
  `keep < 0` (not `< 1`) because I cannot confirm 0 is invalid; if the live run
  returns a 400 for `keep=0`, tighten it and say so.
- **Is `clear_tool_uses_20250919` supported on `claude-haiku-4-5`?** Docs say
  "all supported Claude models"; not independently verified for this ID.
  There is a `beta_context_management_capability.py` type in the SDK that
  suggests capability is model-advertised — worth a look if the live run 400s.
- **Placeholder text.** The docs say cleared results are replaced with
  "placeholder text" but never quote it. Not needed for this increment (we only
  measure token counts) but it would be worth capturing if the builder happens
  to observe it.
- I could not consult a locally installed `claude-api` skill — only `graphify`
  is present under `~/.claude/skills/`. I used Anthropic's published
  `anthropics/skills` repo and the platform docs as primary sources instead,
  plus direct reads of the installed SDK's generated types.

## Sources

- [Context editing — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/context-editing) (fetched 2026-08-11)
- [Token counting — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/token-counting) (fetched 2026-08-11)
- [Compaction — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/compaction) (referenced, out of scope)
- [anthropics/skills — `skills/claude-api/shared/tool-use-concepts.md`](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/tool-use-concepts.md) (fetched 2026-08-11)
- `anthropic==0.121.0` generated types, read locally 2026-08-11:
  `types/beta/beta_clear_tool_uses_20250919_edit_param.py`,
  `beta_context_management_config_param.py`,
  `beta_message_tokens_count.py`,
  `beta_count_tokens_context_management_response.py`,
  `beta_clear_tool_uses_20250919_edit_response.py`,
  `beta_context_management_response.py`,
  `types/anthropic_beta_param.py`,
  `resources/beta/messages/messages.py:1834`
- [HN 45479006 — "Managing context on the Claude Developer Platform"](https://news.ycombinator.com/item?id=45479006) (~Oct 2025, **possibly stale**)
- [HN 45418251 — "Effective context engineering for AI agents"](https://news.ycombinator.com/item?id=45418251) (~Oct 2025, background)
