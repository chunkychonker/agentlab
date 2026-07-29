# Orchestrator-subagents (plan -> delegate -> synthesize)

An orchestrator that fans out to specialist "subagents", built with nothing
but the plain Anthropic Messages API - three calls, each with a distinct
role, no Claude Agent SDK and no `subagents` feature.

From the research note:
[`research/2026-07-29-orchestrator-subagents.md`](../../research/2026-07-29-orchestrator-subagents.md).

## What's here

| File | What it is |
|------|-----------|
| `agent.py` | `Plan`/`Subtask` pydantic schema, `plan_task`, `run_specialist`, `synthesize`, and `run_orchestrator`, which runs all three stages in order. |
| `test_agent.py` | Offline self-test: the schema's `max_length=3` constraint, all three `plan_task` paths (success, validation error, no text block), `run_specialist`, `synthesize`, and the full 4-call orchestrator sequence. No key, no network. |
| `requirements.txt` | `anthropic>=0.120.2`, `pydantic>=2` - needed for **both** the live run and the self-test. |

## The pattern

1. **Plan** - one `client.messages.parse(model=..., output_format=Plan)` call
   turns the task into a validated `Plan`: a list of 1-3
   `Subtask{specialist, instructions}` objects. `max_length=3` produces a
   pydantic `maxItems: 3` in the JSON Schema, but the Anthropic SDK's
   `transform_schema` step demotes `maxItems` into a description string
   (`"description": "{maxItems: 3}"`) before the request is sent -
   `minItems` survives the transform, `maxItems` does not. So on the wire
   the 3-subtask cap is only a prompt hint; the real enforcement is
   client-side, in `plan_task`, which catches the `pydantic_core.ValidationError`
   raised when the model ignores the hint and returns more than 3 subtasks -
   see `test_plan_task_raises_on_validation_error`.
2. **Delegate** - one `client.messages.create(...)` call per subtask, each
   with `system=f"You are a specialist in {subtask.specialist}..."` and no
   shared conversation history. This is what makes each call an
   independently instructed mini-agent instead of just another turn in one
   conversation. Run sequentially, not in parallel (see below).
3. **Synthesize** - one final `client.messages.create(...)` call whose
   prompt contains the original task plus every specialist's output, asking
   for one combined answer.

`run_orchestrator(client, task)` runs all three stages and returns the final
synthesized text.

## Why `client.messages.parse(output_format=...)` instead of XML parsing

The canonical reference for this pattern, Anthropic's
[Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
(2024-12-19) and its companion
[`orchestrator_workers.ipynb`](https://github.com/anthropics/claude-cookbooks/blob/main/patterns/agents/orchestrator_workers.ipynb)
notebook, implement the plan step by prompting for XML-tagged output
(`<analysis>`, `<tasks><task><type>...`) and hand-rolling a regex/string
parser to pull it back apart. That still works, but it's brittle - malformed
or slightly-off-format output silently breaks the regex.

`client.messages.parse(..., output_format=Plan)` (confirmed against
`anthropic==0.120.2` source, non-beta `client.messages`, no `betas=[...]`
header required - the older `structured-outputs-2025-11-13` beta header is
folded into general availability) replaces that entirely: the SDK builds a
JSON Schema from the `Plan` pydantic model, the API returns output
constrained to that schema, and `response.parsed_output` is either a real
`Plan` instance or `None` on failure - never a string to regex apart. This is
the same idea `examples/typed-tool-registry/`'s `@beta_tool` uses for a
tool's *input* schema, applied here to a whole message's *output*.

## Run the self-test (no API key needed, but does need `anthropic` + `pydantic` installed)

```bash
cd examples/orchestrator-subagents
pip install -r requirements.txt
python test_agent.py
```

Expected output:

```
ok  Plan(subtasks=[...4 items...]) raises pydantic.ValidationError
ok  plan_task returns the parsed Plan from a successful .messages.parse call
ok  plan_task catches pydantic_core.ValidationError from .messages.parse and re-raises RuntimeError
ok  plan_task raises RuntimeError when parsed_output is None (no text block at all)
ok  run_specialist returns scripted text and its system prompt names the specialist
ok  synthesize's prompt contains every specialist's output and the original task
ok  run_orchestrator makes exactly 1 parse + 2 specialist + 1 synthesis call, in order

All 7 self-tests passed.
```

## Run it live (needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python agent.py
```

It asks Claude to plan and write a short launch announcement for a
fictional CLI tool, prints the plan, each specialist's output, and the
final synthesized answer. Without `ANTHROPIC_API_KEY` set, `agent.py` prints
a one-line note and exits 0, same as the other two examples.

Model id is the constant `MODEL` at the top of `agent.py` (default
`claude-haiku-4-5`). See
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

**Open question from the research note:** whether `output_format` is
confirmed supported on `claude-haiku-4-5` via the *direct* Claude API (as
opposed to Bedrock/Vertex, where it's explicitly documented) was not
verified with a live network call. If a live run 400s specifically on
`output_config`, the documented fallback is a forced `tool_choice` call
(`tools=[{"name": "plan", "input_schema": ...}], tool_choice={"type": "tool", "name": "plan"}`)
instead of `output_format` - same idea, older and more broadly supported.

## Explicitly out of scope for today

- **Parallel specialist execution.** `run_orchestrator` calls each
  specialist sequentially, not via `ThreadPoolExecutor`/`asyncio.gather` -
  kept simple for determinism and a straightforward offline test. Natural
  next increment.
- **The Claude Agent SDK's real `subagents`/`AgentDefinition` feature**
  (`claude_agent_sdk`) - a different, heavier product that runs on top of
  the Claude Code CLI runtime, with real context isolation and parallel
  background execution. See the research note for why this cycle didn't use
  it.
- **Managed Agents multiagent orchestration** (beta API product, sandboxed
  filesystem, coordinator + workers) - out of scope for a same-day build.
- **Cross-specialist communication.** Specialists here are strictly
  independent: no shared state, no visibility into each other's work or the
  original task - only the synthesis step sees everything.

## How this differs from the other two examples

[`minimal-agent-loop/`](../minimal-agent-loop/) and
[`typed-tool-registry/`](../typed-tool-registry/) are both single-agent:
one conversation, looping until the model stops asking for tools. This
example is multi-*call*, not multi-*turn*: three separate, independently
scoped Messages-API calls (plan, N specialists, synthesize), each with its
own system prompt and no shared history between the specialist calls - the
"fan out to specialist agents" shape, built from the same primitive
(`client.messages`) the other two examples use.
