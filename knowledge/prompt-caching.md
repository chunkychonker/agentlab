# Prompt caching in a growing tool loop

Where `cache_control` breakpoints go in a Messages API request whose `messages`
array grows every turn, and how to prove the saving. Companion to
[[tool-use-loop]] (the loop that grows the transcript) and [[context-editing]]
(the feature that fights caching — see the tension at the bottom).

Verified 2026-08-29 against the
[prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching)
and
[tool use with prompt caching docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/tool-use-with-prompt-caching).
No beta header and no `client.beta` namespace — basic 5-minute caching is GA on
plain `client.messages.create`. The `ttl: "1h"` option is now also GA (no beta).

## Mechanics that drive placement

- **Prefix reuse, in the order `tools` → `system` → `messages`.** A change at one
  level invalidates that level and everything after it. Any tool-definition edit
  invalidates the whole cache.
- **A breakpoint is `"cache_control": {"type": "ephemeral"}` on a content
  block.** The cache entry is a hash of the whole prefix up to and including that
  block. Put it on the **last block whose prefix is identical across requests**.
- **Cap: 4 `cache_control` breakpoints per request.** (Both docs pages.)
- **20-block lookback per breakpoint.** From a breakpoint the API searches back
  at most 20 blocks for a prior cache entry to extend; past that it writes fresh.
- **Minimum cacheable prefix** (docs table, 2026-08-29): Opus 5 / Fable 5 = 512;
  Opus 4.8 / **Sonnet 5** / Sonnet 4.6 / 4.5 = **1,024**; **Haiku 4.5 = 4,096**;
  Haiku 3.5 = 2,048. Below the floor the request runs **without caching and with
  no error**. (A third-party blog said 2,048 for Sonnet 4.6 — looks stale; trust
  the docs table, and build a prefix well clear of both.)
- **TTL lifetime starts at request *start*, and generation time counts against
  it.** A slow streamed turn eats the window; a >5-min human pause evicts it.

## Breakpoint layout for a hand-written tool loop

Each iteration appends `assistant`(tool_use) then `user`(tool_result), then calls
again. Four slots, best used as:

1. **Tools** — `cache_control` on the *last tool* in the `tools` array. Static.
2. **System** — `cache_control` on the *last `system` block*. `system` must be a
   **list of blocks**, not a bare string, to carry it. Static.
3. **Rolling** — `cache_control` on the last block of the *previous* (now frozen)
   turn. Moves forward each turn. Write on the request that sets it; read on the
   next. This is the one that makes an N-turn loop pay the prefix ~once.
4. **Anchor** — once history exceeds the 20-block lookback, a lone rolling
   breakpoint can't "see" the head to extend it; keep a second breakpoint near
   the head so the head stays cached. Below 20 blocks the 4th slot is wasted.

Server tools (web search, code execution) get an **automatic** breakpoint on the
tool result before the next iteration — but only if the request already has ≥1
`cache_control` marker, and always at the 5-minute TTL. Client tools get none;
you place them. Top-level `cache_control=` on `messages.create` ("automatic
caching") moves one breakpoint to the last cacheable block for you and uses one
slot — SDK kwarg shape unverified against `anthropic==1.2.0`; explicit block
markers are the safe, long-stable choice.

## Proving it — `usage` fields

- `cache_creation_input_tokens` — tokens **written** to a new entry.
- `cache_read_input_tokens` — tokens **read** from cache this request.
- `input_tokens` — only tokens **after the last breakpoint**, not the whole
  prompt.
- Identity: `total_input = cache_read + cache_creation + input_tokens`.
- 1-hour TTL adds nested `cache_creation.{ephemeral_5m_input_tokens,
  ephemeral_1h_input_tokens}`.

Turn 1 writes (`creation > 0`, `read == 0`); turn 2 within the TTL, same
tools+system, grown messages, reads it back (`read ≈ turn-1 creation`).

**Pricing multipliers on base input rate:** 5-min write **1.25×**, 1-h write 2×,
read (either) **0.10×**. A re-read costs 10% of fresh; the write is a one-time
25% premium — 5-min caching breaks even in under two reads.

**Cannot be previewed for $0.** `count_tokens` runs no caching logic and returns
no cache fields (see [[context-editing]]); you need one real generation pair.
Contrast [[context-editing]], which *can* be previewed free because its effect
shows up in the input prefix `count_tokens` does reproduce.

## Cache-killers seen in the wild

Timestamp / "current date" / random id in the system prompt (every request a
fresh write, zero reads); tool reordering (order is in the hash — beware
frameworks that sort tools or iterate a dict); model alias vs dated snapshot are
separate namespaces; >5-min pause evicts the 5-min entry; toggling `tool_choice`,
`disable_parallel_tool_use`, or images present/absent invalidates the messages
cache. Thinking and `output_config.effort` parameters are the expensive ones in
that list: they are rendered into the prompt, so changing either *always*
invalidates messages and, on models that render the config ahead of them, the
tools and system caches as well - budget for a full rebuild unless you have
measured your model.

## Tension with context editing

[[context-editing]] clears old `tool_result` bodies to stay in budget — which
**modifies the messages prefix and invalidates the cache from the edit point
forward**. That is exactly why `clear_at_least` exists (clear enough that the
forced re-write pays for itself). In a long loop the two features pull against
each other and the lab does not yet measure the net effect.

Related: [[tool-use-loop]], [[context-editing]], [[anthropic-models]],
[[anthropic-python-sdk]]
