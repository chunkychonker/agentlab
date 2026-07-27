# Minimal agent loop from scratch (Anthropic SDK): one tool, manual tool-use loop

**Question** — What is the smallest honest "agent" you can build by hand with the
current Anthropic Python SDK: define one tool, let Claude decide to call it, run
it, feed the result back, and loop until Claude answers — no framework?

## Findings

### The loop, from the official docs

The Messages API tool-use round trip is fully specified in the tool-use overview
(fetched 2026-07-27, [platform.claude.com/docs/en/docs/build-with-claude/tool-use/overview](https://platform.claude.com/docs/en/docs/build-with-claude/tool-use/overview)).
The mechanics for a **client tool** (a function you define and execute yourself):

1. Define each tool as a plain dict: `name`, `description`, and an
   `input_schema` that is a JSON Schema object (`type: "object"`, `properties`,
   `required`). Description quality drives whether Claude picks the tool.
2. Call `client.messages.create(model=..., max_tokens=..., tools=tools, messages=messages)`.
3. If the response has `stop_reason == "tool_use"`, its `content` list contains
   one or more `tool_use` blocks, each with `.id`, `.name`, and `.input` (already
   parsed to a dict).
4. Run the tool. Append the assistant turn (`{"role": "assistant", "content":
   response.content}`) and then a user turn whose content is a list of
   `tool_result` blocks: `{"type": "tool_result", "tool_use_id": <the id>,
   "content": <string result>}`.
5. Call `create` again with the extended `messages`. Repeat until `stop_reason`
   is no longer `"tool_use"` (typically `"end_turn"`), then read the final `text`
   block. The docs show exactly this two-request round trip in Python.

Key correctness points the docs call out:
- The `tool_use_id` in your `tool_result` **must** match the `id` of the
  `tool_use` block you're answering.
- You must echo the assistant's `tool_use` turn back into `messages` before the
  `tool_result`, or the API rejects it (dangling tool_result).
- `tool_choice` defaults to `{"type": "auto"}` — Claude decides each turn. To
  force at most one tool call per turn, set
  `{"type": "auto", "disable_parallel_tool_use": true}`; to *require* a tool, use
  `{"type": "tool", "name": ...}` or `{"type": "any"}`. (Same doc, "When Claude
  uses tools".)
- The SDK also ships a higher-level `Tool Runner` that automates this loop — we
  deliberately do **not** use it here; the point of this increment is to write
  the loop by hand.

### Current SDK and models (verified 2026-07-27)

- **Package**: `anthropic` on PyPI, latest **0.120.0**, released 2026-07-24,
  requires Python >=3.9 ([pypi.org/pypi/anthropic](https://pypi.org/project/anthropic/)).
- **Model IDs** from the models overview
  ([platform.claude.com/docs/en/about-claude/models/overview](https://platform.claude.com/docs/en/about-claude/models/overview),
  fetched 2026-07-27). Every ID is a pinned snapshot; 4.6-generation and later
  use a dateless-but-still-pinned format:
  - Claude Haiku 4.5 — ID `claude-haiku-4-5-20251001`, alias `claude-haiku-4-5`, **$1 / $5** per MTok in/out. Fastest, cheapest.
  - Claude Sonnet 5 — `claude-sonnet-5`, $3 / $15 ($2 / $10 intro through 2026-08-31).
  - Claude Opus 5 — `claude-opus-5`, $5 / $25.
  - (Fable 5 `claude-fable-5` is the top tier at $10 / $50 — overkill here.)
- **Recommendation for this example**: `claude-haiku-4-5` — cheapest, fast, and
  more than capable of a single-tool loop. Any current model works; make the
  model id a constant at the top of the file so it's a one-line change.

The auth env var is `ANTHROPIC_API_KEY`; `anthropic.Anthropic()` reads it
automatically (docs Python examples construct the client with no args).

### Why a calculator tool

Pick a tool that is (a) pure and self-contained (no network, deterministic,
nothing to key or rate-limit) and (b) something the model plausibly reaches for.
A `calculator(expression)` that evaluates a safe arithmetic expression fits: with
a large multiplication and a system nudge ("use the calculator for any
arithmetic"), Claude reliably calls it, and the result is checkable. This keeps
the whole increment offline-friendly except for the one real API call.

## Build proposal

**What**: `examples/minimal-agent-loop/` — a hand-written single-tool agent loop
in one Python file, plus a README and a self-test that needs no API key.

**Shape** (`agent.py`):
- A `calculator(expression: str) -> str` function using a **safe** evaluator
  (walk an `ast.parse(..., mode="eval")` tree allowing only numbers and
  `+ - * / // % **` and parentheses — **not** bare `eval`). Return the result as
  a string, or an error string on bad input.
- A `TOOLS` list with the one tool schema (name `calculator`, description, and
  `input_schema` requiring a string `expression`).
- `run_agent(client, user_message, *, max_turns=5) -> str`: the manual loop from
  the Findings — call `messages.create`, while `stop_reason == "tool_use"`
  dispatch every `tool_use` block through a `{name: fn}` registry, append the
  assistant turn and the `tool_result` turn, and re-call. Cap iterations at
  `max_turns` and raise if exceeded. Return the concatenated final `text`.
- `__main__`: if `ANTHROPIC_API_KEY` is set, construct `anthropic.Anthropic()`,
  ask e.g. *"What is 4839 * 1284, and is that more than five million?"*, print
  each tool call and the final answer. If the key is absent, print a one-line
  note and exit 0 (don't crash).

**Self-test** (`test_agent.py`, runnable with `python test_agent.py`, no key):
1. **Tool unit tests** — `calculator("4839 * 1284")` returns `"6213276"`;
   a malformed/forbidden expression (e.g. `"__import__('os')"`) returns an error
   string rather than raising or executing.
2. **Loop test with a fake client** — inject a stub whose `.messages.create`
   returns a scripted sequence: first a message with `stop_reason="tool_use"`
   carrying a `calculator` `tool_use` block, then a message with
   `stop_reason="end_turn"` and a text block. Assert the loop (a) invoked
   `calculator` with the scripted input, (b) sent back a `tool_result` whose
   `tool_use_id` matches the block id, and (c) returned the final text. This
   proves the manual loop mechanics deterministically and offline.

"It works" = both self-tests pass with no network, and, when a real key is
present, `python agent.py` prints the calculator being called and a correct
final answer.

**Scope guard**: exactly one tool, one file of logic, one test file. No registry
abstraction beyond a dict, no retries (that's a separate backlog item —
"Tool-use error handling and retries"), no multi-tool (also separate). README
links back to this note.

## Open questions

- Does the pipeline (reviewer step) have `ANTHROPIC_API_KEY` available? The design
  above deliberately doesn't depend on it — the self-test is fully offline via the
  fake client — so review passes either way. Worth the builder confirming the key
  situation and noting it in the README.
- The overview's inline examples use `claude-opus-5`; I'm recommending
  `claude-haiku-4-5` purely for cost/speed. Both are current and valid; not a
  correctness question, just a default.
