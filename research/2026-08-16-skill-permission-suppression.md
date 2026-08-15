# `${CLAUDE_SKILL_DIR}` + `allowed-tools`: does it actually suppress the permission prompt?

## Question

`knowledge/agent-skills.md` (from the 2026-08-06 `skill-script-execution` cycle)
records the `${CLAUDE_SKILL_DIR}` + `allowed-tools` pattern as "documented" but
flags an open GitHub bug report and says "verify live before relying on it."
Does a bundled script whose exact command appears in both the skill body and
an `allowed-tools: Bash(...)` rule actually run **without a permission
prompt**, against the real `claude` CLI, checked mechanically rather than by
eyeballing a transcript?

## Findings

### The mechanism, per current docs (re-fetched 2026-08-16, `code.claude.com/docs/en/skills`)

The docs' wording is essentially unchanged from the 2026-08-05/06 fetch already
recorded in `knowledge/agent-skills.md`, with one correction: **the earlier
"requires Claude Code v2.1.129+" version gate no longer appears anywhere in
the current page.** The only nearby version gate now documented is
`${CLAUDE_PROJECT_DIR}` substitution requiring v2.1.196+; `${CLAUDE_SKILL_DIR}`
substitution in `allowed-tools` carries no version caveat in the current text.
Verbatim: "Claude Code substitutes `${CLAUDE_SKILL_DIR}` and
`${CLAUDE_PROJECT_DIR}` in two places: the skill's markdown content, and Bash
rules in the `allowed-tools` frontmatter. ... Using the same variable in both
places lets a skill run a bundled script without a permission prompt." The
docs' own worked example is a single-token script-path prefix:
`allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/render.sh *)`.

Also newly relevant: "Workspace trust doesn't gate this field. Claude Code
applies a project skill's `allowed-tools` whenever you or Claude invoke the
skill, including in a `-p` run in a folder you've never trusted." — meaning a
`-p` run against a throwaway project directory (no prior trust relationship)
is a valid way to test this, matching how `mcp-connect-claude-code/run_e2e.sh`
already sidesteps the trust dialog for MCP config.

This build environment's `claude --version` is `2.1.221` — well past every
version gate mentioned anywhere in the current docs for this feature.

### The open bug report, re-checked

[anthropics/claude-code#14956](https://github.com/anthropics/claude-code/issues/14956)
is still **open**, filed 2025-12-21 against v2.1.75, zero comments as of
2026-08-16. Its repro uses `allowed-tools: Bash(say -v "Samantha":*)` — the
**colon** `:*` suffix — whereas every current-docs example uses the **space**
`*` suffix (`Bash(git diff *)`, with the docs explicitly noting "the space
before `*` is important: without it, `Bash(git diff*)` would also match
`git diff-index`"). This is a plausible-but-unconfirmed reason the repro
doesn't reproduce on current syntax: not evidence the mechanism works, just a
reason the one filed report may not be representative of the
`${CLAUDE_SKILL_DIR}`-pattern specifically (the doc's flagship example, and
what `examples/skill-script-execution` already ships, both use the space
syntax). Still unresolved from a primary source — this cycle's job is to get
a primary-source answer for the space-syntax, `${CLAUDE_SKILL_DIR}`-matched
case specifically.

### How to test this *without* a human in the loop

`claude -p` (headless mode) is exactly the tool for this, and the docs give a
clean, deterministic account of what happens to an unapproved tool call there
(`code.claude.com/docs/en/permission-modes`, re-fetched 2026-08-16):

> "Sessions that can't prompt: a non-interactive `-p` run without a
> `--permission-prompt-tool` has no prompt to fall back to. When repeated
> blocks reach a threshold, the action doesn't run and Claude keeps working...
> Claude Code doesn't stop the run."

And headless mode's own baseline permission mode is already the strict one:
per the "which mode a session starts in" table, `claude -p` or the Agent SDK
built-in default is `default` (Manual) mode — not the classifier-driven
`auto` mode some interactive sessions get on Pro/Max/Team plans. Manual mode
requires approval for Bash commands (`code.claude.com/docs/en/permissions`:
"Bash commands ... Approval required: Yes, except a built-in set of read-only
commands"). So: **run the exact same skill twice, once with a matching
`allowed-tools` rule and once without, under `claude -p --permission-mode
default`, and see whether the bundled script's own side effect happens.** No
prompt-answering machinery needed — an unapproved call simply never executes.

Two more mechanics make this scriptable and unambiguous:

- `--bare` is **not usable here** — it explicitly skips "auto-discovery of
  hooks, skills, plugins, MCP servers, auto memory, and CLAUDE.md" (headless
  docs, re-fetched 2026-08-16), which would skip skill loading entirely. This
  test must run without `--bare`, which means (per the `mcp-connect-claude-code`
  precedent) it always loads ambient ~/.claude context and therefore always
  costs closer to that example's real $0.026-per-call figure than the
  fraction-of-a-cent `--bare` estimate, regardless of whether
  `ANTHROPIC_API_KEY` is set.
- User-invoked skills work in `-p` mode: "include `/skill-name` in the prompt
  string and Claude Code expands it before running" (headless docs). Combined
  with `disable-model-invocation: true` in the test skills' frontmatter, this
  removes the *separate*, genuinely-nondeterministic question of whether the
  model chooses to trigger the skill at all — `knowledge/agent-skills.md`
  already scopes that question out ("not deterministically testable offline
  — it's a live, model-dependent behavior"), and this build should too. This
  cycle only tests permission suppression, given the skill is already running.

### What's still genuinely unknown until a live run

The exact `stream-json` event shape for a *denied* tool call in headless mode
is not stated verbatim anywhere in the fetched docs (denial is described only
behaviorally: "the action doesn't run"). `examples/mcp-connect-claude-code`
hit the same gap for its own checks and resolved it by capturing one real
transcript and building the parser against that, rather than the docs —
this build should do the same rather than guessing the denial event's shape
in advance.

### Prior art already in this repo

- `examples/skill-script-execution` already ships the `${CLAUDE_SKILL_DIR}` +
  `allowed-tools` pattern for a real script, but its own README explicitly
  says it did **not** verify the no-prompt claim live ("verify it live (see
  below)... **not confirmed from a primary source**") — this cycle closes
  that gap rather than duplicating the example.
- `examples/mcp-connect-claude-code/run_e2e.sh` + `assert_stream.py` is the
  direct structural template: impure shell orchestrator (builds throwaway
  config, invokes the real `claude` CLI, bounded retries) handing a captured
  transcript to a pure, independently-unit-tested verifier.

## Build proposal

**Intent.** Prove or disprove, against the real `claude` CLI (not the SDK's
in-memory client, not a transcript read by eye), whether an `allowed-tools`
rule that reuses the exact `${CLAUDE_SKILL_DIR}`-substituted command from the
skill body actually lets a bundled script run in a headless session with no
approval available — and correct `knowledge/agent-skills.md` with whatever
is actually observed. Out of scope: whether the model *chooses* to trigger a
skill (already flagged elsewhere as not deterministically testable, and
sidestepped here via explicit `/name` invocation); the `python3 <script>`
two-token prefix variant `examples/skill-script-execution` already flagged
as unconfirmed (this build tests the docs' own single-token
`Bash(${CLAUDE_SKILL_DIR}/scripts/x.sh *)` form only, to keep the variable
count to one); re-litigating issue #14956's colon-syntax repro.

**Where it goes:** `examples/skill-permission-suppression/` (new — no
collision with `examples/skill-script-execution*`, no open PR or branch
touching this topic as of 2026-08-16).

**Shape**, mirroring `examples/mcp-connect-claude-code`:

```
examples/skill-permission-suppression/
  skills/verify-allow/SKILL.md        # allowed-tools matches the body command
  skills/verify-allow/scripts/mark.sh # writes a sentinel file, given a path arg
  skills/verify-deny/SKILL.md         # identical body + script, NO allowed-tools
  skills/verify-deny/scripts/mark.sh
  assert_transcript.py                # pure: find_bash_call(events, cmd_substr) -> Outcome
  test_assert_transcript.py           # offline, mutation-based, no live call
  run_e2e.sh                          # orchestrator: builds a throwaway project
                                       # dir per skill, runs claude -p twice,
                                       # checks BOTH the transcript-parsed
                                       # outcome AND the sentinel file's
                                       # actual existence
  fixtures/                           # real transcripts captured during build,
                                       # added once the live run has happened
  README.md
```

- `scripts/mark.sh`: `#!/usr/bin/env bash` + `set -euo pipefail`; writes
  `ran at $(date -u +%FT%TZ)` to the path given as `$1`, nothing else — a
  real, checkable side effect independent of the model's own claims.
- `SKILL.md` bodies: `disable-model-invocation: true`, a description marking
  it test-only, and a body of exactly `Run this command and nothing else,
  then reply DONE: ${CLAUDE_SKILL_DIR}/scripts/mark.sh $ARGUMENTS`.
  `verify-allow`'s frontmatter adds
  `allowed-tools: Bash(${CLAUDE_SKILL_DIR}/scripts/mark.sh *)` (the docs'
  own single-token pattern); `verify-deny` omits `allowed-tools` entirely —
  the only difference between the two skills.
- `run_e2e.sh`, per skill: `mktemp -d` a scratch project dir, copy the one
  skill into `<dir>/.claude/skills/<name>/`, `cd` into it, run
  `claude -p "/verify-allow $SENTINEL" --permission-mode default --model
  haiku --output-format stream-json --verbose > transcript.jsonl`
  (no `--bare` — see Findings), then check **both**: (a) does
  `assert_transcript.py` find a Bash call matching `mark.sh` that
  succeeded/was denied/never appeared, and (b) does the sentinel file exist
  on disk. Bounded retries (`MAX_ATTEMPTS=2` per skill, matching
  `mcp-connect-claude-code`) to absorb tool-choice noise, never unbounded.
  Falls back to ambient auth exactly like the existing example if
  `ANTHROPIC_API_KEY` is unset, and says so on stderr.
- `assert_transcript.py`: one pure function,
  `find_bash_call(events: list[dict], command_substring: str) -> Outcome`
  where `Outcome` is an enum of `NOT_ATTEMPTED | DENIED | SUCCEEDED` inferred
  from whatever the transcript actually contains (a `tool_use` block naming
  Bash with a matching command; presence/absence/error-state of its
  `tool_result`). Do not hardcode the exact denial message before seeing a
  real one — build this against the first live-captured transcript, the same
  order of operations `mcp-connect-claude-code` used, and lock the result in
  as a fixture immediately after.

**"It works" — acceptance criteria:**

1. `python3 test_assert_transcript.py` passes offline, no network, no key:
   the real-run fixture (once captured) parses to the correct `Outcome`, plus
   at least one mutation test per `Outcome` value.
2. `./run_e2e.sh` produces, for `verify-allow`: sentinel file exists AND
   `assert_transcript.py` reports `SUCCEEDED`. For `verify-deny`: sentinel
   file does **not** exist AND `assert_transcript.py` reports `DENIED` or
   `NOT_ATTEMPTED` (not `SUCCEEDED`).
3. If the transcript-parsed outcome and the sentinel-file ground truth ever
   disagree (e.g. transcript looks like success but no file appeared), that
   is a reportable finding, not a swallowed inconsistency — `run_e2e.sh`
   fails loudly rather than picking one signal to trust.
4. The README states the exact result observed for the space-syntax,
   `${CLAUDE_SKILL_DIR}`-matched, single-token case — "suppresses the prompt"
   or "does not" — with the real transcript evidence, and
   `knowledge/agent-skills.md`'s "verify live" line is updated to point at
   this finding instead of remaining an open question.
5. Cost is bounded and disclosed: worst case 2 skills × `MAX_ATTEMPTS=2` ×
   ~$0.026 (the `mcp-connect-claude-code` non-`--bare` observed cost) ≈
   $0.10 total, using `--model haiku`; state this plainly in the README the
   way the reference example does.

## Open questions

- The exact `stream-json` shape of a denied headless tool call is unknown
  until the live run — the build must capture it rather than guess it.
- Whether `disable-model-invocation: true` has any interaction with
  `allowed-tools` grant behavior is undocumented; assumed independent
  (one gates *whether* the skill loads via the model, the other gates a
  *tool* once the skill is already active) but not confirmed from a primary
  source.
- Whether issue #14956's failure is really syntax-specific (colon vs. space)
  or would still reproduce on current syntax is not resolved by this note —
  only by this build's live run, and only for the single-token case.
- Whether a project-level skill in a throwaway, never-trusted directory
  behaves identically to one installed at `~/.claude/skills/` for this
  specific mechanism — the docs say workspace trust doesn't gate
  `allowed-tools`, but that line wasn't tested against this exact scenario
  by a primary source in this cycle.

Sources: [Extend Claude with skills](https://code.claude.com/docs/en/skills) (re-fetched 2026-08-16), [Run Claude Code programmatically / headless](https://code.claude.com/docs/en/headless) (re-fetched 2026-08-16), [Choose a permission mode](https://code.claude.com/docs/en/permission-modes) (re-fetched 2026-08-16), [Configure permissions](https://code.claude.com/docs/en/permissions) (re-fetched 2026-08-16), [anthropics/claude-code#14956](https://github.com/anthropics/claude-code/issues/14956) (open, filed 2025-12-21, checked 2026-08-16), local `claude --version` = `2.1.221` (checked 2026-08-16). Builds on `knowledge/agent-skills.md`, `examples/skill-script-execution` (`research/2026-08-06-skill-script-execution.md`), and `examples/mcp-connect-claude-code` (`research/2026-08-08-mcp-connect-claude-code.md`).
