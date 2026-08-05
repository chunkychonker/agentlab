# Skill anatomy (`commit-message`)

A minimal, correctly-shaped Claude Code **Agent Skill** — `SKILL.md`
frontmatter + body, nothing else — plus an offline validator that checks any
`SKILL.md`'s frontmatter against the documented, checkable rules for `name`
and `description`.

From the research note:
[`research/2026-08-05-skill-anatomy.md`](../../research/2026-08-05-skill-anatomy.md).
Background also collected in
[`knowledge/agent-skills.md`](../../knowledge/agent-skills.md).

## What's here

| File | What it is |
|------|-----------|
| `skills/commit-message/SKILL.md` | The skill itself: valid frontmatter, a third-person trigger description, one use of Claude-Code's dynamic context injection. |
| `validate_skill.py` | Pure offline validator — no network, no `anthropic` import, no API key. Parses flat frontmatter and checks it against the documented rules. |
| `test_validate_skill.py` | Offline self-test: proves the validator actually enforces each rule, not just returns `True`. |

## Progressive disclosure — why a skill is shaped this way

A Skill is a filesystem directory (`.claude/skills/<name>/` or
`~/.claude/skills/<name>/`) containing `SKILL.md`. Claude Code loads it in
three tiers, and this example only needs the first two:

| Level | When loaded | Cost | Content |
|---|---|---|---|
| 1. Metadata | Always, at session startup | ~100 tokens/skill | `name` + `description` from frontmatter |
| 2. Instructions | Only when the skill is triggered | <5k tokens recommended, 500-line soft cap | The `SKILL.md` body |
| 3. Resources/code | Only when referenced | 0 until accessed | Bundled reference files and scripts (out of scope here — see "packaging a skill with reference files" in `BACKLOG.md`) |

Claude Code loads every installed skill's level-1 metadata into the system
prompt at startup, then reads the full body with `cat <skill>/SKILL.md` only
once the skill is actually triggered — the model never pays for the body of
a skill it didn't need.

## What makes the trigger "clear"

`description` is the entire model-invocation contract at level 1 — it is
what gets matched against a user's request to decide whether to load the
skill body. `skills/commit-message/SKILL.md`'s description:

> Drafts a Conventional-Commits-style commit message from the currently
> staged git diff. Use when the user asks for a commit message, asks what to
> commit, or asks you to describe staged changes.

This follows the documented pattern: third person, states *what* it does and
*when* to use it, and includes concrete trigger phrases ("commit message",
"what to commit") a real request would contain — not a vague "Helps with
git" description.

Both `disable-model-invocation` and `user-invocable` are left unset (their
Claude Code defaults: `false`/`true`), so the skill is genuinely both
**model-invoked** (Claude can trigger it on its own from the description)
and **user-invoked** (`/commit-message` triggers it directly).

## Dynamic context injection

The body uses `` !`git status --short` `` and `` !`git diff --cached` ``.
Claude Code pre-executes these shell commands *before* the rendered skill
content reaches the model — Claude sees the diff output already inlined, it
never sees or runs the command itself. This is how the skill grounds itself
in live repo state with zero tool calls. It is a Claude-Code-specific
extension, not part of the portable [Agent Skills open
standard](https://agentskills.io) the frontmatter shape otherwise follows.

## Installing the skill

Project-local (committed, shared with the team):

```bash
mkdir -p .claude/skills
cp -r examples/skill-anatomy/skills/commit-message .claude/skills/
```

Personal (available in every project):

```bash
cp -r examples/skill-anatomy/skills/commit-message ~/.claude/skills/
```

Checked against a live `claude --version` (2.1.221) install: no bundled
skill or plugin command is named `commit-message` (there is an unrelated
`/commit` command from the `commit-commands` plugin), so this name does not
shadow anything. Claude Code resolves project skills over bundled ones of
the same name by design regardless, so a future collision would not be a
correctness bug — just a possible surprise worth renaming for.

## Run the self-test (no API key, no network)

```bash
cd examples/skill-anatomy
python3 test_validate_skill.py
```

Expected output:

```
ok  skills/commit-message/SKILL.md validates with zero errors
ok  uppercase letter in name -> charset error
ok  name containing 'claude' -> reserved-word error
ok  missing description -> error
ok  1025-char description -> length error
ok  description containing an XML tag -> error
ok  parse_frontmatter raises ValueError with no opening '---'
ok  parse_frontmatter raises ValueError with no closing '---'
ok  validate_skill propagates FileNotFoundError for a missing path
ok  malformed frontmatter -> exactly one error Finding, no exception

All 10 self-tests passed.
```

## Run the validator directly

```bash
python3 validate_skill.py skills/commit-message/SKILL.md
```

```
ok  no findings

0 error(s), 0 warning(s)
```

Exit code is `0` iff there are zero `error`-level findings (`warning`-level
findings — like a possibly non-third-person description, or an
over-500-line body — do not fail the run, since those are soft best
practices, not hard rules). Point it at any other `SKILL.md` to check it the
same way.

## Verifying triggering (manual, live — not part of the automated self-test)

Whether a `description` actually causes Claude to pick this skill for a
given prompt is a live, model-dependent behavior — not something checkable
offline. To confirm it manually:

```bash
cd /path/to/any/git/repo
git add -A   # or stage something specific
claude
```

Then, in the session:

1. Ask *"what should I commit this as?"* — the skill should trigger
   automatically from its `description` and produce a message grounded in
   the actual staged diff.
2. Try `/commit-message` directly — this should trigger the same skill body
   via the explicit user-invocation path.
3. With nothing staged, repeat step 1 — the skill should say plainly that
   nothing is staged rather than inventing a message.

The documented, more rigorous version of this check is a baseline A/B (same
prompt, skill enabled vs. `skillOverrides: "off"`, across multiple runs) —
that is what the `skill-creator` plugin's eval loop
(`evals.json`/`grading.json`/`benchmark.json`) automates, and is out of
scope here since it spends real API calls for eval scoring.

## Open question carried from the research note

Whether Claude Code itself rejects a rule-violating `SKILL.md` at load time
(vs. only the API's `/v1/skills` upload path validating server-side) is not
confirmed from a primary source. The Claude Code troubleshooting docs
suggest malformed frontmatter just silently degrades — the skill loads with
no `description` to match against, so `/commit-message` still works but the
skill stops being model-invokable — rather than erroring loudly. That is
exactly the failure mode `validate_skill.py` exists to catch before it ships.

## Explicitly out of scope

Bundled reference files and scripts read on demand (level 3 — see the "A
skill that shells out to a local script" and "packaging a skill with
reference files" `BACKLOG.md` items, not yet built), calling the live model
to prove triggering (see above), and anything about API/claude.ai Skills
(sandboxed code-execution container, `skills-2025-10-02` beta header,
`/v1/skills`) — a different product from Claude Code filesystem skills.
