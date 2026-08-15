# Agent Skills (`SKILL.md`)

A Skill is a directory containing `SKILL.md` (YAML frontmatter + markdown
body), optionally plus reference files and scripts. Two distinct products
share the format — don't conflate them:

- **API / claude.ai Skills** run in a sandboxed code-execution container,
  uploaded via `/v1/skills`, need the `code-execution` tool + the
  `skills-2025-10-02` beta header. `name`/`description` are server-validated
  and formally required.
- **Claude Code Skills** are pure filesystem objects: `~/.claude/skills/<name>/`
  (personal), `.claude/skills/<name>/` (project, committable), or a plugin's
  `skills/` dir. No upload, no container. All frontmatter is technically
  optional but `description` is what drives model-invocation.

## Progressive disclosure — the whole design

| Level | Loaded | Cost | Content |
|---|---|---|---|
| 1. Metadata | Always at startup | ~100 tok/skill | `name` + `description` |
| 2. Instructions | On trigger | <5k tok recommended, 500-line soft cap | SKILL.md body |
| 3. Resources/code | On reference | 0 until accessed | Extra `.md` files (read via bash) + scripts (executed via bash — code never enters context, only output does) |

## The trigger contract (what makes a description work)

- Write `description` in **third person** ("Processes X", never "I can help
  you..." or "You can use this to..."). State both *what* the skill does and
  *when* to use it, with concrete keywords a real request would contain.
  "Helps with documents" is a documented anti-pattern.
- Documented, checkable frontmatter rules (enforceable by a validator, not
  just a style guideline):
  - `name`: ≤64 chars, `[a-z0-9-]+` only, no XML tags, can't contain
    `"anthropic"` or `"claude"` as a substring.
  - `description`: non-empty, ≤1024 chars, no XML tags.
- `disable-model-invocation: true` → user-only (`/name`), hides description
  from context entirely. `user-invocable: false` → model-only, hidden from the
  `/` menu. Leaving both at default = genuinely model-invoked *and*
  user-invoked.
- Claude Code loads only name+description into the system prompt at startup;
  the full body is read via `bash: cat <skill>/SKILL.md` only once triggered —
  this is literal, observable mechanics, not a metaphor.

## Bundled scripts: `${CLAUDE_SKILL_DIR}` + `allowed-tools` (permission pre-approval)

A skill's `scripts/` dir is executed via Bash, never read into context — "only
the script's output consumes tokens." The `${CLAUDE_SKILL_DIR}` variable
expands to the skill's own directory (correct at personal/project/plugin
install locations) and is substituted in **two** places: the markdown body
*and* `Bash(...)` rules inside the `allowed-tools` frontmatter field. Using
the identical path in both is what lets Claude run a bundled script with
**no permission prompt** — **verified live on 2.1.221, see the finding below**:

```yaml
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/render.sh *)
```
```
Run `${CLAUDE_SKILL_DIR}/scripts/render.sh <csv-file>` to render the chart.
```

- Earlier docs fetches (2026-08-05/06) recorded a **v2.1.129+** version gate
  for this substitution. **Correction (re-fetched 2026-08-16): that gate no
  longer appears anywhere on the current docs page.** The only nearby version
  note now is `${CLAUDE_PROJECT_DIR}` substitution requiring v2.1.196+;
  `${CLAUDE_SKILL_DIR}` in `allowed-tools` carries no stated gate. Still check
  `claude --version` before relying on this on an old install — just don't
  cite 2.1.129 as the threshold going forward.
- `allowed-tools` grants clear at the end of the invoking turn, not the whole
  session — re-invoking the skill re-applies the grant.
- **Verified live (2026-08-16, claude 2.1.221)**: the docs' own single-token
  form `Bash(${CLAUDE_SKILL_DIR}/scripts/x.sh *)` really does suppress the
  Bash prompt. Two byte-identical project skills differing *only* in that one
  frontmatter line, run under `claude -p --permission-mode default` (no
  approval channel at all): the one with the rule executed its bundled script
  and its sentinel file appeared on disk; the one without was denied and wrote
  nothing. `disable-model-invocation: true` does not interfere with the grant.
  Working proof + real transcripts: `examples/skill-permission-suppression`
  (`run_e2e.sh`, `fixtures/{allow,deny}_transcript.jsonl`).
- `Bash(prefix *)` is a general prefix-match rule (also used for
  `Bash(git add *)`, `Bash(python3 *)`, etc.) — pinning the rule to
  interpreter+script-path (`Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/foo.py *)`,
  the two-token form `examples/skill-script-execution` ships) is a reasonable
  extrapolation but not literally demonstrated in the docs, and the 2026-08-16
  live run deliberately tested only the single-token form. **Still unverified.**
- [anthropics/claude-code#14956](https://github.com/anthropics/claude-code/issues/14956)
  (open, filed against v2.0.75, older `prefix:*` **colon** syntax) reports
  `allowed-tools` claiming to be active while the Bash command still prompts.
  Not re-litigated: the 2026-08-16 run used the current **space**-suffix syntax,
  so it is evidence about that form only, not about the colon form. Prompt
  suppression is now confirmed for the space-suffix `${CLAUDE_SKILL_DIR}` case;
  treat `prefix:*` as still suspect.
- Script-authoring guidance from the best-practices doc: "solve, don't defer"
  (handle expected errors explicitly inside the script, never let Claude
  improvise on a raw traceback), no "voodoo constants" (justify every
  hardcoded value in a comment), prefer a script over asking Claude to
  generate the same logic for anything deterministic.

**How to test prompt-suppression live without a human answering prompts:**
`claude -p` (headless) has a deterministic, documented account of what
happens to an unapproved tool call: "a non-interactive `-p` run without a
`--permission-prompt-tool` has no prompt to fall back to... the action
doesn't run and Claude keeps working" (permission-modes docs). Headless
mode's own built-in default permission mode is already `default` (Manual),
which requires approval for Bash. So the whole "does allowed-tools suppress
the prompt" question reduces to a scriptable, no-human-needed check: run the
same skill twice — once with a matching `allowed-tools` rule, once
without — under `claude -p --permission-mode default`, and check whether the
bundled script's own side effect (e.g. a sentinel file it writes) actually
happened. No prompt-answering machinery, no TTY, no guessing at the exact
denial JSON shape in advance — capture a real transcript from the live run
and build the parser against that (same order of operations
[[claude-code-mcp-connection]] used for its own checks). `--bare` cannot be
used for this test: it skips skill auto-discovery entirely, so the run
always loads full ambient context and costs closer to a real generation than
`--bare`'s fraction-of-a-cent estimate. Design: research note
[2026-08-16-skill-permission-suppression](../research/2026-08-16-skill-permission-suppression.md);
**executed 2026-08-16, result recorded above** — build:
`examples/skill-permission-suppression`.

**Observed `stream-json` shape of a *denied* headless tool call** (2.1.221,
previously undocumented anywhere in the fetched docs — captured, not guessed):

- The `tool_use` block is emitted normally; there is **no distinct "denied"
  event type**. The denial arrives as an ordinary `tool_result`:
  `{"type":"tool_result","content":"This command requires approval","is_error":true,...}`,
  with `tool_use_result: "Error: This command requires approval"` on the
  wrapping `user` event.
- The final `result` event carries a `permission_denials` array —
  `[{"tool_name":"Bash","tool_use_id":...,"tool_input":{...}}]` — empty when
  nothing was denied. This is the CLI's own accounting and the most specific
  signal available; an `is_error` tool_result *not* listed there is an
  execution failure, not a denial.
- **The run as a whole still reports success**: `"subtype":"success"`,
  `"is_error": false`, exit code 0. Anything asserting only on the top-level
  result would read a fully-denied run as a pass. Two mode-related traps in
  the same area: ambient user settings can set `permissions.defaultMode` to
  `auto` (a classifier may then approve the Bash call for reasons unrelated to
  `allowed-tools`), so pass `--permission-mode default` explicitly *and* re-read
  `permissionMode` back off the `system`/`init` event before trusting the run.

Full write-up: research note [2026-08-06-skill-script-execution](../research/2026-08-06-skill-script-execution.md).

## Reference files (Level 3 resources, not scripts)

A `reference/` (or top-level `FORMS.md`/`REFERENCE.md`-style) `.md` file is
read via the same `bash: cat <file>` primitive as `SKILL.md` itself, only
later — triggered when `SKILL.md`'s body points to it. Zero token cost until
read; "no practical limit on bundled content" (overview doc).

- Two documented organization patterns: **Pattern 1**, a short `SKILL.md`
  linking out to top-level files (`FORMS.md`, `REFERENCE.md`) — real example:
  `anthropics/skills`' `pdf` skill. **Pattern 2**, a `reference/` subdir split
  by domain so one task's tokens don't pull in unrelated domains — real
  example: the same repo's `mcp-builder` skill (`reference/{evaluation,
  mcp_best_practices,node_mcp_server,python_mcp_server}.md`).
- **One level deep, hard rule**: every reference file should be linked
  directly from `SKILL.md`, never file → file → file. Docs' stated reason:
  chained references make Claude `head -100` preview rather than fully read,
  causing partial/incomplete information — a correctness risk, not a style
  nit. Confirmed in the wild: `mcp-builder`'s four reference files contain no
  links to each other.
- **TOC for files >100 lines is only a soft recommendation** — checked
  against real usage: Anthropic's own shipped `pdf/reference.md` is 611 lines
  with **no** table-of-contents heading near the top. A linter should treat
  this as a warning, not an error; even the reference implementation doesn't
  follow it.
- **Reference syntax in the wild is inconsistent** — `pdf/SKILL.md` points to
  `FORMS.md`/`REFERENCE.md` via bare filename mentions in prose (no markdown
  link brackets); `mcp-builder/SKILL.md` uses real `[text](./reference/x.md)`
  links throughout. Both work identically at runtime (Claude reads a
  filename via bash, not a rendered hyperlink) — any tool that parses
  "what does this SKILL.md reference" must handle both forms or it will
  miss real, working references.

Full write-up: research note [2026-08-06-skill-reference-files](../research/2026-08-06-skill-reference-files.md).

## Gotchas

- `` !`shell command` `` in a skill body is pre-executed by **Claude Code**
  (not the model) before the rendered content reaches Claude — output gets
  inlined, the command itself never does. Claude-Code-specific extension, not
  part of the portable [agentskills.io](https://agentskills.io) open standard.
- Whether Claude Code *itself* rejects a rule-violating frontmatter at load
  time is unconfirmed from a primary source (the API upload path definitely
  validates server-side; Claude Code's troubleshooting docs suggest a
  malformed block just silently degrades to "no description to match
  against" rather than a hard error) — validate before shipping, don't rely
  on the runtime to catch it.
- Triggering itself (does Claude actually pick this skill for this prompt) is
  not deterministically testable offline — it's a live, model-dependent
  behavior. What *is* deterministically testable offline is frontmatter
  conformance to the rules above. Anthropic's own answer to the triggering
  question is the `skill-creator` plugin's eval loop (baseline A/B with
  `skillOverrides: "off"`), not a unit test.

Sources: [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview), [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), [Extend Claude with skills](https://code.claude.com/docs/en/skills), [anthropics/skills](https://github.com/anthropics/skills) — fetched 2026-08-05 and re-fetched 2026-08-06; [anthropics/claude-code#14956](https://github.com/anthropics/claude-code/issues/14956) — open issue, checked 2026-08-06.

Research notes: [2026-08-05-skill-anatomy](../research/2026-08-05-skill-anatomy.md), [2026-08-06-skill-script-execution](../research/2026-08-06-skill-script-execution.md), [2026-08-06-skill-reference-files](../research/2026-08-06-skill-reference-files.md).
