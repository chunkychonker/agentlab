# Orchestrator-subagents (plan -> delegate -> synthesize)

An orchestrator that fans out to specialist "subagents", built with nothing
but the plain Anthropic Messages API - three calls, each with a distinct
role, no Claude Agent SDK and no `subagents` feature.

From the research note:
[`research/2026-07-29-orchestrator-subagents.md`](../../research/2026-07-29-orchestrator-subagents.md).

## What's here

| File | What it is |
|------|-----------|
| `agent.py` | `Plan`/`Subtask` pydantic schema, `plan_task`, `run_specialist`, `synthesize`, and two orchestrators over the same three stages: `run_orchestrator` (specialists one after another) and `run_orchestrator_parallel` (specialists at once, via `run_specialists_parallel`). |
| `test_agent.py` | Offline self-test, 12 checks: the schema's `max_length=3` constraint, all three `plan_task` paths (success, validation error, no text block), `run_specialist`, `synthesize`, the full 4-call sequential orchestrator sequence, and the parallel fan-out (calls genuinely overlap, results stay in plan order, call accounting, `max_workers` validation, worker-error propagation). No key, no network. |
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
   conversation. Run one after another by `run_orchestrator`, or all at
   once by `run_orchestrator_parallel` (see below) - the calls are
   independent either way.
3. **Synthesize** - one final `client.messages.create(...)` call whose
   prompt contains the original task plus every specialist's output, asking
   for one combined answer.

`run_orchestrator(client, task)` runs all three stages and returns the final
synthesized text.

## Sequential vs parallel fan-out

Two orchestrators, same three stages, same API calls, same answer - they
differ only in how the delegate step is dispatched:

| | `run_orchestrator` | `run_orchestrator_parallel` |
|---|---|---|
| Delegate step | one `run_specialist` call after another | `run_specialists_parallel`: `ThreadPoolExecutor.map` over `plan.subtasks` |
| Wall-clock for N specialists | sum of N latencies | roughly the slowest single call |
| Token cost | identical | identical |
| Order of `client.messages.create` calls | fixed, safe to assert positionally | completion order - not meaningful |
| Order of results | plan order | plan order |

The whole concurrency story is one stdlib call:
[`Executor.map`](https://docs.python.org/3/library/concurrent.futures.html#concurrent.futures.Executor.map)
**yields results in *input* order**, blocking as needed, no matter which call
finishes first - so "reassemble in plan order rather than completion order"
needs no `future -> index` bookkeeping. `map` also **re-raises a worker's
exception** when iteration reaches that slot, so one failing specialist fails
the whole fan-out loudly instead of quietly returning a short list.
`run_specialists_parallel` rejects `max_workers < 1` with `ValueError` before
any API call, and never opens more threads than `MAX_PARALLEL_SPECIALISTS`
(4 - the `Plan` schema caps `subtasks` at 3, so that is one of headroom).

`run_orchestrator` stays as-is on purpose (expand/contract): it is the
deterministic baseline, and
`test_run_orchestrator_makes_exactly_four_calls_in_order` unpacks
`create_calls` positionally, which is only valid for a serial implementation.
The five new tests assert on the *count and contents* of `create_calls`, never
their positions, and `test_specialists_run_concurrently_not_serially` blocks
every scripted call on a `threading.Barrier(3)` with a 5-second timeout - a
serial loop leaves that barrier one party short and dies with
`BrokenBarrierError`, so the test proves overlap rather than just observing
that three calls happened.

**This buys latency, not money.** The fan-out still makes the same plan + N
specialist + synthesize calls; nothing about it reduces tokens. Anthropic's own
multi-agent research system (orchestrator plus 3-5 parallel subagents) costs
roughly 15x the tokens of a single chat -
[How we built our multi-agent research system](https://www.anthropic.com/engineering/built-multi-agent-research-system).

### One sync client, shared across the pool

All the workers share the single `anthropic.Anthropic` instance created in
`main()`. That is deliberate: the sync client wraps **one** pooled HTTP client
(`httpx2` in the 1.x SDK) whose default
[`max_connections` is 100](https://www.python-httpx.org/advanced/resource-limits/),
so three concurrent specialist calls are nowhere near the pool limit, and the
SDK's automatic retries (2 by default, covering 408/409/429/5xx) are per-call
and unaffected by concurrency. Caveat worth stating: the SDK docs do not
contain a single sentence promising "the sync client is thread-safe" - the
evidence is the shared pooled client, the `copy()`/`with_options()`
"thread-safe usage patterns" language, and the SDK's own `to_thread`/`asyncify`
helpers. If a live run ever shows connection errors under fan-out, the fallback
is a `client.with_options()` copy per worker, or `max_workers=1` (which
degenerates to the sequential path).

### Prompt caching and parallel fan-out

These specialist calls **share no prefix** - each has its own `system` string
and its own single user message - so prompt caching is not in play here at all,
and fanning them out costs nothing extra. That is worth saying explicitly,
because the moment a fan-out's calls *do* share a large common prefix (same big
system prompt, same tools, same shared context block), dispatching them
concurrently is a cache anti-pattern. From Anthropic's
[prompt caching docs](https://platform.claude.com/docs/en/build-with-claude/prompt-caching):

> For concurrent requests, note that a cache entry only becomes available after
> the first response begins. If you need cache hits for parallel requests, wait
> for the first response before sending subsequent requests.

So N parallel calls sharing a prefix, all fired before any of them returns, pay
**N cache writes (1.25x base input) and zero reads**. Serialized, the same work
is 1 write plus N-1 reads at 0.10x. The fix is to let the first call return
before firing the rest - i.e. exactly the latency you were trying to avoid, so
pick one. More cache-killers in
[`knowledge/prompt-caching.md`](../../knowledge/prompt-caching.md).

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
constrained to that schema, and `response.parsed_output` is a real `Plan`
instance - never a string to regex apart. It's only `None` when the response
has no text block at all (e.g. a refusal); a schema validation failure is a
separate case that raises `pydantic_core.ValidationError` out of `.parse()`
itself, caught below in `plan_task` - see the "Two distinct failure modes"
note in `agent.py`'s docstring for both.

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
ok  run_specialists_parallel's calls are in flight together (barrier of 3 releases)
ok  results stay in plan order when the last subtask's call returns first
ok  run_orchestrator_parallel makes 1 parse + 3 specialist + 1 synthesis call
ok  run_specialists_parallel(max_workers=0) raises ValueError before any API call
ok  a specialist's exception propagates out of run_specialists_parallel

All 12 self-tests passed.
```

## Run it live (needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python agent.py
```

It asks Claude to plan and write a short launch announcement for a
fictional CLI tool, then prints the plan, the wall-clock seconds the parallel
fan-out took, each specialist's output, and the final synthesized answer.
`main()` uses `run_specialists_parallel`, so with three specialists that
`fan-out took N.NNs` line should read roughly one specialist's latency rather
than three. No live transcript is committed here - the repo's checks all run
without a key, so the only numbers verified in-repo are the offline self-test's.
Without `ANTHROPIC_API_KEY` set, `agent.py` prints a one-line note and exits 0,
same as the other two examples.

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

- **Async (`AsyncAnthropic` / `asyncio.gather`).** The module is synchronous
  top to bottom, so a thread pool is the small change; going async would mean
  `async def` on every helper and an async fake client in the test, for no
  benefit at N <= 3 calls.
- **Making the parallel path the default.** `run_orchestrator` stays the
  sequential baseline and keeps its positional-order test; flipping the default
  is a later expand/contract step, not this one.
- **A second timeout layer around the fan-out.** `ThreadPoolExecutor.map` takes
  a `timeout`, but the SDK already imposes a per-request timeout (10 minutes by
  default) and its own retries; stacking another one is its own topic (cf.
  [`examples/tool-error-policy/`](../tool-error-policy/)).
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
