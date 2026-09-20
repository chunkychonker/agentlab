# `strict: true` tool schemas as prevention rather than cure

## Question

What is the smallest `typed-tool-registry`-shaped example that sets `strict: true`
on its tool schemas, and what does that flag actually buy — concretely, does it
make the `.call(...)` `ValueError` path (bad type / bad enum / missing field)
unreachable from the model side, and does it work on the lab's cheap default
model?

## Findings

Sources fetched **2026-09-03** unless noted. The `claude-api` skill is **not
installed on this machine** (re-checked `~/.claude/skills/` today — only
`graphify/`; consistent with the last several research notes). API facts below
are from primary Anthropic docs plus direct inspection of `anthropic==1.3.0`
source in a scratch venv, cited inline.

### The field: `strict: true`, top-level on the tool definition

From [Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use)
(fetched 2026-09-03):

> Set `"strict": true` as a top-level property in your tool definition, alongside
> `name`, `description`, and `input_schema`.

Guarantees, verbatim:

> * Tool `input` strictly follows the `input_schema`
> * Tool `name` is always valid (from provided tools or server tools)

Mechanism is grammar-constrained sampling — the same compile-a-grammar pipeline
as [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs).
`strict` is a plain `bool` on the SDK's **non-beta** `ToolParam`
(`anthropic/types/tool_param.py`, v1.3.0): *"When true, guarantees schema
validation on tool names and inputs."* It is also on the beta `BetaToolParam`.

### No beta header. It went GA on 2026-01-29.

From the [release notes](https://platform.claude.com/docs/en/release-notes/overview)
(fetched 2026-09-03):

- **2025-11-14** — public beta, header `structured-outputs-2025-11-13`, Sonnet 4.5
  + Opus 4.1 only.
- **2025-12-04** — "Structured outputs now supports Claude Haiku 4.5."
- **2026-01-29 (GA)** — *"Structured outputs are out of beta on the Claude API
  for Claude Sonnet 4.5, Claude Opus 4.5, and Claude Haiku 4.5. … a simplified
  integration path with no beta header required. … Existing beta users can
  continue using the beta header during the transition period."*

So: **no `anthropic-beta` header is needed.** The doc's quick-start cURL sends
only `anthropic-version: 2023-06-01`. This corrects the assumption baked into the
backlog item ("confirm the exact field and beta-header name") — there is no
header to confirm any more. The old one still works during the transition but is
not required and should not be added.

### Model support — Haiku 4.5 is in. This closes an open question.

`knowledge/tool-failure-taxonomy.md` flagged: *"No per-model support matrix is
published (all doc examples use `claude-opus-5`), so confirm before assuming it
works on a cheap model."* It is now published. The
[Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
page's supported-models list (fetched 2026-09-03) includes
`claude-sonnet-5`, `claude-opus-5`, `claude-opus-4-5-20251101`, and
**`claude-haiku-4-5-20251001`** (plus the Fable/Mythos 5 line). The
`claude-api` skill's `shared/tool-use-concepts.md` on GitHub agrees: *"Supported
models: … Claude Sonnet 5, and Claude Haiku 4.5."* The lab's default cheap model
(`claude-haiku-4-5`) is eligible — the example can stay on it.

Amazon Bedrock / Microsoft Foundry are still beta-only for this feature; the
example targets the first-party API, so that does not bite.

### The JSON Schema subset (from the structured-outputs "limitations" section)

**Supported:** `object`/`array`/`string`/`integer`/`number`/`boolean`/`null`;
`enum` (scalars only), `const`, `anyOf`, `allOf` (no `$ref` inside `allOf`),
`$ref` / `$defs` / `definitions` (no external `$ref`), `default`, `required`,
`additionalProperties` **(must be `false` for objects)**, string `format`
(`date-time`, `time`, `date`, `duration`, `email`, `hostname`, `uri`, `ipv4`,
`ipv6`, `uuid`), array `minItems` (only `0` or `1`).

**Not supported:** recursive schemas; complex types in `enum`; external `$ref`;
numeric constraints (`minimum`, `maximum`, `multipleOf`); string constraints
(`minLength`, `maxLength`, `pattern`); array constraints beyond `minItems 0|1`;
`additionalProperties` set to anything but `false`.

Practitioner failure modes (from the docs' own troubleshooting + the
[HN thread on the beta launch](https://news.ycombinator.com/item?id=45930598),
~2025-11): *"Schema is too complex"* / *"Too many recursive definitions"* if you
over-nest or mark too many tools strict — *"each optional parameter roughly
doubles a portion of the grammar's state space"*; advice is *"mark only critical
tools as strict"* and *"make parameters required where possible."* A safety
`stop_reason: "refusal"` or `max_tokens` truncation still overrides schema
conformance (200, billed, off-schema).

### Grammar caching

Compiled schemas are cached **24h from last use** (docs: "Data retention"). First
use of a new schema pays a compile-latency hit. Cache is invalidated by a change
to the schema *structure* or to the *set of tools* in the request — **not** by a
change to `name` or `description`. PHI must not appear in schema property names,
`enum`/`const` values, or `pattern` regexes (cached separately from message
content, without the same HIPAA safeguards).

### `@beta_tool(strict=True)` already does the right thing — verified in `anthropic==1.3.0`

Directly inspected and exercised in a scratch venv (`pip install anthropic==1.3.0`):

- `beta_tool(func, *, strict: bool | None = None, ...)` — the decorator (and its
  plain-function form) takes a `strict` kwarg. `BetaFunctionTool.to_dict()` emits
  `"strict": true` as a top-level key when it is set, alongside `name` /
  `description` / `input_schema` (`anthropic/lib/tools/_beta_functions.py`).
- `client.beta.messages.{create,parse,stream}` serialize each function-tool via
  `tool.to_dict()` (`anthropic/resources/beta/messages/messages.py` ~line 1525:
  `"tools": [*[tool.to_dict() for tool in runnable_tools], ...]`), so
  `client.beta.messages.tool_runner(tools=[...])` carries `strict` end-to-end.
  `tool_runner` still exists in v1.3.0 with the same
  `model/max_tokens/tools/messages/max_iterations` signature the existing
  example uses.
- The Pydantic-generated schema for a typed function **already includes
  `"additionalProperties": false`** and lists every argument in `required` —
  i.e. it is strict-subset-compatible out of the box, no post-processing needed.
- `Literal["low","medium","high"]` lowers to
  `{"type": "string", "enum": ["low","medium","high"], "title": "...", "description": "..."}`
  — the supported scalar-`enum` case.
- One wrinkle: the generated schema also carries a `"title"` key on the object
  and on each property (a Pydantic artifact). `title` is **not** named in the
  docs' supported-features list, but it is a pure annotation and the SDK ships
  `strict=True` on exactly this generation path, so it is almost certainly
  tolerated. The live run in the proposal is what confirms it.
- `BetaFunctionTool.call(bad_input)` still raises `ValueError` for a wrong type,
  a bad `enum` value, or a missing field (exercised: `set_priority.call({"task":
  "x", "level": "urgent"})` → `ValueError`; `{"level": 3}` → `ValueError`). That
  guard does not go away — `strict` makes the model **unable to produce the
  input that would trip it**, which is the whole "prevention not cure" point.

### The A/B this enables

Take one typed function, wrap it twice: `beta_tool(f, strict=True)` and
`beta_tool(f)`. Both `.to_dict()` payloads carry the **identical**
`input_schema` (same `additionalProperties: false`, same `enum`). The **only**
wire difference is the top-level `"strict": true`. Without it the schema is
advisory and the model can still emit `level: "urgent"` or `attendees: "3"`;
with it the same schema is compiled to a grammar and those emissions cannot be
sampled. The example's message is precisely *the schema didn't change, the
enforcement did.*

### Prior art in the lab

- `examples/typed-tool-registry/` — the shape to mirror (`@beta_tool` +
  `tool_runner` + `{name: tool}` dict + `run_agent`). Explicitly lists strict as
  out of scope.
- `examples/tool-error-policy/` — the *cure* side (retry / report / abort a
  failing call). Also defers strict.
- `knowledge/tool-failure-taxonomy.md` — one paragraph on strict as removing
  "one whole error class"; this cycle turns that into a runnable demo and a
  dedicated knowledge note.

Nothing in `examples/` sets `strict`. Directory name `strict-tool-schemas/` is
free (`ls examples/` on `main`; only open PR is #36, unrelated; no matching
branch).

## Build proposal

### Layer 1 — Intent

Add `examples/strict-tool-schemas/`: a `typed-tool-registry`-shaped multi-tool
agent whose tools are registered **twice** — once with `strict=True`, once
without — over the identical generated schemas, so the single wire difference is
the `strict` flag. It shows offline that the strict schemas are well-formed and
subset-compatible and still carry their `.call()` validation guard, and shows
live (one cheap model, ~2 billed calls) that under `strict` every tool input the
model emits validates against the schema, while the loose registry has no such
guarantee.

**Out of scope:** JSON *output* format / `output_config` (that is the sibling
feature, not tool inputs); `client.messages.parse`; nested / recursive schemas;
a general schema-rewriter that strips unsupported keywords (the walker only
*detects*); measuring grammar-compile latency or cache behaviour; any Bedrock /
Foundry path; forcing the loose registry to fail deterministically (it is
probabilistic — the example reports what happened, it does not assert loose
breaks).

### Layer 2 — Behavioral spec

**Modules**

1. `schema_subset.py` — pure. "Does this JSON Schema use only keywords the
   strict grammar compiler supports?"
2. `agent.py` — the two tool registries, `run_agent`, and a live `main`.
3. `test_agent.py` — offline self-test, imports `anthropic`, no key, no network.

**`schema_subset.unsupported_keywords(schema)`**

- Input: a `Mapping[str, object]` (a tool's `input_schema` or any sub-schema).
- Output: a sorted, de-duplicated `list[str]` of schema keywords found anywhere
  in the tree that are **not** in `SUPPORTED_KEYWORDS`.
- `SUPPORTED_KEYWORDS` is a module-level `frozenset[str]` transcribed from the
  structured-outputs "limitations" section (see Findings), plus `title` and
  `description` as tolerated annotations (comment saying so and citing this
  note).
- Walks `properties` values, `items`, `anyOf` / `allOf` / `oneOf` members,
  `$defs` / `definitions` values. Does **not** dereference `$ref` (only notes
  the keyword's presence).
- Invariants: pure (no I/O, no clock, no globals); returns `[]` for a
  fully-supported schema; deterministic; input not mutated.
- Failure modes (docstring): a non-mapping at a position where a sub-schema is
  expected is skipped, not raised on (real schemas nest lists of mappings);
  never raises on well-formed JSON-Schema-shaped input.

**`agent.py`**

- `MODEL = "claude-haiku-4-5"` — one constant (per `knowledge/anthropic-models.md`).
- Two typed tool functions defined once as plain functions, chosen so the
  strict subset is exercised and a loose model plausibly fumbles:
  - `set_priority(task: str, level: Priority) -> str` where
    `Priority = Literal["low", "medium", "high"]` — the scalar-`enum` case.
  - `schedule_event(title: str, date: str, attendees: int) -> str` — an
    `integer` the model is tempted to send as `"3"` / `"three"`.
- `STRICT_TOOLS` — each function wrapped with `beta_tool(fn, strict=True)`.
- `LOOSE_TOOLS = [beta_tool(t.func) for t in STRICT_TOOLS]` — same underlying
  functions, no `strict`. Parallel by construction.
- `STRICT_REGISTRY` / `LOOSE_REGISTRY` = `{t.name: t for t in ...}`.
- `run_agent(client, user_message, tools, *, max_iterations=6) -> str` — same
  body as `typed-tool-registry`'s `run_agent` (drive
  `client.beta.messages.tool_runner(...).until_done()`, raise `RuntimeError` if
  the final `stop_reason == "tool_use"`, else join text blocks) but with `tools`
  as an explicit parameter so `main` can call it once per registry.
- `main()`:
  - No `ANTHROPIC_API_KEY` → print a one-line note, `return 0` (same convention
    as `typed-tool-registry`).
  - With a key: for each of `("loose", LOOSE_*)` and `("strict", STRICT_*)`, run
    a prompt that needs both tools — e.g. *"Mark the task 'submit tax forms' as
    top priority, then schedule a kickoff titled 'Q3 Planning' on 2026-10-05 for
    me plus three teammates."* — collect every `tool_use` block's `.input` from
    the runner's turns, and for each print: the raw input dict, the Python
    `type()` of each value, and the result of `REGISTRY[name].call(input)`
    (either the return value or the `ValueError` text). Print a one-line verdict
    per side.
  - The builder MAY pass `tool_choice={"type": "any"}` to make both sides
    reliably emit a call (Haiku 4.5 supports forced tool use; the `claude-api`
    skill notes `tool_choice: any` + `strict: true` only applies on models that
    support forced tool use — Haiku 4.5 does).

**Acceptance criteria (checkable)**

Offline (`python test_agent.py`, no key, no network):

1. Every tool in `STRICT_TOOLS` has `to_dict()["strict"] is True`.
2. No tool in `LOOSE_TOOLS` has a `"strict"` key in `to_dict()`.
3. Registries are parallel: `[t.name for t in STRICT_TOOLS] == [t.name for t in
   LOOSE_TOOLS]`; for each index `STRICT_TOOLS[i].func is LOOSE_TOOLS[i].func`;
   and `STRICT_TOOLS[i].input_schema == LOOSE_TOOLS[i].input_schema` (the schema
   is byte-identical — only the flag differs).
4. For every strict tool's `input_schema`: `type == "object"`;
   `additionalProperties is False`; `set(required) == set(properties)` (all
   fields required); and `schema_subset.unsupported_keywords(schema) == []`.
5. `set_priority`'s `level` property has `enum == ["low", "medium", "high"]` and
   `type == "string"` (the `Literal` lowered to a scalar enum).
6. The `.call()` guard is intact: `set_priority.call({"task": "x", "level":
   "urgent"})`, `set_priority.call({"task": "x", "level": 3})`, and
   `schedule_event.call({"title": "x", "date": "2026-10-05", "attendees":
   "three"})` each raise `ValueError`. (Comment: `strict` makes the model unable
   to produce these; the guard stays as defence in depth.)
7. `unsupported_keywords` unit checks: `[]` for a strict tool schema; returns
   `["minLength", "pattern"]` (sorted) for a hand-built schema that adds those;
   input mapping is not mutated.
8. `run_agent` seam (tiny fake client, as in `typed-tool-registry/test_agent.py`):
   returns the joined final text on a clean finish; raises `RuntimeError` when
   the final message `stop_reason == "tool_use"` (cap hit mid-call).

Live (`ANTHROPIC_API_KEY` set, `python agent.py`, ~2 billed Haiku calls):

9. The strict side completes without an API error (confirms `strict: true` +
   the Pydantic-generated schema, `title` and all, is accepted on
   `claude-haiku-4-5` with no beta header).
10. Every `tool_use.input` observed from the **strict** side passes
    `REGISTRY[name].call(input)` with no `ValueError` — `level` is one of the
    three enum strings, `attendees` is a JSON integer.
11. The loose side's observed inputs are printed with their types alongside the
    strict side's for eyeball comparison. No assertion that loose fails.
12. Without `ANTHROPIC_API_KEY`, `agent.py` prints one line and exits `0`.

### Layer 3 — Interfaces (stubs, no bodies)

```python
# schema_subset.py
from collections.abc import Mapping

SUPPORTED_KEYWORDS: frozenset[str]  # transcribed from the structured-outputs
                                    # limitations section + {"title","description"}

def unsupported_keywords(schema: Mapping[str, object]) -> list[str]:
    """Return sorted unique JSON-Schema keywords in `schema` (recursively) that
    the strict grammar compiler does not support.

    Pure. `[]` means the schema is strict-subset-compatible. Does not follow
    `$ref`. Skips non-mapping nodes rather than raising. Never mutates `schema`.
    """
```

```python
# agent.py
from typing import Literal
from anthropic import beta_tool

MODEL = "claude-haiku-4-5"
Priority = Literal["low", "medium", "high"]

@beta_tool(strict=True)
def set_priority(task: str, level: Priority) -> str: ...
@beta_tool(strict=True)
def schedule_event(title: str, date: str, attendees: int) -> str: ...

STRICT_TOOLS: list           # [set_priority, schedule_event]
LOOSE_TOOLS: list            # [beta_tool(t.func) for t in STRICT_TOOLS]
STRICT_REGISTRY: dict[str, object]
LOOSE_REGISTRY: dict[str, object]

def run_agent(client, user_message: str, tools: list, *, max_iterations: int = 6) -> str:
    """Drive client.beta.messages.tool_runner(...).until_done() with `tools`.
    Raise RuntimeError if the final message stop_reason == 'tool_use'
    (cap hit mid-call); otherwise return the joined final text blocks.
    """

def main() -> int:
    """No ANTHROPIC_API_KEY -> print one line, return 0. Otherwise run the same
    prompt through LOOSE_TOOLS then STRICT_TOOLS, print each observed
    tool_use.input, its value types, and the REGISTRY[name].call(...) result,
    plus a one-line verdict per side.
    """
```

`requirements.txt`: `anthropic>=1.3.0` (SDK is v1 now; `beta_tool` +
`client.beta.messages.tool_runner` both present and carry `strict` through in
1.3.0 — the existing examples' `>=0.120.0` pin predates the v1 release). Needed
for both the live run and the self-test (the test imports `anthropic` to build
and inspect the real `BetaFunctionTool` objects, same as
`typed-tool-registry/test_agent.py`).

### Where it goes

New directory `examples/strict-tool-schemas/` with `agent.py`,
`schema_subset.py`, `test_agent.py`, `requirements.txt`, `README.md`. The README
follows `typed-tool-registry/README.md`: a table of files, the offline-test
transcript block (wire it into
`examples/readme-transcript-check/` — `python3 check_transcript.py
../strict-tool-schemas -- python3 test_agent.py`), a "run it live" section that
**states the ~2 billed Haiku calls** the way `mcp-connect-claude-code` states
its cost, and an "explicitly out of scope" section (JSON output format,
`messages.parse`, nested/recursive schemas, schema rewriting, latency/cache
measurement).

### The self-test ("it works")

`python test_agent.py` prints one `ok` line per criterion 1–8 above and a final
`All N self-tests passed.`, exits non-zero on the first failure. No key, no
network, deterministic. The live check is `ANTHROPIC_API_KEY=... python
agent.py` printing the loose-vs-strict input comparison and the strict-side
"every input validated" verdict.

## Open questions

- **`title` in a strict schema.** The Pydantic-generated schema carries
  `"title"` on the object and every property; the docs' supported-keyword list
  does not mention it. Expected to be tolerated (pure annotation; the SDK ships
  `strict=True` on this exact generation path), but only the live run
  (criterion 9) confirms it on `claude-haiku-4-5`. If it *is* rejected, the
  fallback is a two-line post-processor that pops `title` before `.to_dict()` —
  note it in the README, do not expand scope.
- **How reliably the loose side fumbles on one cheap call.** Enum violations
  ("urgent" for a low/medium/high field) and stringified integers are common but
  not guaranteed on a single Haiku turn. The example is designed not to depend
  on it (criterion 11 is print-only). If the builder wants a sharper contrast,
  `tool_choice={"type": "any"}` plus a deliberately tempting prompt raises the
  odds; still not asserted.
- **Interaction with prompt caching / `cache_control` on tools.** Out of scope
  here, but `knowledge/prompt-caching.md` and the 24h grammar cache are two
  different caches keyed differently — worth a sentence in the knowledge note,
  not code this cycle.
- **`claude-api` skill.** Still not installed locally; every API fact above is
  from the live docs + SDK source read today. If the skill later disagrees on
  the model-support list or header, it wins — re-check before the next strict
  cycle.

## Sources

- [Strict tool use](https://platform.claude.com/docs/en/agents-and-tools/tool-use/strict-tool-use) — Anthropic docs, fetched 2026-09-03
- [Structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs) — Anthropic docs (model list, JSON Schema limitations, caching), fetched 2026-09-03
- [Claude Platform release notes](https://platform.claude.com/docs/en/release-notes/overview) — GA date 2026-01-29, Haiku 4.5 support 2025-12-04, beta launch 2025-11-14; fetched 2026-09-03
- [`skills/claude-api/shared/tool-use-concepts.md`](https://github.com/anthropics/skills/blob/main/skills/claude-api/shared/tool-use-concepts.md) — Anthropic skills repo (model-support list, `tool_choice: any` + `strict` caveat), fetched 2026-09-03
- [Structured outputs on the Claude Developer Platform](https://news.ycombinator.com/item?id=45930598) — Hacker News, ~2025-11 (practitioner reception; date approximate)
- `anthropic==1.3.0` source read in a scratch venv — `anthropic/lib/tools/_beta_functions.py`, `anthropic/resources/beta/messages/messages.py`, `anthropic/types/tool_param.py` — 2026-09-03
- `knowledge/tool-failure-taxonomy.md`, `knowledge/typed-tool-registry.md`, `knowledge/anthropic-models.md`; `examples/typed-tool-registry/`
