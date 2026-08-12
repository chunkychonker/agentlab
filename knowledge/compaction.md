# Server-side compaction (`compact_20260112`)

The **summarize** sibling of [[context-editing]]'s **prune**. Same
`context_management` parameter, different beta, different response shape,
different failure modes, and — unlike clearing — it costs an extra sampling
iteration you get billed for.

Verified 2026-08-12 against the
[compaction docs](https://platform.claude.com/docs/en/build-with-claude/compaction),
the [Bedrock compaction page](https://docs.aws.amazon.com/bedrock/latest/userguide/claude-messages-compaction.html)
(more explicit on billing and the default prompt), and the generated types in
`anthropic==0.121.0`.

## The edit

```
type                    Required  Literal["compact_20260112"]
trigger                           {"type": "input_tokens", "value": int} | None   # default 150_000
pause_after_compaction            bool                                            # default False
instructions                      str | None                                      # default None
```

- Beta header: `compact-2026-01-12` (**not** `context-management-2025-06-27`).
- `trigger` supports **only** `input_tokens`, and the value **must be ≥ 50,000**.
  There is no `tool_uses` trigger — the cheap deterministic firing trick from
  [[context-editing]] does not transfer.
- `instructions` **replaces** the default summarization prompt entirely. The
  default (quoted verbatim only on the Bedrock page) ends "You must wrap your
  summary in a `<summary></summary>` block", so a custom prompt silently drops
  that contract too. A blank string is not "use the default".
- `pause_after_compaction: true` returns as soon as the summary exists, with
  `stop_reason: "compaction"` and only the compaction block in `content`. Append
  it and re-request to get the real answer. This is the cheap way to *inspect* a
  summary: one iteration instead of two.

## Four gotchas from SDK source, not docs prose

1. **`"compact-2026-01-12"` is not in the SDK's beta literal union**
   (`types/anthropic_beta_param.py`, 0.121.0 — `context-management-2025-06-27`
   is). The alias is `Union[str, Literal[...]]`, so a typo type-checks fine and
   fails at the server. The reassurance recorded in [[context-editing]] ("a typo
   is a type error") does **not** apply here. Pin it in one constant.
2. **`applied_edits` never mentions compaction.** `BetaContextManagementResponse.
   applied_edits` unions only the two *clear* edit responses. Detect compaction
   from a `{"type": "compaction"}` block in `content`, `stop_reason ==
   "compaction"`, or a `usage.iterations` entry with `type: "compaction"`.
   (`"compaction"` *is* in the `BetaStopReason` literal, but the docstring on
   `BetaMessage.stop_reason` omits it.)
3. **A compaction block can have `content: null` — that means it failed.**
   SDK docstring: "the compaction failed to produce a valid summary (e.g.,
   malformed output from the model). Clients may round-trip compaction blocks
   with null content; the server treats them as no-ops." Empty string is not
   allowed. Both Anthropic's and AWS's example snippets do
   `block['content'][:200]` and would crash on it.
4. **`encrypted_content` must be round-tripped verbatim** ("opaque metadata from
   prior compaction"). Round-tripping the whole `content` array preserves it;
   hand-rebuilding the block loses it silently.

## Billing: top-level `usage` becomes a lie

> The top-level `input_tokens` and `output_tokens` ... do not include compaction
> iteration usage ... To calculate the total tokens consumed and billed, sum
> across all entries in the `usage.iterations` array.

The documented example: top-level `input_tokens: 45000`, while the compaction
iteration alone was `180000` in / `3500` out. Any cost tracker written before
compaction under-reports by the whole compaction iteration.

`BetaUsage.iterations` is `Optional[List[...]]`, discriminated on `type`
(`"message"` | `"compaction"` | advisor | fallback). It is **present only when a
new compaction fires**; re-applying an existing compaction block is free and
leaves the top-level numbers accurate. So `iterations is None` is a legal,
meaningful state — not an error.

## Continuation

Append the response (compaction block included) to your messages; **the server
drops every block before the compaction block** on the next request. Your client
keeps full history and does not sync — same property as [[context-editing]], and
the same corollary: you cannot observe the effect by printing your local list.
Multiple compactions can accumulate; the last block reflects the final state.

## Models and cost

Supported (first-party, 2026-08-12): `claude-fable-5`, `claude-mythos-5`,
`claude-mythos-preview`, `claude-opus-5`, `claude-opus-4-8`, `claude-opus-4-7`,
`claude-opus-4-6`, `claude-sonnet-5`, `claude-sonnet-4-6`.
**`claude-haiku-4-5` is not supported**, so the repo's usual cheap default from
[[anthropic-models]] does not apply — `claude-sonnet-5` ($2/$10 per MTok) is the
cheapest option. Bedrock lists only Sonnet 4.6 and Opus 4.6.

## Measurement: half of the free trick survives

`count_tokens` **applies existing compaction blocks but does not trigger new
ones**. So there is no $0 "what would compaction save me" preview — the summary
has to be generated. What is still free:

- *Before:* count the transcript to confirm it exceeds `trigger.value`, so you
  never pay for a call that cannot fire.
- *After:* count `messages + [compaction response]` with the same edit;
  `input_tokens` is the effective size and
  `context_management.original_input_tokens` the size before. Achieved saving,
  for free.

## The standing criticism

Compaction is *stop-the-world and lossy*: an extra sampling iteration the user
waits through, after which the agent resumes from a summary. `pause_after_compaction`
makes that step explicit rather than removing it. Practitioner complaints
(see the 2026-08-12 research note) target summarization generally, not this API
specifically — there is no substantial HN thread about `compact_20260112` itself.

Related: [[context-editing]], [[tool-use-loop]], [[anthropic-models]],
[[anthropic-python-sdk]]
