# Subagent delegation: an orchestrator that fans out to specialist agents

## Question

What's the smallest, real way to build an "orchestrator that delegates to
specialist subagents" using the plain Anthropic Messages API — consistent with
this repo's existing `examples/minimal-agent-loop/` and
`examples/typed-tool-registry/` (hand-rolled/`anthropic` package only, no extra
runtime) — rather than reaching for the heavier Claude Agent SDK product?

## Findings

### There are (at least) three distinct "subagent" products in the Anthropic ecosystem today — they are not interchangeable

1. **The classic orchestrator-workers *workflow pattern*** from Anthropic's
   [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
   (published **2024-12-19** — over a year old, flagged as foundational/stale-dated
   but still the canonical reference; the pattern itself, not the code, is what's
   durable). "A central LLM dynamically breaks down tasks, delegates them to
   worker LLMs, and synthesizes their results." Implemented with nothing but the
   Messages API — no special SDK. The companion notebook
   [`patterns/agents/orchestrator_workers.ipynb`](https://github.com/anthropics/claude-cookbooks/blob/main/patterns/agents/orchestrator_workers.ipynb)
   (last touched **2026-02-17**, per its GitHub commit history — a docs-only
   model-id refresh, so still current) implements the orchestrator step by
   prompting for **XML-tagged** output (`<analysis>`, `<tasks><task><type>...`)
   and hand-rolling a regex/string-based XML parser. That's a real gotcha this
   cycle avoids (see below).

2. **The Claude Agent SDK's `subagents` feature** (`claude_agent_sdk` /
   `@anthropic-ai/claude-agent-sdk`, docs at
   [code.claude.com/docs/en/agent-sdk/subagents](https://code.claude.com/docs/en/agent-sdk/subagents),
   fetched **2026-07-29**, versioned against Claude Code v2.1.21x). This is a
   *different, heavier product*: subagents are invoked via the `Agent` tool
   inside a Claude Code session, defined via `AgentDefinition` (`description`,
   `prompt`, `tools`, `model`, `skills`, `maxTurns`, `background`, etc.), and the
   whole thing runs on top of the Claude Code CLI runtime (Node-based process,
   its own session/transcript machinery, `query()` async generator). It is not
   something you `pip install anthropic` and call — it requires the Claude Code
   CLI as an underlying dependency. Genuinely more capable (context isolation,
   parallel background subagents, tool-restriction, resumable sessions,
   nested-subagent depth limits) but too heavy a dependency chain to stand up
   and *actually run* in one day inside this repo's plain-`anthropic`-SDK
   examples.

3. **Managed Agents multiagent orchestration** — a distinct beta *API product*
   (`managed-agents-2026-04-01` beta header, per
   [platform.claude.com/docs/en/managed-agents/multiagent-orchestration](https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration),
   fetched 2026-07-29), where a coordinator and worker agents share a sandboxed
   filesystem and run in isolated session threads. Multi-agent orchestration
   reached public beta **2026-05-06** per search results (not independently
   verified against a primary changelog). Requires provisioning a sandbox/vault
   — out of scope for a same-day build.

**Decision for today's build: use path (1)**, adapted with a materially better
parsing mechanism than the 2024 cookbook's XML regex approach (see next
section), matching this repo's existing plain-`anthropic`-SDK-only convention.

### `client.messages.parse(..., output_format=<pydantic model>)` replaces the cookbook's XML parsing

Verified directly against the installed SDK (`anthropic==0.120.2`, latest on
PyPI today, 2026-07-29) by reading
`anthropic/resources/messages/messages.py::Messages.parse` and
`anthropic/types/parsed_message.py`:

- `client.messages.parse(model=, max_tokens=, messages=, output_format=SomePydanticModel)`
  exists on the **non-beta** `client.messages` resource (not
  `client.beta.messages`) — confirmed via `hasattr` and by reading the source,
  no `betas=[...]` header required (the only header it adds is an internal
  SDK-usage tracking header, `_helper_header("messages.parse")`, unrelated to
  the beta-feature mechanism).
- Internally it builds a JSON Schema from the pydantic type via
  `pydantic.TypeAdapter(output_format).json_schema()` and sends it as
  `output_config.format = {"type": "json_schema", "schema": ...}` on `POST
  /v1/messages`. This matches the public
  [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
  docs (fetched 2026-07-29): the older `structured-outputs-2025-11-13` beta
  header + `output_format` body param are now **generally available** and
  folded into `output_config.format`; the beta header "continues working during
  a transition period" but is no longer required.
- The returned `ParsedMessage` exposes `.parsed_output` (an `Optional[T]`,
  `None` if the model's output didn't parse/validate) — confirmed by reading
  `anthropic/types/parsed_message.py`.
  (Superseded: a validation failure actually *raises* `pydantic_core.ValidationError` out of `.parse()` rather than returning `None`; see the corrected `README.md` / `knowledge/orchestrator-workers.md`.)
- **Verified fully offline, no network** (2026-07-29): a `pydantic.Field(
  min_length=1, max_length=3)` constraint on a `list[Subtask]` field shows up
  in the generated schema as `"minItems": 1, "maxItems": 3` and is enforced
  locally by pydantic when constructing the model directly — this gives the
  orchestrator's "break the task into at most 3 subtasks" instruction a real,
  checkable schema constraint instead of just a hopeful prompt sentence.

### What "fans out to specialist agents" means for a single-file example

Given (1)+(2) above, the smallest real orchestrator has three Messages-API
calls, each with a distinct system prompt / role:

1. **Plan** — one `client.messages.parse(..., output_format=Plan)` call turns
   the user's task into a validated `Plan` (a short list of `Subtask{specialist,
   instructions}`).
2. **Delegate** — one `client.messages.create(...)` call per `Subtask`, each
   with `system=f"You are a specialist in {subtask.specialist}..."` — this *is*
   the "fan out to specialist agents" step: each call is an independently
   instructed, independently invoked mini-agent with no shared conversation
   history, run sequentially (see Open questions re: parallelism).
3. **Synthesize** — one final `client.messages.create(...)` call whose prompt
   contains the original task plus every specialist's output, asking for one
   combined answer.

This mirrors the cookbook's three-phase shape exactly, but swaps its
regex/XML parsing for the SDK's real structured-output support — a strictly
better and now-idiomatic way to do the same thing, and it stays consistent
with this repo's `beta_tool`/pydantic-driven pattern from
`examples/typed-tool-registry/`.

## Build proposal

New example: **`examples/orchestrator-subagents/`**, sibling to the existing
two, reusing their conventions (MODEL constant, "skip live run without a key"
`main()`, offline `test_agent.py` driven by a scripted fake client).

**Shape:**

- `agent.py`
  - `class Subtask(BaseModel): specialist: str; instructions: str`
  - `class Plan(BaseModel): subtasks: list[Subtask] = Field(min_length=1, max_length=3)`
  - `plan_task(client, task: str) -> Plan` — calls `client.messages.parse(model=MODEL,
    max_tokens=512, messages=[...], output_format=Plan)`, raises `RuntimeError`
    if `response.parsed_output is None` (parse/validation failure — don't
    silently proceed with an empty plan).
  - `run_specialist(client, subtask: Subtask) -> str` — one
    `client.messages.create(model=MODEL, max_tokens=512,
    system=f"You are a specialist in {subtask.specialist}. ...",
    messages=[{"role": "user", "content": subtask.instructions}])` call;
    extracts and returns the text block, same extraction pattern as the other
    two examples.
  - `synthesize(client, task: str, results: list[tuple[Subtask, str]]) -> str`
    — one final `client.messages.create` call combining the task and every
    specialist's `(specialist, instructions, output)` into one prompt, asking
    for a synthesized answer; returns its text.
  - `run_orchestrator(client, task: str) -> str` — calls the three steps above
    in order (plan → each specialist sequentially → synthesize) and returns
    the final text. Kept sequential (not `ThreadPoolExecutor`) for
    determinism and a simple offline test; note in the README that
    parallelizing the specialist calls is the natural next increment.
  - `main()` — same `ANTHROPIC_API_KEY` guard as the other two examples; runs
    one real task end to end and prints the plan, each specialist's answer,
    and the final synthesis.
  - `MODEL = "claude-haiku-4-5"`, per `knowledge/anthropic-models.md`.
- `test_agent.py` — offline, no network, using a `FakeClient` whose
  `.messages.parse` and `.messages.create` are both scripted (same
  `SimpleNamespace`-block style as `minimal-agent-loop/test_agent.py`):
  1. `Plan(subtasks=[...4 items...])` raises `pydantic.ValidationError` (the
     `max_length=3` constraint is real, not just a prompt hint) — a pure local
     test, no client involved.
  2. `plan_task` against a fake `.messages.parse` returning
     `SimpleNamespace(parsed_output=Plan(...))` returns that exact `Plan`.
  3. `plan_task` against a fake `.messages.parse` returning
     `SimpleNamespace(parsed_output=None)` raises `RuntimeError` (parse-failure
     path is not silently swallowed).
  4. `run_specialist` against a fake `.messages.create` returns the scripted
     text, and the call's `system` kwarg contains the subtask's `specialist`
     string (specialization actually reached the API call, not just the
     prompt).
  5. `synthesize` against a fake `.messages.create` returns the scripted text,
     and the call's `messages` contain every specialist's output string.
  6. `run_orchestrator` end-to-end against a `FakeClient` scripted for a 2-item
     plan: asserts exactly 1 `parse` call + 2 specialist `create` calls + 1
     synthesis `create` call (4 total), each specialist call's `system` used
     the right `specialist`, and the returned value is the scripted synthesis
     text.
- `requirements.txt` — `anthropic>=0.120.2`, `pydantic>=2` (needed for both the
  live run and the offline self-test, matching `typed-tool-registry`'s
  requirements shape).
- `README.md` — same shape as the other two: what it is, how to run the
  self-test, how to run it live, and a short paragraph on why this uses
  `client.messages.parse(output_format=...)` instead of the classic
  XML-parsing cookbook approach, linking back to this note and to
  `examples/typed-tool-registry/` (same pydantic-driven-schema idea, applied to
  a whole message's output instead of one tool call's input).

**"It works" means:** `python test_agent.py` (after `pip install -r
requirements.txt`, no API key, no network) prints an all-`ok` summary covering
the schema constraint, both `plan_task` paths, `run_specialist`,
`synthesize`, and the full 4-call orchestrator sequence; `python agent.py`
with `ANTHROPIC_API_KEY` set runs a real task through plan → 1-3 specialist
calls → synthesis and prints all three stages.

**Explicitly out of scope for today** (leave for later backlog items):
parallel specialist execution (`ThreadPoolExecutor`/`asyncio.gather`), the
Claude Agent SDK's real `subagents`/`AgentDefinition` feature, Managed Agents
multiagent orchestration, and any cross-specialist communication (specialists
here are strictly independent — no shared state, matching the "clear
decomposition, minimal interdependence" guidance from the coordination-patterns
source below).

## Open questions

- Whether `output_format` / `output_config.format` is confirmed supported on
  `claude-haiku-4-5` specifically via the **direct** Claude API (not just
  Bedrock/Vertex). The docs page's direct-API model list says "Claude 4.5 and
  later models" without an exhaustive ID list; Haiku 4.5 is explicitly listed
  for Bedrock and Vertex, and reading the *installed SDK source* shows no
  model-based gating client-side (the request is built the same way
  regardless of model) — but this was not confirmed with a real network call
  against the direct API today. If the live run 400s specifically on
  `output_config`, the documented, longer-standing fallback is a forced
  `tool_choice` call (`{"type": "tool", "name": "plan"}`) — same idea, older
  and more broadly supported mechanism.
- The exact public beta-availability date of Managed Agents multiagent
  orchestration (search results say 2026-05-06; not independently confirmed
  against Anthropic's own changelog) — irrelevant to today's build since that
  path isn't used, but worth checking before ever proposing it as a future
  increment.
- Whether the Claude Agent SDK's `subagents` feature could realistically be
  exercised in this environment at all (it shells out to the Claude Code CLI)
  — not investigated, since path (1) was chosen instead; a future cycle
  wanting real subagent isolation, parallel background execution, or
  per-subagent tool restriction should start there instead of extending this
  hand-rolled version further.

Sources:
- [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents) (Anthropic, 2024-12-19)
- [`orchestrator_workers.ipynb`](https://github.com/anthropics/claude-cookbooks/blob/main/patterns/agents/orchestrator_workers.ipynb) (last touched 2026-02-17 per commit history)
- [Multi-agent coordination patterns](https://claude.com/blog/multi-agent-coordination-patterns) (Claude/Anthropic, 2026-04-10)
- [Subagents in the SDK](https://code.claude.com/docs/en/agent-sdk/subagents) (Claude Code docs, fetched 2026-07-29)
- [Multiagent orchestration (Managed Agents)](https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration) (fetched 2026-07-29)
- [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) (fetched 2026-07-29)
- `anthropic==0.120.2` source, read directly (`resources/messages/messages.py`, `types/parsed_message.py`), and exercised locally offline, 2026-07-29
