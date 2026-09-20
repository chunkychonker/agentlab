# Strict tool use (`strict: true`): grammar-constrained tool inputs

Setting `"strict": true` on a tool definition makes the API compile the tool's
`input_schema` into a grammar and constrain sampling to it, so the emitted
`tool_use.input` always matches the schema and `tool_use.name` is always a real
tool. It deletes the missing-parameter / wrong-type / bad-enum / unknown-name
error class instead of handling it — the *prevention* half of
[[tool-failure-taxonomy]] (which covers the *cure* half: retry / report / abort).

## The field and the header

- `"strict": true` is a **top-level** key on the tool definition, next to
  `name`, `description`, `input_schema`.
- **No `anthropic-beta` header.** Went GA on the first-party API **2026-01-29**
  ([release notes](https://platform.claude.com/docs/en/release-notes/overview)).
  Launched 2025-11-14 in public beta behind `structured-outputs-2025-11-13`;
  that header is still accepted during the transition but is not required and
  should not be added. (Still beta-only on Amazon Bedrock / Microsoft Foundry.)
- Sibling feature: JSON **output** format (`output_config.format`, was
  `output_format`) constrains Claude's *text* response. Same grammar pipeline,
  different target. Strict tool use is the tool-*input* one.

## Model support (published — no longer "all examples use opus")

`claude-sonnet-5`, `claude-opus-5`, `claude-opus-4-5-20251101`,
**`claude-haiku-4-5-20251001`** (added 2025-12-04), plus the Fable/Mythos 5
line. The lab's cheap default `claude-haiku-4-5` is eligible — no need to jump
to a pricier tier (contrast [[compaction]], which excludes Haiku). Source:
[structured outputs](https://platform.claude.com/docs/en/build-with-claude/structured-outputs)
model list + `claude-api` skill `shared/tool-use-concepts.md`, both 2026-09-03.

## JSON Schema subset

**Allowed:** the basic types; `enum` (scalars only), `const`, `anyOf`, `allOf`
(no `$ref` inside), `$ref`/`$defs`/`definitions` (no external `$ref`), `default`,
`required`, `additionalProperties` **(must be `false` for every object)**,
string `format` (`date-time`, `time`, `date`, `duration`, `email`, `hostname`,
`uri`, `ipv4`, `ipv6`, `uuid`), array `minItems` (only `0` or `1`).

**Rejected:** recursive schemas; complex `enum` members; external `$ref`;
`minimum`/`maximum`/`multipleOf`; `minLength`/`maxLength`/`pattern`; array
constraints past `minItems 0|1`; `additionalProperties` anything but `false`.

`title` is not on the "supported" list but is a pure annotation that
Pydantic-generated schemas always emit; observed tolerated on `claude-haiku-4-5`
(see `examples/strict-tool-schemas/`). If a schema you send is rejected as *"too
complex"* / *"too many recursive definitions"*, flatten it and mark fewer tools
strict — each optional field roughly doubles part of the grammar state space, so
prefer required fields and reserve `strict` for tools where a violation actually
breaks something.

## Caching and data retention

Compiled grammars cached **24h from last use**; first use of a new schema pays a
compile-latency hit. Invalidated by a change to schema *structure* or to the
*set of tools* in the request — **not** by editing `name`/`description`. PHI must
not go in schema property names, `enum`/`const` values, or `pattern` regexes
(cached separately from messages, without the HIPAA safeguards prompts get).

## What it does *not* override

A safety refusal (`stop_reason: "refusal"`) or `max_tokens` truncation still
wins — you get a 200, you are billed, and the content is off-schema. Strict is
not a substitute for checking `stop_reason`.

## SDK: `anthropic>=1.3.0` (SDK is v1 now)

- `beta_tool(func, *, strict: bool | None = None, ...)` takes the kwarg;
  `BetaFunctionTool.to_dict()` then emits `"strict": true`.
  `client.beta.messages.{create,parse,stream,tool_runner}` serialize function
  tools via `.to_dict()`, so `strict` flows through `tool_runner` end-to-end.
- The Pydantic schema `@beta_tool` generates from a typed function **already**
  has `additionalProperties: false` and every arg in `required` — strict-subset
  compatible with no post-processing. `Literal[...]` → `{"type":"string",
  "enum":[...]}`.
- `BetaFunctionTool.call(bad_input)` still raises `ValueError` on a bad
  type/enum/missing field. `strict` doesn't remove that guard — it makes the
  model unable to produce the input that trips it. Keep the guard as defence in
  depth.
- `tool_choice: {"type": "any"}` + `strict: true` only applies on models that
  support forced tool use (Haiku 4.5 does).

Runnable demo: `examples/strict-tool-schemas/` — same function wrapped
`strict=True` and not, over the identical generated schema, so the only wire
difference is the flag.

Related: [[tool-failure-taxonomy]], [[typed-tool-registry]], [[tool-use-loop]],
[[anthropic-models]], [[prompt-caching]]
