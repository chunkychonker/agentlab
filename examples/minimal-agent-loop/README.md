# Minimal agent loop (one tool, by hand)

The smallest honest "agent": define **one** tool, let Claude decide to call it,
run it yourself, feed the result back, and loop until Claude answers. No
framework, and deliberately **not** the SDK's built-in Tool Runner — the whole
point is to write the tool-use loop by hand so the mechanics are visible.

From the research note:
[`research/2026-07-27-minimal-agent-loop.md`](../../research/2026-07-27-minimal-agent-loop.md).

## What's here

| File | What it is |
|------|-----------|
| `agent.py` | The tool (`calculator`), its schema, and `run_agent()` — the manual loop. |
| `test_agent.py` | Offline self-test: tool unit tests + a scripted fake-client loop test. No key, no network. |
| `requirements.txt` | `anthropic` — only needed for the live run. |

The one tool is a **safe** calculator: it walks an `ast` tree and only allows
numbers, `+ - * / // % **`, and parentheses. It is not `eval` — input like
`__import__('os')` returns an error string and never executes.

## Run the self-test (no API key needed)

```bash
cd examples/minimal-agent-loop
python test_agent.py
```

Expected output:

```
ok  calculator evaluates arithmetic correctly
ok  calculator safely rejects forbidden/malformed input
ok  manual loop dispatches the tool and returns the final answer
ok  loop enforces max_turns and raises when exceeded

All 4 self-tests passed.
```

The loop test injects a fake client that returns a scripted `tool_use` response
then an `end_turn` response, and asserts the loop (a) ran the tool on the
scripted input, (b) sent back a `tool_result` whose `tool_use_id` matches the
`tool_use` block, and (c) returned the final text — proving the mechanics
deterministically and offline.

## Run it live (needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python agent.py
```

It asks *"What is 4839 * 1284, and is that more than five million?"*, prints the
calculator being called, then the final answer. Without `ANTHROPIC_API_KEY` set,
`agent.py` prints a one-line note and exits 0 — it never crashes.

Model id is the constant `MODEL` at the top of `agent.py` (default
`claude-haiku-4-5`, the cheapest current model — switch tiers in one line). See
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## The loop, in one paragraph

Call `messages.create(..., tools=TOOLS, messages=messages)`. If
`stop_reason == "tool_use"`, echo the assistant turn back into `messages`, run
each `tool_use` block through a `{name: fn}` registry, append a user turn of
`tool_result` blocks (each `tool_use_id` matching the block it answers), and call
again. Repeat until `stop_reason` is no longer `"tool_use"`, then return the
concatenated `text`. A `max_turns` cap stops runaway loops.

## Scope

Exactly one tool, one loop, one test file. No retries, no multi-tool, no registry
abstraction beyond a dict — those are separate backlog items.
