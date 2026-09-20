# Strict tool schemas (`strict: true` as prevention, not cure)

Two typed tools, registered **twice** over the identical generated schemas —
once with `strict=True`, once without. The `input_schema` bytes are the same on
both sides; the only difference on the wire is one top-level key:

```json
"strict": true
```

`examples/typed-tool-registry/` shows the *cure*: `@beta_tool` validates
model-supplied input with Pydantic and raises `ValueError` when the model sends
`level: "urgent"` for a `low|medium|high` field, or `attendees: "three"` for an
`int`. By then the bad input already exists and a turn has been burned on it.
`strict: true` is the *prevention*: the same schema is compiled into a sampling
grammar, so those tokens cannot be sampled at all. **The schema didn't change,
the enforcement did.**

From the research note:
[`research/2026-09-03-strict-tool-schemas.md`](../../research/2026-09-03-strict-tool-schemas.md).

## What's here

| File | What it is |
|------|-----------|
| `agent.py` | The two `@beta_tool(strict=True)` functions, `STRICT_TOOLS` / `LOOSE_TOOLS` (the loose list derived from the strict one, so "same functions, same schemas" is true by construction), the two `{name: tool}` registries, `run_agent()`, and a live `main()` that runs one prompt through each registry. |
| `schema_subset.py` | Pure, no I/O: `unsupported_keywords(schema)` walks a JSON Schema and returns the keywords the strict grammar compiler does not support. Detection only — it never rewrites a schema. |
| `test_agent.py` | Offline self-test: one function and one `ok` line per acceptance criterion 1–8 in the note. No key, no network. |
| `requirements.txt` | `anthropic>=1.3.0` — needed for **both** the live run and the self-test. |

The two tools are plain deterministic functions chosen so the strict subset is
actually exercised and a loose model is plausibly tempted off-schema:

- `set_priority(task: str, level: Priority) -> str`, where
  `Priority = Literal["low", "medium", "high"]` — the scalar-`enum` case. The
  live prompt asks for **"top priority"**, which is not one of the three values.
- `schedule_event(title: str, date: str, attendees: int) -> str` — the live
  prompt says **"three teammates"**, tempting a `"three"` or `"3"` where the
  schema wants a JSON integer.

### No beta header

Structured outputs / strict tool use went **GA on 2026-01-29**. There is no
`anthropic-beta` header here and you should not add one; the old
`structured-outputs-2025-11-13` header still works during the transition but is
no longer required. Model support is likewise settled: `claude-haiku-4-5` is on
the published support list (since 2025-12-04), so this example stays on the
lab's cheap default rather than escalating to Sonnet.

## Run the self-test (no API key needed, but does need `anthropic` installed)

```bash
cd examples/strict-tool-schemas
pip install -r requirements.txt
python test_agent.py
```

Expected output:

```
ok  every strict tool's to_dict() carries top-level "strict": true
ok  no loose tool's to_dict() has a "strict" key at all
ok  strict and loose payloads are identical except for that one key
ok  each strict schema is an object, closed, all-required, subset-clean
ok  Priority lowered to a string enum, attendees to a JSON integer
ok  .call() still raises ValueError on off-enum, wrong-type, missing input
ok  walker finds nested constraints, ignores property names, mutates nothing
ok  run_agent joins final text, reports tool inputs, raises on the cap

All 8 self-tests passed.
```

Verifiable, not hand-copied:
[`examples/readme-transcript-check`](../readme-transcript-check/) checks this
block against the real thing —
`python3 check_transcript.py ../strict-tool-schemas -- python3 test_agent.py`.
Run it from this example's venv; a bare interpreter without `anthropic` reports
`UNRUNNABLE`, not `MATCH`.

The third line is the load-bearing one. It strips `"strict"` from the strict
payload and asserts the remainder equals the loose payload exactly — which is
what makes the live comparison a controlled experiment instead of two unrelated
runs. The sixth line is the deliberate counterweight: `.call()`'s `ValueError`
guard is still there and still fires. `strict` constrains *the model*, not your
own code, a replayed transcript, or a tool invoked from a non-strict path, so
the guard stays as defence in depth.

## Run it live (needs a key, and costs money)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python agent.py
```

This is a **billed** run: one tool-use conversation per registry, so roughly
**2–4 Haiku 4.5 calls** total (each side is an initial turn plus a turn after
the tool results come back). At Haiku pricing with a ~1K-token prompt this is
fractions of a cent, but it is not free, and unlike `test_agent.py` it is not
reproducible offline. Without `ANTHROPIC_API_KEY` set, `agent.py` prints a
one-line note and exits 0, same as `typed-tool-registry`.

It prints, for each side, every `tool_use` input the model actually emitted, the
Python `type()` of each value, and what `REGISTRY[name].call(...)` makes of it,
then a one-line verdict.

**What is asserted and what is not.** The strict side's claim is checkable:
every observed input must validate. The loose side is **print-only** — it is
reported for eyeball comparison and nothing asserts that it fails. Whether one
cheap Haiku turn actually fumbles the enum is probabilistic, and an example that
asserted it would be a flaky test dressed up as a demonstration.

No `tool_choice` is set, deliberately. Forcing `{"type": "any"}` would sharpen
the contrast, but it obliges the model to call a tool on *every* turn including
the one after the tool results, so the runner can never reach a natural
`end_turn` and instead burns to `max_iterations` and raises. The prompt needs
both tools on its own.

Model id is the constant `MODEL` at the top of `agent.py` (default
`claude-haiku-4-5`). See
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## `schema_subset.py`, and why it only detects

A strict schema outside the supported subset is a **400 at request time**, not a
quiet degradation — so it is worth checking before you ship.
`SUPPORTED_KEYWORDS` is transcribed from the structured-outputs "limitations"
section (see the note for the full list). Two details worth knowing:

- **`oneOf` is not supported** — only `anyOf` and `allOf` are. The walker still
  descends into a `oneOf`, so it reports both the branch keyword and anything
  unsupported hiding inside it.
- **`title` is tolerated by assumption, not by documentation.** Pydantic emits
  `"title"` on the object and on every property, and the docs' supported list
  does not name it. It carries no constraint and the SDK ships `strict=True` on
  exactly this generation path, so it is treated as an annotation here. This is
  the one thing in the example that only a live run can confirm — see below.

The walker never dereferences `$ref`, so an *external* `$ref` (unsupported) is
invisible to it: the keyword itself is legal. It also checks keywords, not
values, so `additionalProperties: false` is asserted separately in the
self-test.

## Deviations from the research note, and what is unverified

- **`run_agent` has one parameter the note's stub does not**: a keyword-only
  `observer` callback, invoked once per `tool_use` block as each turn arrives.
  The note's spec requires `main` to "collect every `tool_use` block's `.input`
  from the runner's turns" while `run_agent` returns `str`; the final message
  alone does not contain those blocks. A callback was the way to get them
  without either a second billed conversation or a copy of `run_agent`'s body
  inside `main`. It defaults to `None`, so the note's call shape is unchanged.
- **`main`'s reporting helpers are not covered by the suite.** `evaluate` and
  `report_lines` are pure and easy to test, but the note fixes the transcript at
  one `ok` line per criterion 1–8 and none of those criteria is about
  presentation. They were exercised by hand against a scripted fake client
  during the build; they are not regression-tested.
- **Acceptance criteria 9–11 (the live ones) are unverified.** This example was
  built and checked without making a billed API call. Criteria 1–8 and 12 are
  verified by the self-test and the no-key run above. Criterion 9 in particular
  — that a Pydantic-generated schema with `title` in it is accepted under
  `strict: true` on `claude-haiku-4-5` — is *expected* to hold but has not been
  observed here. If it turns out to be rejected, the fix is a two-line
  post-processor that pops `title` before `.to_dict()`; do not expand the
  example further than that.

## Explicitly out of scope

JSON **output** format / `output_config` and `client.messages.parse` (the
sibling feature — this example is about tool *inputs*); nested and recursive
schemas; a schema *rewriter* that strips unsupported keywords (`schema_subset`
only detects); measuring grammar-compile latency or the 24h grammar cache; any
Bedrock / Foundry path, both of which are still beta-only for this feature; and
forcing the loose registry to fail deterministically.
