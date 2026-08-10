# Tool failure taxonomy: retry / report / abort

When a tool call fails inside an agent loop there are **three** correct responses,
and conflating them is the usual bug. The layer that owns each is different.

| Class | Example | Owner | Why |
|---|---|---|---|
| **Transient** | connection reset, upstream 503, upstream 429 | your loop, locally, bounded backoff | The model can't help. A model turn spent saying "try again" costs tokens and latency for a decision needing neither. |
| **Recoverable-by-model** | bad argument, not found, ambiguous query, unknown tool name | the model, via `is_error: true` | Only the model can choose different arguments or a different tool. |
| **Terminal** | auth/permission failure, budget exhausted, model repeating an identical already-failed call | your loop, by aborting loudly | Neither retrying nor re-prompting can succeed; continuing only spends money. |

The third row is the one people skip. It's the documented real-world blowup:
an agent whose tool timed out and that then retried ~400 times in five minutes
([ODSC, 2026-07](https://opendatascience.com/the-3-loops-that-break-ai-agents-in-production/) —
anecdote, not measurement). The default shapes make it easy to reach: the SDK
runner feeds *every* tool exception back to the model, and `max_iterations`
defaults to unbounded (see [[typed-tool-registry]]).

## The `is_error` wire contract

```json
{"type": "tool_result", "tool_use_id": "toolu_…",
 "content": "ConnectionError: weather service unavailable (HTTP 500)",
 "is_error": true}
```

- `is_error` and `content` are both **optional**; absent `is_error` means success.
- tool_result blocks must come **first** in the user message's content array —
  text before them is a 400.
- Write *instructive* messages, not `"failed"`: docs' own example is
  `"Rate limit exceeded. Retry after 60 seconds."` Error text is prompt surface;
  it's what the model uses to self-correct.
- Corollary: error text interpolated from a third party is untrusted input and an
  indirect prompt-injection surface.
- Server tools (web_search etc.) handle their own errors — never emit `is_error`
  for them.
- Docs claim Claude retries an invalid tool call "2-3 times with corrections"
  before giving up. Unverified by live measurement.

## Prevention beats recovery for one whole class

`"strict": true` on a tool definition grammar-constrains sampling so tool `input`
always matches `input_schema` and tool `name` is always valid — deleting the
missing-parameter / wrong-type / unknown-name class rather than handling it.
Caveats: only a JSON Schema subset, compiled schemas cached ≤24h, no PHI in
schema property names / enums / patterns. No per-model support matrix is
published (all doc examples use `claude-opus-5`), so confirm before assuming it
works on a cheap model.

Sources: [Handle tool calls](https://platform.claude.com/docs/en/agents-and-tools/tool-use/handle-tool-calls),
[Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)
(both fetched 2026-08-10);
[Writing effective tools for agents](https://www.anthropic.com/engineering/writing-tools-for-agents) (2025-09-11).

Related: [[sdk-retry-behavior]] (the *other* retry layer), [[tool-use-loop]],
[[typed-tool-registry]]
