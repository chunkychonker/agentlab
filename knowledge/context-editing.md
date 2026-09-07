# Server-side context editing (`clear_tool_uses_20250919`)

The knob for keeping a long tool loop's transcript inside the context budget
*without* touching your own message list. Distinct from client-side pruning and
from summarizing compaction. Companion to [[tool-use-loop]] (the loop that
grows the transcript in the first place).

Verified 2026-08-11 against the
[context-editing docs](https://platform.claude.com/docs/en/build-with-claude/context-editing)
and the generated types in `anthropic==0.121.0`.

## Three strategies, two betas

`context_management` is `{"edits": [ ... ]}` and the SDK's
`BetaContextManagementConfigParam` accepts three edit types:

| Strategy | Beta | Behaviour |
|---|---|---|
| `clear_tool_uses_20250919` | `context-management-2025-06-27` | prunes old `tool_result` bodies |
| `clear_thinking_20251015` | `context-management-2025-06-27` | prunes old `thinking` blocks |
| `compact_20260112` | `compact-2026-01-12` | **summarizes** (different beta, different response shape) |

Pruning ≠ compaction. Clearing removes content and leaves a placeholder;
compaction replaces it with a summary. The quality-degradation and
hallucinated-reference complaints practitioners raise are aimed at
summarization, not at clearing. Compaction has its own note: [[compaction]] —
and note that two reassurances below do **not** transfer to it (its beta string
is absent from the SDK literal union, and it never appears in `applied_edits`).

## `clear_tool_uses_20250919` fields (SDK source, not docs prose)

```
type            Required  Literal["clear_tool_uses_20250919"]
trigger                   {"type": "input_tokens"|"tool_uses", "value": int}
keep                      {"type": "tool_uses", "value": int}          # default 3
clear_at_least            {"type": "input_tokens", "value": int} | None
exclude_tools             Sequence[str] | None
clear_tool_inputs         bool | Sequence[str] | None
```

- `trigger` has **two** shapes. `input_tokens` defaults to 100k;
  `{"type": "tool_uses", "value": N}` fires on a count of tool uses and is far
  easier to trigger deterministically in a test or demo.
- `clear_tool_inputs` is **not** a plain bool — it is
  `Union[bool, SequenceNotStr[str], None]`; a list names specific tools whose
  inputs to clear.
- The beta string is a literal member of the SDK's `AnthropicBetaParam` union,
  so a typo is a type error rather than a silent no-op.

## Semantics worth writing down

- **Pairing survives by default.** Only `tool_result` bodies are cleared; the
  preceding `tool_use` block stays, so the model keeps the record that it made
  the call and with what input. `clear_tool_inputs: true` removes the inputs too.
- **`clear_at_least` is all-or-nothing.** "If the API can't clear at least the
  specified amount, the strategy will not be applied." An over-ambitious value
  yields *zero* savings, not partial savings.
- **Clearing invalidates the cached prompt prefix.** That is the entire reason
  `clear_at_least` exists — clear enough to be worth the cache write. This is
  the most common practitioner criticism of the feature, and it means the
  token saving is not the whole cost story. See [[prompt-caching]] for the
  other side of this tension.
  - **Only the `messages`-level cache is lost.** Prefix reuse is ordered
    `tools` → `system` → `messages`; `clear_tool_uses` edits only `messages`,
    so the `tools` and `system` breakpoints still hit on the clearing turn.
    `cache_read_input_tokens` on that turn ≈ tools+system size;
    `cache_creation_input_tokens` ≈ the surviving message prefix re-written.
  - **Payback, from the documented 5-min multipliers** (write `1.25×`, read
    `0.10×` — [[prompt-caching]]): with `removed` = tokens the clear deletes and
    `rewritten` = `cache_creation_input_tokens` on the clearing turn, the
    one-time invalidation cost ≈ `rewritten × 1.15 × base` and each later turn
    saves ≈ `removed × 0.10 × base`, so **`payback_turns ≈ 11.5 × rewritten /
    removed`**. `clear_at_least` is the knob that keeps `removed ≫ rewritten`
    and payback near 1–2 turns; too low and it stretches to ~10. Derived from
    docs, not yet measured — `examples/context-editing-cache-tradeoff/`
    (proposed 2026-09-07) is the increment that confirms it, and the clearing
    turn's counters also settle a third-party claim that clearing runs "after
    cache lookup, without destroying the prefix" (every Anthropic source says
    it does).
  - The [tool-use context-engineering cookbook](https://platform.claude.com/cookbook/tool-use-context-engineering-context-engineering-tools)
    (2026-03-20) has concrete clearing magnitudes (a message list `~128,740 →
    ~43,060` tokens for `keep=1`; firings freeing `~163,817` tokens each) but
    **no** payback calculation.
- **Your client keeps the full history.** The edit is per-request and
  server-side; you do not sync local state to it. Corollary: you cannot inspect
  the effect by printing your own messages list — you have to ask the API.

## The free measurement path, and its asymmetry

`count_tokens` accepts `context_management`, and
[token counting is free](https://platform.claude.com/docs/en/build-with-claude/token-counting)
("free to use", with rate limits *independent* of message-creation limits). So
you can preview the exact saving for a real transcript at $0, before any billed
generation.

**But the two endpoints return different shapes:**

| Endpoint | Response type | Fields |
|---|---|---|
| `beta.messages.count_tokens` | `BetaCountTokensContextManagementResponse` | `original_input_tokens` **only** |
| `beta.messages.create` | `BetaContextManagementResponse` | `applied_edits: [...]` with `cleared_tool_uses`, `cleared_input_tokens` |

On the free path you get the saving by subtracting `input_tokens` from
`original_input_tokens`; **`applied_edits` does not exist there**. Reaching for
it is the easy mistake. The field is `Optional[...] = None` on both, so "no edit
applied" (below trigger, or `clear_at_least` unmet) arrives as `None` — handle
it as zero savings, never as a negative number or a crash.

Also: `count_tokens` deliberately does not use caching logic, so a preview
measures raw prefix size and won't flatter you with cache hits.

## Gotcha: the model changes the number

`count_tokens` counts under the tokenizer of the `model` you pass, and Claude
4.7+ models use a newer tokenizer producing ~30% more tokens for the same text
(see [[anthropic-models]]). Preview against the model you will actually run.

## Pattern

Keep the policy a frozen, self-validating value type with a `to_edit()`
serializer, and let the imperative shell adapt the SDK response into a small
core-owned type — the same functional-core/imperative-shell split used by
[[tool-failure-taxonomy]]'s retry policy. Then the whole thing is testable
offline with a fake counter, and the endpoint asymmetry above is absorbed in
exactly one adapter function.

Related: [[tool-use-loop]], [[anthropic-python-sdk]], [[anthropic-models]],
[[tool-failure-taxonomy]], [[prompt-caching]]
