# Previewing `clear_thinking_20251015` for $0

## Question

Can `examples/context-editing-preview/` gain a second context-editing strategy —
`clear_thinking_20251015` — as a pure policy type plus a synthetic thinking
transcript, so the same free `count_tokens` double-count reports the tokens
saved by clearing old reasoning blocks? And what does that transcript need to
contain — real signed thinking blocks, or synthetic ones?

## Findings

### The edit is much simpler than `clear_tool_uses_20250919`

From the [context editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)
(fetched 2026-09-08; page shows no explicit date, still on beta
`context-management-2025-06-27`) and the anthropic-sdk-python `main` branch
type files (fetched 2026-09-08):

`beta_clear_thinking_20251015_edit_param.py` — `BetaClearThinking20251015EditParam`,
`total=False`:

| Field | Type | Required |
|---|---|---|
| `type` | `Literal["clear_thinking_20251015"]` | **yes** (explicit `Required`) |
| `keep` | `Keep` | no |

`Keep = Union[BetaThinkingTurnsParam, BetaAllThinkingTurnsParam, Literal["all"]]`.
`BetaThinkingTurnsParam` = `{"type": Literal["thinking_turns"], "value": int}`,
**both fields Required**. `value` must be `> 0` (docs). The bare string `"all"`
is the documented simple form for "keep everything"; `BetaAllThinkingTurnsParam`
is an object alternative, not needed for this increment.

There is **no `trigger`, no `clear_at_least`, no `exclude_tools`, no
`clear_tool_inputs`.** The strategy fires **unconditionally** — the docs' config
table lists only `keep`. So the policy type is one optional field, versus
`ClearToolUsesPolicy`'s five.

Default `keep` when omitted is **model-specific**: Opus 4.5+ / Sonnet 4.6+ /
Fable / Mythos keep all prior thinking turns; earlier Opus/Sonnet and all Haiku
keep only the last turn.

### Beta header is the *same* as `clear_tool_uses`

`context-management-2025-06-27` — identical to what `policy.BETA` already holds.
No new constant, no second beta. (Contrast `compact_20260112`, which needs
`compact-2026-01-12`; see `knowledge/compaction.md`.)

### Ordering rule (recorded, not exercised here)

Docs: "When using multiple strategies, the `clear_thinking_20251015` strategy
must be listed first in the `edits` array." This increment sends exactly one
edit, so ordering does not bite — but it belongs in the knowledge note.

### `count_tokens` supports it — the free-preview path is unchanged

The [token-counting docs](https://platform.claude.com/docs/en/build-with-claude/token-counting)
(fetched 2026-09-08) and the context-editing docs both show a `count_tokens`
request carrying `context_management.edits[].type = "clear_thinking_20251015"`.
The response carries `input_tokens` (after editing) and
`context_management.original_input_tokens` (before). That is the **exact shape
`preview.py` already consumes** for `clear_tool_uses` — same
`TokenCount(input_tokens, original_input_tokens)`, same
`original_input_tokens is None ⇒ nothing applied` asymmetry, same "no
`applied_edits` on the free path" rule from `knowledge/context-editing.md`. The
core `preview()` / `PreviewReport` / `EditPolicy` code needs **zero** changes;
`preview.py`'s own "Deviations" note already anticipates this
("a future `clear_thinking_20251015` policy would drop in unchanged").

For completeness, the *billed* `create` response type is
`BetaClearThinking20251015EditResponse` =
`{type, cleared_input_tokens: int, cleared_thinking_turns: int}` (all Required) —
not used by this increment.

### The discriminating constraint: the model must retain prior-turn thinking

Token-counting docs, "Count tokens in messages with thinking":

> Thinking blocks from **previous** assistant turns count toward your input
> tokens on models that keep all prior turns; on models that keep only the last
> turn, the API strips them and they do **not** count. **Current** assistant
> turn thinking **does** count.

So on `claude-haiku-4-5` (the existing example's `MODEL`) or Sonnet 4.5 and
earlier, the API auto-strips prior-turn thinking before counting — there is
nothing left for `clear_thinking_20251015` to clear, and the measured saving is
~0. The demo must run against a **keep-all** model. Cheapest qualifying option
per `knowledge/anthropic-models.md` is **`claude-sonnet-5`** ($2/$10; Haiku is
disqualified, mirroring the `compact_20260112` situation). `count_tokens` is
free regardless of model, so this is a correctness choice, not a cost one.

### Synthetic vs. real thinking blocks in the input

The token-counting docs' worked example passes an assistant `thinking` block
whose `signature` is an **obviously truncated placeholder**
(`"EuYBCkQYAiJAgCs1le6/Pol5Z4/JMomVOouGrWdhYNsH3ukzUECbB6iWrSQtsQuRHJID6lWV..."`)
plus `thinking={"type": "adaptive"}`, and shows it returning
`{"input_tokens": 88}`. Every documented 400 `invalid_request_error` for
"invalid / modified signature in thinking block" I found
([anthropics/claude-code#10627](https://github.com/anthropics/claude-code/issues/10627),
[router-for-me/CLIProxyAPI#1398](https://github.com/router-for-me/CLIProxyAPI/issues/1398),
[NousResearch/hermes-agent#17992](https://github.com/NousResearch/hermes-agent/issues/17992),
all 2025–2026) is about **`messages.create`**, not `count_tokens`.

**Best read of the evidence: `count_tokens` does not cryptographically verify
thinking-block signatures — structural well-formedness (`type` / `thinking` /
`signature` keys present) is enough.** This is not confirmed live and the docs
example could be merely illustrative, so it is the top open question — but the
increment is designed so the answer does not gate shipping (see below).

Note also (docs, and `knowledge/thinking-blocks.md`): on 5-series models
`thinking` must be the **adaptive** form `{"type": "adaptive"}` — manual
`budget_tokens` is a 400 on 4.7+. So the live counter passes
`thinking={"type": "adaptive"}`.

### Cache interaction (recorded, out of scope to measure)

Docs: keeping thinking blocks preserves the prompt cache; clearing invalidates
it "at the point where clearing occurs" — the same tension `knowledge/prompt-caching.md`
and PR #40 (`examples/context-editing-cache-tradeoff/`) already cover for
`clear_tool_uses`. This increment measures only the token drop, exactly as the
existing `clear_tool_uses` preview does.

### SDK version note

`anthropic` is now on the 1.x line (PyPI latest `1.4.0`, fetched 2026-09-08;
PR #40's `requirements.txt` confirms 1.4.0/1.3.0/1.2.0/1.1.0/1.0.0, plus a
parallel `0.12x` line at `0.125.0`, 2026-08-19). The existing
`context-editing-preview` example pins `anthropic==0.121.0`. **Do not bundle an
SDK bump into this feature** (Protocol §5, one intent per change): the pure
policy and the offline self-test import nothing, and the live path sends plain
dicts through `count_tokens`. Keep the `0.121.0` pin; the builder should just
confirm at build time that `0.121.0`'s `beta.messages.count_tokens` accepts a
`thinking=` kwarg (the non-beta `messages.count_tokens` does, per the docs'
Python sample) — if it does not, pass it in the request body instead.

### `claude-api` skill

Not installed in this environment (`~/.claude/plugins` has only `clangd-lsp`).
Model ids, betas, and the `thinking`/`context_management` field shapes here are
taken from the live docs (2026-09-08), the SDK `main`-branch type files
(2026-09-08), and the repo's own dated `knowledge/` notes, cross-checked against
each other. Flagged in Open questions.

## Build proposal

Layers 1–3 of the Engineering Protocol, for the builder.

### Layer 1 — Intent

Add `clear_thinking_20251015` to `examples/context-editing-preview/` as a
second, self-validating `EditPolicy` (`ClearThinkingPolicy`) plus a synthetic
thinking-heavy transcript and a sibling entry point, so the existing free
`count_tokens` double-count reports the tokens saved by clearing old reasoning
blocks — with the offline self-test as the acceptance surface and one optional
$0 live preview.

**Explicitly out of scope:** any billed `messages.create` call; obtaining real
signed thinking blocks; measuring the cache-write cost that clearing incurs;
running a real agent or thinking loop; the `compact_20260112` strategy;
combining `clear_thinking` with `clear_tool_uses` in one request; and any change
to the existing `ClearToolUsesPolicy` / `main.py` / `transcript.py` /
`preview.py` code paths beyond additive ones.

### Layer 2 — Behavioral spec

**Inputs**

- `ClearThinkingPolicy(keep: int | Literal["all"] | None = None)` — `keep` is
  the number of most recent assistant thinking-turns to retain; `"all"` keeps
  every thinking block; `None` omits the field (model default).
- `build_thinking_transcript(turns: int, thinking_chars: int) -> list[dict]` — a
  deterministic conversation: an opening user turn, then per round one assistant
  turn holding `[thinking block, text block]` and one user follow-up turn,
  ending on a user turn (the shape you send to request the next assistant turn).
  Each `thinking` block has non-empty `thinking` text of exactly `thinking_chars`
  characters and a `signature` string (a documented synthetic placeholder).
- Live path only: `ANTHROPIC_API_KEY` in the environment; model
  `claude-sonnet-5`.

**Outputs**

- `ClearThinkingPolicy.to_edit()`:
  - `keep is None`  → `{"type": "clear_thinking_20251015"}`
  - `keep == N` (int) → `{"type": "clear_thinking_20251015", "keep": {"type": "thinking_turns", "value": N}}`
  - `keep == "all"` → `{"type": "clear_thinking_20251015", "keep": "all"}`
- `ClearThinkingPolicy.to_config()` → `{"edits": [self.to_edit()]}`.
- The sibling entry point prints a report of the same shape `main.py` prints:
  strategy, model, transcript summary, beta, the exact `context_management`
  object sent, then `applied` / `original` / `edited` / `saved (tokens, %)`.
- Offline self-test exits 0 with no key, no network, and `anthropic` not
  importable.

**Invariants**

1. `to_edit()` never emits `keep` when `keep is None` — an unset optional is
   omitted, not `null` (mirrors `ClearToolUsesPolicy`'s A3 contract).
2. `to_edit()`'s first key is always `"type"`, value always
   `"clear_thinking_20251015"`.
3. `BETA` is unchanged and shared: `"context-management-2025-06-27"`. No new
   beta constant.
4. `build_thinking_transcript` is pure and deterministic: equal args → identical
   list (by `==`); `1 + 2*turns` messages; every assistant message's `content`
   is exactly `[{"type": "thinking", ...}, {"type": "text", ...}]` with
   `len(content[0]["thinking"]) == thinking_chars` and a non-empty
   `content[0]["signature"]`.
5. `preview()`, `PreviewReport`, `TokenCount`, `EditPolicy`, `CountTokens` are
   reused **unchanged** — the increment adds a new `EditPolicy` implementation
   and a new transcript builder, no new core logic.
6. `tokens_saved >= 0`; `edited <= original`; `applied is False ⇒
   original == edited` (already enforced by `PreviewReport.__post_init__`).

**Failure modes**

- `ClearThinkingPolicy` raises `ValueError` at construction if `keep` is an int
  `<= 0`, or a `str` other than `"all"`. Raises `TypeError` if `keep` is not
  `int | str | None` (note `bool` is an `int` subclass — reject it explicitly,
  `keep=True` is not a turn count). Validated once, at the boundary;
  `to_edit()` / `to_config()` never re-check.
- Missing key → the sibling entry point prints one line to stderr, returns
  `EXIT_NO_KEY` (non-zero), makes no network call. Same contract as `main.py`.
- Every `anthropic.APIError` from the live count propagates untouched — a
  rejected beta, unsupported model, or rate limit surfaces as an exception with
  a traceback, never a zero-saving report.
- If `count_tokens` rejects synthetic thinking-block signatures, the live path
  fails loudly with that API error; the offline self-test is unaffected and
  still fully covers layers 2's criteria.

**Acceptance criteria**

1. `python test_preview_thinking.py` exits 0 with no `ANTHROPIC_API_KEY`, no
   network, and `anthropic` not installed.
2. `ClearThinkingPolicy().to_edit() == {"type": "clear_thinking_20251015"}`
   exactly (dict equality — a stray `"keep": None` fails).
3. `ClearThinkingPolicy(keep=2).to_edit() == {"type": "clear_thinking_20251015",
   "keep": {"type": "thinking_turns", "value": 2}}`.
4. `ClearThinkingPolicy(keep="all").to_edit() == {"type":
   "clear_thinking_20251015", "keep": "all"}`.
5. `ClearThinkingPolicy(keep=0)`, `(keep=-1)`, `(keep="most")` each raise
   `ValueError`; `(keep=1.5)` and `(keep=True)` each raise `TypeError`. One
   assertion per case.
6. `build_thinking_transcript(turns=6, thinking_chars=800)` returns 13 messages;
   called twice the two lists are `==`; each assistant message (indices
   1,3,5,7,9,11) has `content == [thinking, text]` with
   `len(content[0]["thinking"]) == 800` and `content[0]["signature"]` truthy.
7. `preview()` with a `FakeCounter` scripted `original=60000, edited=38000`
   yields `PreviewReport(applied=True, original_input_tokens=60000,
   edited_input_tokens=38000)`, `tokens_saved == 22000`, `percent_saved == 36.7`.
8. `preview()` with a `FakeCounter` whose edited count reports
   `original_input_tokens=None` yields `applied=False`, `tokens_saved == 0`, no
   exception.
9. A `FakeClient` exposing **only** `.beta.messages.count_tokens` (no
   `.messages`): the live counter's call sends
   `betas == ["context-management-2025-06-27"]`,
   `thinking == {"type": "adaptive"}`, and
   `context_management == {"edits": [{"type": "clear_thinking_20251015", "keep":
   {"type": "thinking_turns", "value": <DEMO_POLICY.keep>}}]}`.
10. `python main.py` and `python test_preview.py` (the existing `clear_tool_uses`
    path) still run and pass unchanged.
11. `README.md`'s "Explicitly out of scope" no longer lists
    `clear_thinking_20251015`; a new section documents the edit shape, the three
    `keep` forms, and the "must run on a keep-all model (`claude-sonnet-5`, not
    `claude-haiku-4-5`) or there is nothing to clear" constraint. The live
    transcript is marked NOT YET CAPTURED with no invented numbers, matching the
    existing section.

### Layer 3 — Interfaces (no bodies)

`policy.py` — append only; `ClearToolUsesPolicy` untouched:

```python
STRATEGY_CLEAR_THINKING = "clear_thinking_20251015"
KEEP_THINKING_KIND = "thinking_turns"   # the object-form discriminator
KEEP_ALL = "all"                        # the string-form "keep everything"

@dataclasses.dataclass(frozen=True)
class ClearThinkingPolicy:
    """A `clear_thinking_20251015` edit that is well-formed by construction.

    `keep` is the count of most recent assistant thinking-turns to retain
    (`{"type": "thinking_turns", "value": N}`, N > 0), or "all", or None to
    omit the field and take the model default. There is no trigger: the edit
    always fires. Failure modes (all at construction): ValueError if keep is an
    int <= 0 or a str other than "all"; TypeError if keep is not int|str|None
    (bool rejected explicitly).
    """
    keep: int | Literal["all"] | None = None

    def __post_init__(self) -> None: ...
    def to_edit(self) -> dict[str, object]: ...      # omits `keep` when None
    def to_config(self) -> dict[str, object]: ...    # {"edits": [self.to_edit()]}
```

New `thinking_transcript.py` — one sentence: "builds the synthetic
thinking-heavy request this preview measures":

```python
TOOL_FREE = True  # no tools in this transcript; documents the difference from transcript.py
SYNTHETIC_SIGNATURE: str  # placeholder; NOT a real signature — see README

def build_thinking_transcript(turns: int, thinking_chars: int) -> list[dict[str, object]]:
    """`1 + 2*turns` messages; each assistant turn is [thinking, text].
    Pure/deterministic. ValueError if turns < 1 or thinking_chars < 1.
    """
```

New `preview_thinking.py` — sibling of `main.py` (imperative shell for the
thinking strategy):

```python
MODEL = "claude-sonnet-5"       # must retain prior-turn thinking; see README + research note
API_KEY_ENV = "ANTHROPIC_API_KEY"
TURNS = 8
THINKING_CHARS = 1200
DEMO_POLICY = policy.ClearThinkingPolicy(keep=2)
EXIT_OK = 0
EXIT_NO_KEY = 1

def make_thinking_counter(client, *, model: str) -> preview.CountTokens:
    """Adapt `client.beta.messages.count_tokens` to `preview.CountTokens`.
    Internally also sends `betas=[policy.BETA]` and
    `thinking={"type": "adaptive"}` (required on 5-series). Needs only
    `.beta.messages.count_tokens`, so a fake satisfies it. APIError propagates.
    """

def render(report: preview.PreviewReport, pol: policy.ClearThinkingPolicy, *,
           model: str, turns: int, thinking_chars: int) -> str: ...

def main() -> int:
    """Preview DEMO_POLICY against build_thinking_transcript(TURNS, THINKING_CHARS)
    and print the report. EXIT_NO_KEY + stderr line + no network if key absent.
    """
```

New `test_preview_thinking.py` — offline, stdlib only, mirrors
`test_preview.py`'s doubles (`FakeCounter`; `FakeMessages`; `FakeClient`
exposing only `.beta`, deliberately missing `.messages`).

**Files added:** `thinking_transcript.py`, `preview_thinking.py`,
`test_preview_thinking.py`.
**Files edited (additively):** `policy.py` (new class + constants),
`README.md` (new section + out-of-scope line), `requirements.txt` (comment only;
keep `anthropic==0.121.0`).
**No new example directory** — `ls examples/` on `main` shows
`context-editing-preview/` already exists and this extends it;
`gh pr list --state open` (#36, #39, #40) and `git branch -a` show nothing else
touching it (#40 creates the separate `context-editing-cache-tradeoff/`).

### "It works"

`python test_preview_thinking.py` → criteria 1–9 pass, exit 0, no key / network /
SDK. `python main.py` and `python test_preview.py` still pass (criterion 10).
README updated (criterion 11). Optional, with a key: `python preview_thinking.py`
prints a report with non-zero `saved` on `claude-sonnet-5` — the "NOT YET
CAPTURED" live half, matching the discipline the existing README section already
follows (no numbers invented if the run is not made).

## Open questions

- **Does `count_tokens` accept thinking blocks with synthetic / placeholder
  signatures?** The token-counting docs' own worked example uses a visibly
  truncated signature and shows a successful count, and every signature-400
  report traced back to `messages.create`, not `count_tokens` — but this is not
  verified live. If it turns out `count_tokens` *does* verify, the offline
  self-test (the whole acceptance surface) is unaffected; only the optional live
  preview would need a real signed block first, which the README would note
  rather than fake. Worth one $0 call to settle when a key is available.
- **Does `anthropic==0.121.0`'s `beta.messages.count_tokens` accept a
  `thinking=` kwarg?** The non-beta `messages.count_tokens` does (docs' Python
  sample). If the beta binding in that pinned version does not, the builder
  passes `thinking` in the request body / via `extra_body` instead — a
  build-time check, not a blocker.
- **Is `tools=[]` accepted by `count_tokens`, or must the key be omitted when
  empty?** `preview.preview()` always passes `tools=`; the thinking transcript
  has none. Low risk (empty list should be fine); if not, the new adapter omits
  `tools` when empty, the way `make_counter` already omits `context_management`
  when `None`.
- **Exact placeholder text the API substitutes for a cleared thinking block.**
  Docs describe it for tool results ("placeholder text indicating ... it was
  removed") but say nothing for thinking. Not needed for a token-count preview —
  noted for the eventual live `create` example.
- **`claude-api` skill unavailable here.** All model ids / betas / field shapes
  came from live docs + SDK `main` type files + the repo's dated `knowledge/`
  notes (all 2026-09-08), cross-checked. The builder should re-confirm
  `claude-sonnet-5` and `context-management-2025-06-27` against the skill if it
  is present in the build environment.
