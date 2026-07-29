# Orchestrator-workers: fan out to specialist agents

The pattern behind "subagent delegation": one Messages API call plans, N
independent Messages API calls execute specialist subtasks, one final call
synthesizes. All plain `client.messages` — no special SDK needed for the basic
version. Complements [[tool-use-loop]] and [[typed-tool-registry]] (same repo,
same `anthropic` package, different shape of multi-call orchestration).

## The pattern

1. **Plan**: one call turns the task into a short list of `{specialist,
   instructions}` subtasks.
2. **Delegate**: one call per subtask, each with its own `system` prompt (the
   "specialist" persona) — this is what makes them independent mini-agents,
   not just prompt variations in one conversation.
3. **Synthesize**: one final call combines every specialist's output into one
   answer.

Canonical reference: Anthropic's [Building Effective Agents](https://www.anthropic.com/research/building-effective-agents)
(2024-12-19 — dated but still the standard reference for this workflow name).
Its companion notebook parses the plan step with hand-rolled XML tags/regex;
prefer `client.messages.parse` (below) instead — it's strictly better and
didn't exist when that notebook was written.

## `client.messages.parse(..., output_format=<pydantic model>)`

Verified against installed `anthropic==0.120.2` source, 2026-07-29
(`resources/messages/messages.py::Messages.parse`,
`types/parsed_message.py`):

- Lives on the **non-beta** `client.messages` (not `client.beta.messages`).
- `output_format` takes a pydantic model; the SDK builds its JSON Schema via
  `pydantic.TypeAdapter(...).json_schema()` and sends it as
  `output_config.format = {"type": "json_schema", "schema": ...}`.
- Response is a `ParsedMessage` with `.parsed_output: Optional[T]`. A schema
  validation failure does **not** show up as `None` — `.parse()` calls
  pydantic's `TypeAdapter(...).validate_json(...)` internally with no
  try/except, so an invalid response raises `pydantic_core.ValidationError`
  *out of* the `.parse()` call itself. `parsed_output` is `None` only in the
  separate case where the response has no text block at all (e.g. a refusal
  or a tool-use-only response) — two distinct failure modes; catch
  `ValidationError` for the first, check for `None` for the second, don't
  conflate them.
- No `betas=[...]` header needed — this used to require the
  `structured-outputs-2025-11-13` beta header, but per the
  [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
  docs (fetched 2026-07-29) it's now **generally available**; the old header
  still works during a transition period but isn't required.
- `pydantic.Field(min_length=..., max_length=...)` on a `list[...]` field
  becomes `minItems`/`maxItems` in `TypeAdapter(...).json_schema()`, but the
  Anthropic SDK's `transform_schema` step demotes `maxItems` into a
  description string (e.g. `"description": "{maxItems: 3}"`) before the
  request is sent — `minItems` survives the transform, `maxItems` does not.
  On the wire, the upper bound is only a prompt hint, not an enforced schema
  constraint. The client-side enforcement is real and separate, though:
  constructing the model directly (or letting `client.messages.parse` do so
  internally) with too many items raises `pydantic.ValidationError` — that's
  what a caller should catch if the model ignores the hint.
- There's also a forced-`tool_choice` route to the same goal
  (`tools=[{"name": ..., "input_schema": ...}], tool_choice={"type": "tool",
  "name": ...}`) — older, more broadly documented, worth falling back to if
  `output_format` ever 400s on a specific model.

## Three different "subagent" products — don't conflate them

- **Plain Messages API orchestration** (above): just multiple
  `client.messages.create`/`.parse` calls with different system prompts. No
  extra dependency. What this repo's examples use.
- **Claude Agent SDK `subagents`** (`claude_agent_sdk`, docs:
  [code.claude.com/docs/en/agent-sdk/subagents](https://code.claude.com/docs/en/agent-sdk/subagents)):
  `AgentDefinition` + `Agent` tool inside a Claude Code session — real context
  isolation, parallel background execution, per-subagent tool restriction,
  resumable sessions. Requires the Claude Code CLI runtime underneath, not
  just `pip install anthropic`. Reach for this when you need real isolation
  or parallelism, not for a small same-day example.
- **Managed Agents multiagent orchestration** (beta, header
  `managed-agents-2026-04-01`, [docs](https://platform.claude.com/docs/en/managed-agents/multiagent-orchestration)):
  coordinator + workers sharing a sandboxed filesystem/vault, isolated session
  threads. A distinct hosted-sandbox product, not something to casually add to
  a local example.

Related: [[tool-use-loop]], [[typed-tool-registry]], [[anthropic-python-sdk]], [[anthropic-models]]
