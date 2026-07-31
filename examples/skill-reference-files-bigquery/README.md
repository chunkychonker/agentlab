# Skill reference files (selective loading)

Demonstrates level-3 **reference-file** loading in the Skills
progressive-disclosure model: Claude selectively reads named markdown files
from a skill's `reference/` directory into context, one at a time, and
leaves sibling files it doesn't need unread on disk.

This is the **read-into-context** counterpart to
[`examples/skill-script-execution`](../skill-script-execution/)'s
**execute-for-stdout-only** pattern — same level-3 slot in the Skills model,
different content type (instructions/resources vs. code). See both together
plus [`research/2026-07-29-anatomy-of-a-skill.md`](../../research/2026-07-29-anatomy-of-a-skill.md)
(the SKILL.md frontmatter/trigger mechanism, not built here) for the full
picture.

From the research note:
[`research/2026-07-31-skill-reference-files.md`](../../research/2026-07-31-skill-reference-files.md).

## What's here

| File | What it is |
|------|-----------|
| `agent.py` | `read_reference(filename)`, its tool schema, and `run_agent()` — the manual loop. |
| `test_agent.py` | Offline self-test: tool unit tests + scripted fake-client transcript tests. No key, no network. |
| `skills/bigquery-analysis/SKILL.md` | The skill body, linking to all three reference files one level deep. |
| `skills/bigquery-analysis/reference/{finance,sales,product}.md` | Domain-specific reference content (real metric definitions, not placeholders). |
| `skills/bigquery-analysis/reference/reference/` | Test-only fixture directory — see its own README. Not linked from SKILL.md. |
| `requirements.txt` | `anthropic` — only needed for the live run. |

The skill mirrors the authoring best-practices doc's own worked example: a
BigQuery-style skill split into `reference/finance.md`, `reference/sales.md`,
and `reference/product.md`, each with a short table of contents, so a
question about revenue never has to pull sales or product content into
context.

## The tool: `read_reference`

`read_reference(filename)` reads one file's full content **if and only if**
it resolves to a real file inside the skill's own `reference/` directory.
Anthropic's Skills docs describe progressive disclosure and reference-file
authoring patterns, but say nothing about constraining which on-disk path a
reference read may resolve to — that's presumably handled by the sandboxed
code-execution container for real Claude API/Code Skills, but **this is a
hand-built tool on the raw Messages API with no sandbox**, so the containment
check is this example's own responsibility, not something the Skills
mechanism promises for free. `read_reference` resolves the path via
`os.path.realpath`, checks containment *before* opening anything, and
returns a plain `"Error: ..."` string (never raises, never a traceback) for:

- a path that resolves outside `reference/` — via `../` traversal or an
  absolute path
- a filename that doesn't exist
- a filename that resolves to a directory, not a file

## Run the self-test (no API key needed)

```bash
cd examples/skill-reference-files-bigquery
python test_agent.py
```

Expected output:

```
ok  read_reference returns exact on-disk content for finance.md
ok  '../SKILL.md' traversal is rejected, real SKILL.md content never returned
ok  deep '../' traversal toward /etc/passwd is rejected, no passwd content leaked
ok  absolute path '/etc/hosts' is rejected, real content never returned
ok  absolute path '/etc/passwd' is rejected, no passwd content leaked
ok  nonexistent sibling 'marketing.md' returns a clean named error, not a crash
ok  a directory target returns a clean error, not a traceback or empty content
ok  single-read transcript loads only finance.md; sales.md/product.md never appear
ok  multi-read transcript loads finance.md then sales.md in order; product.md never appears
ok  no tool call at all -> filenames_read is empty (zero context cost until accessed)
ok  loop enforces max_turns and raises when exceeded

All 9 self-tests passed.
```

The transcript tests inject a fake client, script its `tool_use`/`end_turn`
responses, and then flatten every message actually sent to the client into
one string — asserting that content unique to `sales.md` and `product.md`
(e.g. `"stage-close-probability"`, `"stickiness ratio"`) never appears when
only `finance.md` was requested. That's the checkable form of "the sibling
files remain on the filesystem, consuming zero context tokens until needed."

## Run it live (needs a key)

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python agent.py
```

It asks *"What's our Q3 revenue policy?"*, prints which reference file(s) got
read, then the final answer. This is a **best-effort demo, not a test** —
whether Claude decides to call `read_reference` at all, and for which file,
is a model judgment call each time, not a guaranteed deterministic outcome
(same caveat as every other example in this repo touching a live trigger
decision — see `knowledge/agent-skills.md`). Without `ANTHROPIC_API_KEY` set,
`agent.py` prints a one-line note and exits 0 — it never crashes.

Model id is the constant `MODEL` at the top of `agent.py` (default
`claude-haiku-4-5`, the cheapest current model). See
[`knowledge/anthropic-models.md`](../../knowledge/anthropic-models.md).

## Scope

Built: the `read_reference` tool, its path-traversal guard, the manual loop
tracking `filenames_read`, and the selective-loading transcript tests.

Deliberately **out of scope** (per the research note): SKILL.md frontmatter
parsing and the trigger decision itself (a separate, not-yet-built "anatomy
of a skill" example); the beta Claude API Skills/code-execution container;
Claude Code's own filesystem auto-discovery; script execution (see
`skill-script-execution`); reference-from-a-reference nesting beyond one
level; anything requiring a live model call to pass — the self-test is fully
offline and deterministic.
