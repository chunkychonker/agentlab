# Anatomy of a skill: a minimal model-invoked skill with a clear trigger

## Question

What exactly is a Claude "Agent Skill" (the `SKILL.md` format), what makes it
*model-invoked* with a *clear trigger*, and what's the smallest real,
same-day-buildable increment that demonstrates the anatomy correctly and can be
verified without burning API calls?

## Findings

Primary sources, all fetched today (2026-08-05), current as of Claude Code
~v2.1.21x:

- [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) — cross-product concept doc (claude.ai, API, Claude Code).
- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) — the authoritative "how to write a good SKILL.md" guide, includes an evaluation-driven-development workflow and a checklist.
- [Extend Claude with skills](https://code.claude.com/docs/en/skills) — the Claude-Code-specific mechanics: file locations, full frontmatter reference, invocation control, dynamic context injection, troubleshooting.
- [anthropics/skills](https://github.com/anthropics/skills) — the public repo of example skills + the `template/` skeleton + a `spec/`.

No sources found older than a few months for the current frontmatter shape;
the feature and its docs are clearly under active iteration (the frontmatter
reference table cites specific Claude Code version gates like v2.1.196,
v2.1.199, v2.1.218 for individual fields — i.e. this is a fast-moving surface,
re-check before relying on any field not marked available "before" a recent
version).

### What a Skill actually is

A Skill is a directory containing at minimum `SKILL.md`: YAML frontmatter
(`---`-delimited) + a markdown body. Two products use overlapping but distinct
mechanics:

1. **API/claude.ai Skills** run inside a sandboxed **code execution
   container** — require the `code-execution` tool + a `skills-2025-10-02` beta
   header, uploaded via `/v1/skills`. `name`/`description` are formally
   **required** there (validated server-side).
2. **Claude Code Skills** are pure **filesystem** objects — a directory under
   `~/.claude/skills/<name>/` (personal) or `.claude/skills/<name>/` (project,
   committed to the repo) or a plugin's `skills/` dir. No upload, no
   container, no beta header. All frontmatter fields are technically
   *optional* here, but `description` is "recommended" because it's what
   drives model-invocation. This project builds and runs entirely inside
   Claude Code, so this is the mechanics that matter for the build.

### Progressive disclosure — the core design idea

Three load tiers, confirmed identically in both the overview and best-practices
docs:

| Level | When loaded | Cost | Content |
|---|---|---|---|
| 1. Metadata | Always, at session/request startup | ~100 tokens/skill | `name` + `description` from frontmatter |
| 2. Instructions | When the skill is triggered | recommended <5k tokens (Claude Code caps body at 500 lines as a soft limit) | SKILL.md body |
| 3. Resources/code | Only when referenced | 0 until accessed | Bundled `.md` reference files (read via bash) and scripts (executed via bash — the script's *code* never enters context, only its *output* does) |

This is why "Anatomy of a skill" and "packaging a skill with reference files"
(the next backlog item) are correctly split: a minimal skill only needs levels
1–2; level 3 (bundled reference files Claude reads on demand) is a separate,
larger increment and is explicitly out of scope for today.

### What makes a trigger "clear" — the model-invocation contract

- `description` is matched against the user's request to decide whether to
  auto-load the skill. Best-practices doc, verbatim: "Always write in third
  person" ("Processes Excel files...", not "I can help you..." or "You can use
  this to..."), and it must state **both** what the skill does **and** when to
  use it, with concrete trigger keywords a real request would use. Vague
  descriptions ("Helps with documents") are explicitly called out as
  anti-patterns.
- Documented frontmatter validation rules for `name`/`description` (from the
  overview page, "Skill structure" section — these are the concrete,
  checkable rules a validator can enforce):
  - `name`: ≤64 chars, only lowercase letters/numbers/hyphens, no XML tags, must not contain the reserved words `"anthropic"` or `"claude"`.
  - `description`: non-empty, ≤1024 chars, no XML tags.
- Claude Code additionally supports `disable-model-invocation: true` (skill
  becomes manual-only, invoked with `/name`) and `user-invocable: false`
  (skill becomes model-only, hidden from the `/` menu) — the two knobs that
  let you deliberately choose "model-invoked," "user-invoked," or both. A
  **minimal model-invoked skill with a clear trigger**, precisely, is one that
  leaves both flags at their default (`false`) and relies entirely on a
  well-written `description`.
- Claude Code loads skill *metadata* into the system prompt at startup and
  reads the full body via `bash: cat <skill>/SKILL.md` only once triggered —
  this is directly observable behavior described in the "How Claude accesses
  Skill content" section of the overview doc, not an implementation detail I'm
  inferring.

### Dynamic context injection (Claude-Code-specific extension)

`` !`shell command` `` inside a skill body is pre-executed by Claude Code
*before* the rendered skill content reaches the model — the model sees the
command's output already inlined, never the command itself. This is how the
docs' own worked example (`summarize-changes`, injecting `` !`git diff HEAD` ``)
grounds the skill in live repo state without Claude needing a tool call. Useful
primitive, but it's a Claude-Code-only feature, not part of the portable
[Agent Skills open standard](https://agentskills.io) the format otherwise
follows — worth flagging since this repo aims for "runnable," and this feature
only runs correctly inside an actual Claude Code session (can't be
unit-tested by literally invoking Claude without spending a real turn).

### What's *not* independently verifiable offline

- Whether a given `description` actually causes Claude to trigger the skill on
  a given prompt is a live, model-dependent behavior — the best-practices doc's
  own recommended methodology is a baseline A/B (same prompt, skill enabled vs.
  `skillOverrides: "off"`) across multiple runs and models, not a single
  deterministic check. There's a whole tool for this now
  (`skill-creator` plugin, `/plugin install skill-creator@claude-plugins-official`)
  that automates exactly that loop into `evals/evals.json` + `grading.json` +
  `benchmark.json` — genuinely useful, but installing/running a plugin and
  burning real API calls for eval scoring is out of scope for one day's build.
  What **is** independently, deterministically verifiable offline is whether a
  `SKILL.md`'s frontmatter *conforms to the documented, checkable rules above*
  — that's the self-test this cycle's build proposal targets.

## Build proposal

### Intent

Ship one real, installable Claude Code skill (`commit-message`) that
demonstrates correct skill anatomy — required frontmatter, a clear
third-person trigger description, a concise instruction body, one use of
dynamic context injection — **and** a small offline validator that checks any
`SKILL.md`'s frontmatter against the documented, checkable rules from the
primary sources above. Out of scope: bundled reference files/scripts beyond
the validator itself (that's the next backlog item, "packaging a skill with
reference files"), calling the live model to prove triggering (not
deterministically testable offline — the README documents how to check this
manually), and anything touching API/claude.ai Skills (container, beta
headers, `/v1/skills` — a different product from Claude Code filesystem
skills).

### Behavioral spec

**`examples/skill-anatomy/skills/commit-message/SKILL.md`** — the skill itself:

- Frontmatter: `name: commit-message` (conforms: 14 chars, lowercase+hyphens
  only, no reserved words), `description` stating what it does ("Drafts a
  Conventional-Commits-style commit message from the currently staged git
  diff") and when to use it ("Use when the user asks for a commit message, asks
  what to commit, or asks you to describe staged changes"), third person,
  non-empty, under 1024 chars.
- Body: uses `` !`git diff --cached` `` and `` !`git status --short` `` dynamic
  injection to pull live staged-diff context, then instructs Claude to produce
  a `type(scope): summary` header + optional body, and to say plainly if
  nothing is staged. Under ~40 lines — deliberately small, this is the
  "anatomy" example, not a feature-complete skill.
- Both `disable-model-invocation` and `user-invocable` left at their Claude
  Code defaults (`false`/`true`) — i.e. genuinely model-invoked *and*
  user-invoked via `/commit-message`, per spec.

**`examples/skill-anatomy/validate_skill.py`** — pure offline validation, no
network, no `anthropic` import, no API key:

- Input: a path to a `SKILL.md` file.
- Parses YAML frontmatter (the flat `key: value` block between the first two
  `---` lines — no external YAML dependency needed since Skill frontmatter for
  this example is flat scalars, not nested structures).
- Output: a list of `(level, field, message)` findings, `level` ∈
  `{"error", "warning"}`. Checkable, concrete rules enforced (each traceable to
  a doc quote above):
  - `name` present → error if missing name AND missing description (fallback
    behavior differs; state clearly what's checked). If `name` present: error
    if >64 chars; error if it contains characters outside `[a-z0-9-]`; error if
    it contains `<`/`>`; error if it contains the substring `"anthropic"` or
    `"claude"`.
  - `description`: error if missing or empty; error if >1024 chars; error if
    it contains `<`/`>`; warning (heuristic, not a hard doc rule) if it starts
    with `"I "` or contains `"You can"` — flagged as a *warning* not an
    *error* since third-person-ness isn't machine-checkable in general, only
    this crude heuristic.
  - Body: warning (not error — this is a "keep under 500 lines" best practice,
    not a hard constraint) if the body exceeds 500 lines.
- Exit code 0 iff zero `error`-level findings; prints each finding.
- Failure modes stated in the module docstring: malformed/missing frontmatter
  delimiters → a single error finding naming the problem (never a raw
  exception); unreadable file path → `FileNotFoundError` propagates (a
  legitimate boundary failure, not swallowed).

**Acceptance criteria ("it works"):**

1. `python validate_skill.py skills/commit-message/SKILL.md` exits 0 and
   prints no `error` findings for the shipped skill.
2. `python test_validate_skill.py` (offline, no key, no network) passes and
   demonstrates the validator actually enforces each rule — not just returns
   `True` — by constructing at least these fixtures and asserting the expected
   finding appears:
   - a valid skill → zero errors
   - `name` with an uppercase letter → name-length/charset error
   - `name` containing `"claude"` → reserved-word error
   - `description` missing → error
   - `description` of 1025 chars → length error
   - `description` containing an XML tag (`<foo>`) → error
3. `examples/skill-anatomy/README.md` documents: the three progressive
   disclosure levels (with a table), how to install the skill
   (`cp -r skills/commit-message ~/.claude/skills/` or project-local
   `.claude/skills/`), and the **manual, live** verification step (open
   `claude` in a repo with a staged change, ask "what should I commit this
   as?", confirm the skill triggers and produces a message; also try
   `/commit-message` directly) — explicitly labeled as manual/live since it
   can't be part of the automated self-test.

### Interfaces (stubs only — builder implements bodies)

```python
# examples/skill-anatomy/validate_skill.py

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Finding:
    level: str        # "error" | "warning"
    field: str         # "name" | "description" | "body"
    message: str

def parse_frontmatter(text: str) -> tuple[dict[str, str], str]:
    """Split SKILL.md into (frontmatter dict, body).

    Failure modes: raises ValueError if the file does not start with a
    '---' delimited block (never silently returns an empty dict).
    """

def validate_name(name: str | None) -> list[Finding]: ...

def validate_description(description: str | None) -> list[Finding]: ...

def validate_body(body: str) -> list[Finding]: ...

def validate_skill(path: Path) -> list[Finding]:
    """Read, parse, and validate a SKILL.md file.

    Failure modes: FileNotFoundError propagates unchanged (boundary
    failure). A malformed frontmatter block produces one error Finding,
    never an exception.
    """

def main(argv: list[str]) -> int:
    """CLI entry point. Returns 0 iff no error-level findings."""
```

### Open questions

- Whether Claude Code enforces these frontmatter rules itself at load time (vs.
  only the API/`/v1/skills` upload path validating them) isn't stated
  explicitly in the Claude-Code-specific doc — the overview doc's "Skill
  structure" section states the rules but its examples/prose lean toward the
  API upload path. The troubleshooting section of the Claude Code doc *implies*
  no client-side validation ("If the frontmatter YAML is malformed, Claude
  Code loads the skill body with empty metadata... `/skill-name` still works
  but Claude has no `description` to match against") — i.e. a bad `name` might
  silently degrade discoverability rather than error loudly in Claude Code.
  This doesn't change the value of the validator (catching the problem before
  a silent degradation is exactly the point) but the exact runtime behavior on
  a rule violation in Claude Code specifically (vs. the API's server-side
  rejection) is not confirmed from a primary source and is worth a one-line
  caveat in the README.
- Whether `commit-message` collides with a bundled Claude Code skill/command
  name wasn't checked against a live `claude --version` install (no Claude
  Code CLI available in this research environment) — the builder should
  `claude --version` and `/help` (or check the [commands
  reference](https://code.claude.com/docs/en/commands)) before finalizing the
  name, and rename if it shadows something unexpected. Docs confirm project
  skills override bundled skills of the same name by design, so a collision
  is not a correctness bug, only a possible surprise worth a README note.
