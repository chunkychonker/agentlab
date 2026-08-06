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
**no permission prompt**:

```yaml
allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/render.sh *)
```
```
Run `${CLAUDE_SKILL_DIR}/scripts/render.sh <csv-file>` to render the chart.
```

- Requires Claude Code **v2.1.129+** for the `allowed-tools` substitution
  specifically (earlier versions leave the literal string unmatched → still
  prompts). Check `claude --version` before relying on this.
- `allowed-tools` grants clear at the end of the invoking turn, not the whole
  session — re-invoking the skill re-applies the grant.
- `Bash(prefix *)` is a general prefix-match rule (also used for
  `Bash(git add *)`, `Bash(python3 *)`, etc.) — pinning the rule to
  interpreter+script-path (`Bash(python3 ${CLAUDE_SKILL_DIR}/scripts/foo.py *)`)
  is a reasonable extrapolation but not literally demonstrated in the docs;
  verify live.
- **Unresolved as of the docs' own citation**: [anthropics/claude-code#14956](https://github.com/anthropics/claude-code/issues/14956)
  (open, v2.0.75, older `prefix:*` colon syntax) reports `allowed-tools`
  reporting itself active while the Bash command still prompts. Not confirmed
  fixed on current syntax/version — treat permission-prompt suppression as
  best-effort and verify with a real session, same as triggering itself.
- Script-authoring guidance from the best-practices doc: "solve, don't defer"
  (handle expected errors explicitly inside the script, never let Claude
  improvise on a raw traceback), no "voodoo constants" (justify every
  hardcoded value in a comment), prefer a script over asking Claude to
  generate the same logic for anything deterministic.

Full write-up: research note [2026-08-06-skill-script-execution](../research/2026-08-06-skill-script-execution.md).

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

Sources: [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview), [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), [Extend Claude with skills](https://code.claude.com/docs/en/skills) — fetched 2026-08-05 and re-fetched 2026-08-06; [anthropics/claude-code#14956](https://github.com/anthropics/claude-code/issues/14956) — open issue, checked 2026-08-06.

Research notes: [2026-08-05-skill-anatomy](../research/2026-08-05-skill-anatomy.md), [2026-08-06-skill-script-execution](../research/2026-08-06-skill-script-execution.md).
