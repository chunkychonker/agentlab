# Server-side compaction (`compact_20260112`): the summarize-don't-prune sibling

Date: 2026-08-12

## Question

What does Anthropic's server-side compaction (`compact_20260112`) actually
return and cost, how does a client correctly continue a conversation across a
compaction boundary, and what part of that can be proven without an API key?

## Backlog item

Topmost unclaimed item under **Context & cost** — "Server-side compaction
(`compact_20260112`, beta `compact-2026-01-12`)", filed 2026-08-11 as the
deliberate follow-on to the context-editing cycle. `gh pr list --state open` is
empty, `git branch -a` shows only `main` plus three already-merged cycle
branches, so nothing else is covering this ground. Marked `[researching]`.

This note assumes [`research/2026-08-11-context-editing-preview.md`](2026-08-11-context-editing-preview.md)
and `knowledge/context-editing.md`, and does not repeat the
`clear_tool_uses_20250919` material.

## Findings

### 1. The parameter is small; four fields, one of which has a hard floor

From the [Compaction docs](https://platform.claude.com/docs/en/build-with-claude/compaction)
(fetched 2026-08-12) and confirmed field-for-field against the generated type
`types/beta/beta_compact_20260112_edit_param.py` in `anthropic==0.121.0`
(installed in a throwaway venv today; 0.121.0 is still the current release):

```
type                    Required  Literal["compact_20260112"]
trigger                           BetaInputTokensTriggerParam | None   # default 150_000
pause_after_compaction            bool                                  # default False
instructions                      str | None                            # default None
```

Four things that matter:

- **`trigger` has exactly one shape here.** `BetaInputTokensTriggerParam` is
  `{"type": Literal["input_tokens"], "value": int}`. There is no `tool_uses`
  trigger for compaction — the escape hatch that made the context-editing demo
  cheap to fire does not exist. Both the Anthropic docs and the
  [Bedrock compaction page](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-compaction.html)
  (fetched 2026-08-12) state the value **must be at least 50,000 tokens**.
  The SDK type is a bare `int`, so that floor is server-enforced only.
- **`instructions` replaces the default prompt completely** — it does not
  append. The Bedrock page quotes the default prompt verbatim, which the
  Anthropic page does not; it ends "You must wrap your summary in a
  `<summary></summary>` block." So a careless custom `instructions` silently
  drops that formatting contract as well as the continuity framing.
- **`pause_after_compaction: true`** makes the API return as soon as the summary
  exists, with `stop_reason: "compaction"` and *only* the compaction block in
  `content`. You then append that response and re-request to get the actual
  answer. This is the cheap path: one compaction iteration, no answer iteration.
- The wrapper is the same `{"edits": [...]}`; `BetaContextManagementConfigParam`'s
  `Edit` union in 0.121.0 is
  `BetaClearToolUses20250919EditParam | BetaClearThinking20251015EditParam | BetaCompact20260112EditParam`.

### 2. Three SDK-source facts the docs prose does not tell you

These came out of reading `anthropic==0.121.0`'s generated types, not the docs.

**(a) `"compact-2026-01-12"` is NOT in the SDK's beta literal union.**
`types/anthropic_beta_param.py` lists 33 beta strings; `context-management-2025-06-27`
is there, `compact-2026-01-12` is not. Because the alias is
`Union[str, Literal[...]]`, passing it type-checks fine — but a *typo* in it
also type-checks fine and fails at the server. This is the exact inverse of the
reassurance recorded for `clear_tool_uses_20250919` in
`knowledge/context-editing.md` ("a typo is a type error rather than a silent
no-op"). For compaction it is not. Pin the string in one named constant.

**(b) `applied_edits` will never mention compaction.**
`types/beta/beta_context_management_response.py` defines
`AppliedEdit = BetaClearToolUses20250919EditResponse | BetaClearThinking20251015EditResponse`
— there is no compaction variant. So the "did it fire?" signal on a generation
response is *not* `response.context_management`. It is:
1. a `{"type": "compaction"}` block in `response.content`
   (`BetaCompactionBlock` is a member of the `BetaContentBlock` union), and/or
2. `response.stop_reason == "compaction"` (present in the `BetaStopReason`
   literal, though the docstring on `BetaMessage.stop_reason` in 0.121.0 does
   **not** list it — the docstring enumerates seven values and omits this one),
   and/or
3. a `usage.iterations` entry with `type: "compaction"`.

**(c) A compaction block can carry `content: None`, meaning it failed.**
`beta_compaction_block.py`'s own docstring: "When content is None, it indicates
the compaction failed to produce a valid summary (e.g., malformed output from
the model). Clients may round-trip compaction blocks with null content; the
server treats them as no-ops." The param version adds "Empty string content is
not allowed." Neither docs page mentions this failure mode. Any adapter that
does `block["content"][:200]` — as *both* docs pages' example code does —
crashes on it.

Both block types also carry `encrypted_content: Optional[str]`, documented as
"Opaque metadata from prior compaction, to be round-tripped verbatim." Neither
docs page's continuation example preserves it explicitly (they round-trip the
whole `content` array, which happens to carry it). Hand-rebuilding the block
without it is a silent correctness bug.

### 3. Continuation contract

From the Bedrock page (fetched 2026-08-12), which is more explicit than the
Anthropic page:

> On subsequent requests, append the response to your messages. The API
> automatically drops all message blocks before the `compaction` block,
> continuing the conversation from the summary.

So the client keeps its full history and the *server* prunes — the same
"you don't sync your state" property as context editing. Corollary for a demo:
you cannot see the effect by printing your local messages list.

Also: "A long-running conversation may result in multiple compactions. The last
compaction block reflects the final state of the prompt."

With `pause_after_compaction: true`, the documented flow is: check
`stop_reason == "compaction"`, append `{"role": "assistant", "content":
response.content}`, then re-request with the same `context_management`.

### 4. Billing: the top-level `usage` numbers become wrong

This is the finding with the most operational bite. From the Bedrock page:

> The top-level `input_tokens` and `output_tokens` in the `usage` field do not
> include compaction iteration usage, and reflect the sum of all non-compaction
> iterations. To calculate the total tokens consumed and billed for a request,
> sum across all entries in the `usage.iterations` array.
> If you previously relied on `usage.input_tokens` and `usage.output_tokens`
> for cost tracking or auditing, you will need to update your tracking logic...
> The `iterations` array is **only present when a new compaction is triggered**
> during the request. Re-applying a previous `compaction` block incurs no
> additional compaction cost, and the top-level usage fields remain accurate in
> that case.

The documented example makes the size of the discrepancy concrete: top-level
`input_tokens: 45000` while the compaction iteration alone consumed
`input_tokens: 180000, output_tokens: 3500`.

SDK shape: `BetaUsage.iterations: Optional[BetaIterationsUsage]`, where
`BetaIterationsUsage = List[Union[BetaMessageIterationUsage,
BetaCompactionIterationUsage, BetaAdvisorMessageIterationUsage,
BetaFallbackMessageIterationUsage]]`, discriminated on `type` with literals
`"message"` and `"compaction"` respectively. So `iterations` being `None` is a
legal, meaningful state (no new compaction), not an error.

### 5. Supported models — Haiku is not one of them

The docs list compaction support for: `claude-fable-5`, `claude-mythos-5`,
`claude-mythos-preview`, `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-7`,
`claude-opus-4-6`, `claude-sonnet-5`, `claude-sonnet-4-6`. **`claude-haiku-4-5`
is absent.** That contradicts this repo's standing default ("for cheap runnable
examples, default to `claude-haiku-4-5`", `knowledge/anthropic-models.md`), so
this example must deviate and say why.

Cheapest supported model is `claude-sonnet-5`. Per the
[pricing page](https://platform.claude.com/docs/en/about-claude/pricing)
(fetched 2026-08-12) Sonnet 5 is **$2/MTok in, $10/MTok out** — and the page now
carries a note that the $2/$10 launch price *is* the standard price and the
scheduled 2026-09-01 rise to $3/$15 "will not occur." `knowledge/anthropic-models.md`
still says "$2/$10 intro thru 2026-08-31"; that is now stale and I have updated it.

Rough live cost of the increment below: one compaction iteration over a ~55k-token
transcript ≈ 55k × $2/MTok = $0.11, plus a ~2k-token summary ≈ $0.02. Call it
**~$0.15 for one run** with `pause_after_compaction: true`. Not free — unlike
the previous cycle's example — so the shell must refuse to spend blind.

### 6. Token counting helps, but only on the "after" side

The docs are explicit that `/v1/messages/count_tokens` **"applies existing
compaction blocks but does not trigger new compactions."** So the free-preview
trick that carried the context-editing cycle does *not* extend to compaction:
you cannot ask "what would compaction save me" for $0, because the summary has
to actually be generated.

What you *can* do for $0, both before and after the billed call:

- **Before:** count the transcript to confirm it exceeds `trigger.value`, so you
  never pay for a call that cannot fire. (Free, and rate-limited independently
  of message creation — see `knowledge/context-editing.md`.)
- **After:** count `messages + [compaction response]` with the same
  `context_management` edit. The docs' own example prints
  `count_response.input_tokens` (effective, post-drop) alongside
  `count_response.context_management.original_input_tokens` (before) — so the
  real, achieved saving is measurable for free once the block exists.

`BetaCountTokensContextManagementResponse` still has exactly one field,
`original_input_tokens` — same asymmetry as last cycle.

### 7. Practitioner reception: thin on the API, consistent on the idea

I queried HN's Algolia API directly (the `hn-search` MCP tools were not in this
cycle's tool set; same limitation the 2026-08-11 note recorded). There is
**no substantial HN thread about `compact_20260112` itself** — a comment search
for "compaction API / server-side" since Jan 2026 returns 3 hits, none about it.
I am not going to manufacture a reception signal from that.

What does recur is criticism of compaction *as a technique*, which is what the
backlog item flagged:

- ["Double-buffering for LLM context windows"](https://news.ycombinator.com/item?id=47147224)
  (2026-02-25): "Every LLM agent framework does stop-the-world compaction when
  context fills — pause, summarize, resume. The agent freezes, the user waits,
  and the post-compaction agent wakes up with a lossy summary." **Caveat: that
  post scored 2 points** — it is the author's framing of the problem, not a
  community verdict. I cite it for the articulation, not as evidence.
- A commenter on ["AGENTS.md outperforms skills in our agent evals"](https://news.ycombinator.com/item?id=46809708)
  (2026-01-29, 524 points) contrasts retrieval-per-task with "lossy 'memory'
  compaction and summarization" — the same objection, on a thread people
  actually read.

Both are about latency and lossiness, and both are *structural* — `pause_after_compaction`
makes the stop-the-world step explicit rather than removing it. The honest
framing for the example: compaction's cost is a whole extra sampling iteration
and a lossy summary, and the API now hands you both the summary text and the
per-iteration token bill, so you can judge the trade instead of guessing.

## Build proposal

### Layer 1 — Intent

Ship `examples/server-side-compaction/`: a small, honest client for one
compaction round — it validates a `compact_20260112` policy, refuses to spend if
the transcript cannot reach the trigger, makes **one** paused compaction call,
and turns the response into a report that shows (a) the summary the model wrote,
(b) the true per-iteration token bill that `usage.input_tokens` hides, and
(c) the continuation message list that carries the conversation across the
compaction boundary.

**Explicitly out of scope:** the follow-on generation call after the pause (we
build and print the continuation messages, we do not send them); multi-turn or
repeated compaction; streaming (`compaction_delta`); custom `instructions`
beyond accepting/validating the field; `clear_tool_uses_20250919` (already
shipped in `examples/context-editing-preview/`); prompt-cache accounting.

**Directory-name check (2026-08-12):** `ls examples/` on `main` shows 14
directories, none named `server-side-compaction`; `gh pr list --state open` is
empty; `git branch -a` shows only `main` and three merged cycle branches. Name
is free, and it is distinct from the existing `context-editing-preview`.

### Layer 2 — Behavioral spec

**Inputs**
- A synthetic transcript sized in characters, built by a pure deterministic
  helper (reuse the *idea* of `context-editing-preview/transcript.py`; do not
  import across example directories — these are self-contained by design).
- A `CompactionPolicy`: `trigger_tokens`, `pause_after_compaction`,
  optional `instructions`.
- At the shell only: `ANTHROPIC_API_KEY`.

**Outputs**
- The exact `context_management` dict sent, printed for copy-paste.
- A preflight line: counted tokens vs. `trigger_tokens`, and go/no-go.
- A `CompactionReport`: `triggered`, `paused`, `summary` (or an explicit
  "compaction failed, content was null"), `summary_chars`,
  `compaction_input_tokens`, `compaction_output_tokens`,
  `message_input_tokens`, `message_output_tokens`, `total_billed_tokens`, and
  an estimated dollar cost from named per-MTok constants.
- The continuation message list's length and its final block types.

**Invariants**
1. `CompactionPolicy(trigger_tokens=...)` raises `ValueError` at construction
   for `trigger_tokens < 50_000` (documented server floor — make the illegal
   state unrepresentable rather than discovering it as a 400), and for an
   `instructions` string that is empty or whitespace-only (a blank string is not
   "use the default", it *replaces* the default with nothing).
2. `to_edit()` emits `type` and never emits a key whose value is `None`; in
   particular no `"instructions": null`.
3. The core module never imports `anthropic` and never reads an env var. It
   consumes **wire-shaped dicts** (`Mapping[str, object]`), so the shell's only
   job is `response.model_dump(mode="json")` plus printing.
4. `total_billed_tokens` equals the sum over `usage.iterations` when that key is
   present, and the top-level `input_tokens + output_tokens` when it is absent —
   never a mix, and never the top-level numbers when iterations exist.
5. The continuation list equals the original messages plus exactly one appended
   assistant message whose content is the response's content array **verbatim**,
   including `encrypted_content` on the compaction block.

**Failure modes**
- **Compaction block with `content: null`** → report `triggered=True`,
  `summary=None`, and a plain-English "compaction failed to produce a summary;
  the block is a server-side no-op but must still be round-tripped." Must not
  raise, must not slice `None`. *This is the criterion most likely to be got
  wrong — both vendors' example snippets get it wrong.*
- **No compaction block and no `iterations`** → `triggered=False`, zeroed
  compaction counters, and a stated reason (below trigger).
- **Preflight below trigger** → print the shortfall and exit non-zero *before*
  any billed call. Never "try it and see."
- **Missing `ANTHROPIC_API_KEY`** → one line to stderr, exit 1, no network.
- **API error** (unsupported model, unknown beta, 400 on the trigger floor) →
  propagate loudly; never degrade into an empty report.

**Acceptance criteria ("it works")**
- A1. `python3 test_compaction.py` passes with **no API key, no network, and no
  third-party dependency** (stdlib only — the core takes dicts).
- A2. `trigger_tokens=49_999` raises `ValueError`; `50_000` constructs.
  `instructions=""` and `instructions="   "` each raise; `instructions=None`
  constructs. Four separate assertions.
- A3. `CompactionPolicy(trigger_tokens=50_000, pause_after_compaction=True).to_edit()`
  equals exactly
  `{"type": "compact_20260112", "trigger": {"type": "input_tokens", "value": 50000}, "pause_after_compaction": True}`
  — dict equality, so a stray `"instructions": None` fails the test.
- A4. Fed the docs' own usage JSON (`input_tokens: 45000, output_tokens: 1234`,
  iterations `compaction 180000/3500` + `message 23000/1000`), the report gives
  `compaction_input_tokens == 180000` and
  `total_billed_tokens == 180000 + 3500 + 23000 + 1000 == 207500` — i.e. it
  does **not** report 46234.
- A5. Fed a response whose only content block is
  `{"type": "compaction", "content": null, "encrypted_content": "abc"}` with
  `stop_reason: "compaction"`, the report is `triggered=True, paused=True,
  summary=None` and does not raise.
- A6. Fed a response with no compaction block and `usage` lacking `iterations`,
  the report is `triggered=False` with `total_billed_tokens` from the top-level
  fields.
- A7. `continuation_messages(original, response_content)` returns
  `original + [{"role": "assistant", "content": response_content}]` and the
  appended compaction block still carries `encrypted_content` verbatim.
- A8. The preflight decision function returns "no-go" with the shortfall when
  counted tokens ≤ trigger, and "go" when above — asserted without any client.
- A9. `python3 verify_fixture_schema.py` (needs `pip install -r requirements.txt`,
  still no key/network) validates every fixture used in A4–A6 through the real
  SDK: `anthropic.types.beta.BetaMessage.model_validate(fixture)` succeeds, and
  it asserts `"compaction" in typing.get_args(BetaStopReason)`. This is what
  stops the offline tests from being self-congratulatory — the fixtures are
  checked against Anthropic's own schema, not one we invented.
- A10. README documents the live path and the ~$0.15 estimate. Because no
  `ANTHROPIC_API_KEY` exists in this build environment (verified today, same as
  the 2026-08-11 cycle), the live transcript section must say
  **NOT YET CAPTURED** and invent nothing — exactly the precedent set by
  `examples/context-editing-preview/README.md`.
- A11. The README's self-test block is verified by
  `python3 check_transcript.py ../server-side-compaction -- python3 test_compaction.py`
  from `examples/readme-transcript-check/`. Only the deterministic self-test
  output goes under an "Expected output" heading; live output (nondeterministic
  summary text and token counts) must **not** be marked that way.

### Layer 3 — Interfaces (stubs, no bodies)

```python
# policy.py — turns a validated compaction policy into the API's edit dict.
BETA = "compact-2026-01-12"          # NOT in the SDK's beta literal union; see §2(a)
STRATEGY = "compact_20260112"
MIN_TRIGGER_TOKENS = 50_000          # documented server floor

@dataclass(frozen=True)
class CompactionPolicy:
    trigger_tokens: int
    pause_after_compaction: bool = False
    instructions: str | None = None
    def __post_init__(self) -> None: ...      # raises ValueError; invariant 1
    def to_edit(self) -> dict[str, object]: ...
    def to_config(self) -> dict[str, object]: ...   # {"edits": [self.to_edit()]}

# preflight.py — decides whether a transcript can reach the trigger.
@dataclass(frozen=True)
class Preflight:
    counted_tokens: int
    trigger_tokens: int
    @property
    def will_fire(self) -> bool: ...
    @property
    def shortfall(self) -> int: ...           # 0 when it will fire

# report.py — reads a wire-shaped response into a compaction report.
@dataclass(frozen=True)
class CompactionReport:
    triggered: bool
    paused: bool
    summary: str | None                       # None => compaction failed (§2c)
    encrypted_content: str | None
    compaction_input_tokens: int
    compaction_output_tokens: int
    message_input_tokens: int
    message_output_tokens: int
    @property
    def total_billed_tokens(self) -> int: ...

def read_response(response: Mapping[str, object]) -> CompactionReport: ...
def continuation_messages(
    original: Sequence[Mapping[str, object]],
    response_content: Sequence[Mapping[str, object]],
) -> list[dict[str, object]]: ...

# cost.py — pure dollars from tokens.
def estimate_usd(input_tokens: int, output_tokens: int,
                 usd_in_per_mtok: float, usd_out_per_mtok: float) -> float: ...

# transcript.py — deterministic synthetic transcript, sized in characters.
def build_transcript(turns: int, chars_per_turn: int) -> list[dict[str, object]]: ...

# main.py — imperative shell: key, SDK, preflight, one billed call, printing.
MODEL = "claude-sonnet-5"     # Haiku 4.5 does NOT support compaction — see §5
USD_IN_PER_MTOK = 2.0
USD_OUT_PER_MTOK = 10.0
def main() -> int: ...
```

Files: `README.md`, `policy.py`, `preflight.py`, `report.py`, `cost.py`,
`transcript.py`, `main.py`, `test_compaction.py`, `verify_fixture_schema.py`,
`requirements.txt` (`anthropic==0.121.0`).

The shell's `betas=[policy.BETA]` and `context_management=policy.to_config()`
should be asserted by a fake-client test in `test_compaction.py`, following the
`test_adapter_sends_the_beta_and_the_policy_config` pattern already established
in `examples/context-editing-preview/test_preview.py`.

## Open questions

- **What does `usage` look like under `pause_after_compaction: true`?** Every
  documented `iterations` example shows a `compaction` entry followed by a
  `message` entry. When the turn pauses there is no answer iteration, so I
  expect a single `compaction` entry — but I could not verify this, and the
  report must therefore treat a missing `message` iteration as zeros rather
  than an error (A4/A5 encode that).
- **Does `count_tokens` on a paused response's continuation list actually drop
  the pre-compaction content?** The docs say it "applies existing compaction
  blocks," which implies yes, but the only worked example is a normal
  (non-paused) flow. The builder should print both numbers and record what
  really happened; nothing in the offline criteria depends on the answer.
- **Is the 50,000-token floor a 400 or a silent clamp?** Documented as "must be
  at least 50,000 tokens" on both pages, with no stated error behaviour. We
  reject below-floor at construction, so we never find out — which is the point.
- **Does `claude-sonnet-5` use the newer (4.7+) tokenizer?** The pricing page
  says "Claude 4.7 and later models" use it and "Sonnet 4.6 and earlier" do not;
  Sonnet 5 is not named either way. This only affects how much synthetic text
  is needed to clear 50k tokens, which is exactly what the free preflight count
  measures empirically — another reason the preflight is not optional.
- **AWS lists only Sonnet 4.6 and Opus 4.6 as compaction-capable on Bedrock**,
  while the first-party docs list nine models. First-party is the source of
  truth for this example; the discrepancy is worth knowing if anyone ports it.
- No local `claude-api` skill is installed (`~/.claude/skills/` has only
  `graphify`), so as on 2026-08-11 I used the platform docs plus direct reads of
  the installed SDK's generated types as primary sources.

## Sources

- [Compaction — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/compaction) (fetched 2026-08-12)
- [Compaction — Amazon Bedrock User Guide](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-compaction.html) (fetched 2026-08-12; quotes the default summarization prompt and the `usage.iterations` billing warning verbatim)
- [Pricing — Claude Platform Docs](https://platform.claude.com/docs/en/about-claude/pricing) (fetched 2026-08-12; Sonnet 5 $2/$10 now permanent)
- [Token counting — Claude Platform Docs](https://platform.claude.com/docs/en/build-with-claude/token-counting) (via `knowledge/context-editing.md`, verified 2026-08-11)
- `anthropic==0.121.0` generated types, read locally 2026-08-12:
  `types/beta/beta_compact_20260112_edit_param.py`,
  `beta_compaction_block.py`, `beta_compaction_block_param.py`,
  `beta_compaction_content_block_delta.py`,
  `beta_compaction_iteration_usage.py`, `beta_message_iteration_usage.py`,
  `beta_iterations_usage.py`, `beta_usage.py`, `beta_stop_reason.py`,
  `beta_content_block.py`, `beta_context_management_response.py`,
  `beta_context_management_config_param.py`,
  `beta_input_tokens_trigger_param.py`, `types/anthropic_beta_param.py`
- HN via the Algolia API, queried 2026-08-12:
  [Double-buffering for LLM context windows](https://news.ycombinator.com/item?id=47147224) (2026-02-25, 2 points),
  [AGENTS.md outperforms skills in our agent evals](https://news.ycombinator.com/item?id=46809708) (2026-01-29, 524 points)
