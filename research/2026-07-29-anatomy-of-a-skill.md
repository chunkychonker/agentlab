# Anatomy of a skill: a minimal model-invoked skill with a clear trigger

## Question

What exactly *is* an Agent Skill (SKILL.md), what makes it "model-invoked" —
i.e. what mechanism decides Claude should load it and when — and what is the
smallest faithful, runnable increment this repo can ship to demonstrate that
mechanism today?

## Findings

### What a Skill is, structurally

A Skill is a directory whose entry point is `SKILL.md`: YAML frontmatter
(`name`, `description`, plus optional fields) followed by a markdown body,
optionally alongside other files (`references/`, `scripts/`, `assets/`).
[Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
(fetched 2026-07-29, undated but current — reflects Claude Code v2.1.x
version-gated features, so actively maintained) and the
[engineering blog post](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills)
(Skills launched 2025-10-16; open standard released 2025-12-18).

**Frontmatter validation rules** (overview + [best-practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices), both fetched 2026-07-29):
- `name`: required, ≤64 chars, lowercase letters/digits/hyphens only, no XML
  tags, cannot contain `"anthropic"` or `"claude"`.
- `description`: required, non-empty, ≤1024 chars, no XML tags. **Must state
  both what the skill does and when to use it** — "descriptions without
  trigger conditions are the primary reason skills fail to load when they
  should" (paraphrased from best-practices guidance). Write in third person
  ("Processes Excel files...", not "I can help you...").

### The actual trigger mechanism: progressive disclosure, three levels

This is the load-bearing fact for "model-invoked":

| Level | When loaded | Token cost | Content |
|---|---|---|---|
| 1: Metadata | Always, at startup | ~100 tokens/skill | `name` + `description` only, injected into the system prompt |
| 2: Instructions | When Claude decides the task matches | Skill body, ideally <5k tokens (Claude Code caps at 500 lines) | Full `SKILL.md` markdown body |
| 3: Resources/code | On demand, as referenced | Zero until accessed | Bundled reference files (read) or scripts (executed via bash; only stdout enters context, code never does) |

Claude **only ever sees name+description for every installed skill** until one
matches the current request — literally decides "does this description match
what I'm being asked to do?" — then reads the body itself via a filesystem
`bash: cat SKILL.md`-style call (API Skills use the code-execution container;
Claude Code uses ordinary Read/bash tool calls). Nothing forces the model to
trigger a skill; it's discretionary and description-driven, which is exactly
why the description field is the entire "trigger" surface. Source: Skills
overview §"How Skills work" (2026-07-29 fetch).

### Two different real substrates — don't conflate them

1. **Claude API / Claude Platform on AWS / Microsoft Foundry**: Skills run
   inside the **code execution tool**'s sandboxed container. Requires beta
   header `skills-2025-10-02` (+ `files-api-2025-04-14` if uploading/
   downloading files). No network access, no runtime package installs inside
   that container. Upload custom skills via the `/v1/skills` API; reference by
   `skill_id` in the `container` param.
2. **Claude Code**: Purely filesystem-based, no upload step. Drop a directory
   with `SKILL.md` under `~/.claude/skills/<name>/` (personal) or
   `.claude/skills/<name>/` (project); Claude Code watches the directory and
   picks it up live, no restart needed for edits to existing skills. The
   directory/file name becomes the `/skill-name` command; `description`
   (+ optional `when_to_use`) is what's matched against the user's request for
   auto-invocation. `disable-model-invocation: true` makes it user-only
   (manual `/name` only); `user-invocable: false` makes it model-only (hidden
   from the `/` menu). Source: [code.claude.com/docs/en/skills](https://code.claude.com/docs/en/skills)
   (fetched 2026-07-29; heavily version-annotated down to Claude Code
   v2.1.129–v2.1.218, i.e. actively evolving — treat any specific
   version-gated behavior as needing a recheck against the installed CLI
   version before relying on it).

Both substrates implement the *same* three-level progressive-disclosure idea;
they differ in where the sandbox/filesystem lives.

### Why triggering is inherently probabilistic, not deterministic

Because the "trigger" is the model reading a natural-language description and
judging relevance, there is no code path that mechanically guarantees a skill
fires on a given prompt — the docs' own troubleshooting section confirms this
("Skill not triggering" / "Skill triggers too often" are both listed as
things you tune by rewording the description, not bugs to fix in code). This
matters for the build proposal below: an example that self-tests "did the
model choose to trigger" against the *real* API would be flaky and cost
tokens per run, unlike every other example in this repo.

### Sources
- [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) — platform.claude.com, fetched 2026-07-29
- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) — platform.claude.com, fetched 2026-07-29
- [Extend Claude with skills](https://code.claude.com/docs/en/skills) — code.claude.com, fetched 2026-07-29 (Claude Code-specific: directory locations, frontmatter reference table, invocation control)
- [Equipping agents for the real world with Agent Skills](https://www.anthropic.com/engineering/equipping-agents-for-the-real-world-with-agent-skills) — Anthropic engineering blog, published 2025-10-16 (skills launch), open standard noted 2025-12-18
- [anthropics/skills](https://github.com/anthropics/skills) — official skills repo, includes a `template-skill` with the minimal two-field frontmatter; fetched 2026-07-29, exact current file layout not fully confirmed by the fetch (see Open questions)

None of these sources are stale by the "~1 year" bar; the Claude Code doc in
particular is being actively updated (version-gated notes up to v2.1.218).

## Build proposal

### What, and why this shape

Every existing example in `examples/` (`minimal-agent-loop`,
`typed-tool-registry`, `orchestrator-subagents`) is a hand-written program
against the raw Anthropic **Messages API**, self-tested fully offline with a
scripted fake client (see [[tool-use-loop]] gotcha: "write the loop by hand
only to learn the mechanics"; see also the knowledge-base convention "Testing
agent loops offline: inject a fake client"). A literal Claude API Skills
demo needs the beta code-execution container; a literal Claude Code
`.claude/skills/` demo can only be verified by *actually* running `claude -p`
against the live model and observing whether it happened to trigger — which
is non-deterministic per the "Findings" section above, and would be the first
example in this repo without a deterministic, offline self-test.

So: build the **mechanism**, not the product surface. A small hand-written
harness on the raw Messages API that faithfully reproduces the three
findings above that actually matter (metadata-always/body-on-trigger,
description as the sole discovery signal, a real `SKILL.md` file format) —
in the same spirit as `minimal-agent-loop` teaching the tool-use loop by hand.
**Out of scope:** the beta Skills API/container, real Claude Code discovery
from `~/.claude/skills/`, multi-skill ranking among 10s of skills, resource
files (level 3), script execution.

### Where: `examples/anatomy-of-a-skill/`

```
examples/anatomy-of-a-skill/
├── README.md
├── requirements.txt          # anthropic (only needed for the live path)
├── skills/
│   └── git-commit-helper/
│       └── SKILL.md          # a real, valid SKILL.md — used as fixture + demo
├── agent.py
└── test_agent.py
```

`skills/git-commit-helper/SKILL.md` is a genuine, spec-valid skill (name,
description with a stated trigger, and a short body), directly modeled on the
"Git Commit Helper" example given verbatim in the best-practices doc:

```yaml
---
name: git-commit-helper
description: Generate descriptive commit messages by analyzing git diffs. Use when the user asks for help writing commit messages or reviewing staged changes.
---
```

### Interfaces (layer 3 — for the builder to implement)

```python
@dataclass(frozen=True)
class Skill:
    name: str
    description: str
    body: str

def parse_skill_md(path: str) -> Skill:
    """Parse a SKILL.md file into a Skill.

    Failure modes: missing/malformed YAML frontmatter delimiters, or a
    missing/empty `name` or `description` field, raises ValueError with the
    file path and the missing field named. Never fills in a default.
    """

def build_system_prompt(skills: list[Skill]) -> str:
    """Level-1 disclosure: name + description only, one line per skill.
    Must never contain any skill's `body` text (invariant, asserted in tests).
    """

LOAD_SKILL_TOOL: dict  # Messages API tool schema: load_skill(name: str)

def run_agent(
    client, skills: list[Skill], user_message: str, *, max_turns: int = 5
) -> tuple[str, list[str]]:
    """Run the loop; return (final_text, names_of_skills_loaded).

    `names_of_skills_loaded` records every skill whose body was actually
    injected via a load_skill tool call — the observable proxy for "did the
    trigger fire." Unknown skill name requested by the model -> tool_result
    with is_error content, loop continues (mirrors the unknown-tool handling
    already established in minimal-agent-loop's TOOL_FUNCTIONS.get pattern).
    """
```

`MODEL = "claude-haiku-4-5"` per `knowledge/anthropic-models.md`.

### What "it works" means (acceptance criteria / self-test)

All offline, no network, via a scripted fake client exactly like
`minimal-agent-loop/test_agent.py`:

1. `parse_skill_md` on the bundled `skills/git-commit-helper/SKILL.md`
   returns the correct `name`/`description`/`body`, and `body` does not
   contain the frontmatter delimiters or fields.
2. `parse_skill_md` on a temp file missing `description` raises `ValueError`
   naming the missing field (fail fast at the boundary, no silent default).
3. `build_system_prompt([skill])` contains the skill's `description` text and
   does **not** contain a distinctive substring from its `body` — this is the
   checkable form of "level 1 metadata only, ~100 tokens, no body."
4. Fake client scripted: turn 1 = `tool_use` calling
   `load_skill(name="git-commit-helper")`; turn 2 = `end_turn` text. Assert
   `run_agent` returns `names_of_skills_loaded == ["git-commit-helper"]` and
   that the `tool_result` sent back on turn 2's input equals the skill's
   `body` verbatim — i.e. the full instructions really did enter the
   conversation only after the (simulated) trigger decision.
5. Fake client scripted: single `end_turn` response, no tool call at all.
   Assert `names_of_skills_loaded == []` — the "no context penalty until
   triggered" claim, made checkable.
6. Fake client scripted: `tool_use` calling `load_skill(name="nonexistent")`.
   Assert the loop does not crash, returns an `is_error` tool_result
   containing `"nonexistent"`, and continues to a normal final answer.

`agent.py`'s `main()` follows the existing convention: if
`ANTHROPIC_API_KEY` is unset, print a message and exit 0 (no live call
required to "work"); if set, run one live example prompt that should trigger
the bundled skill and one that shouldn't, printing which skill(s) loaded for
each — an honest, clearly-labeled best-effort demo of real triggering
behavior, not a test.

### README must state explicitly

That this demonstrates the Skills *mechanism* (progressive disclosure +
description-driven, model-decided triggering) hand-built on the raw Messages
API — not the literal Claude API Skills container feature or Claude Code's
filesystem auto-discovery — and link to the two follow-up backlog items
(`shells out to a local script`, `reference files loaded on demand`) as the
natural next increments toward level 3.

## Open questions

- Could not fully confirm the current file layout of the `anthropics/skills`
  GitHub repo's `template-skill` beyond the two-field frontmatter shown by
  the fetch tool's summary — worth a direct look (`gh repo clone
  anthropics/skills` or browsing) before citing its exact folder structure
  as fact.
- Whether Claude Code's live skill-listing character budget
  (`skillListingBudgetFraction`, 1% of context window default) or the
  1,536-char combined `description`+`when_to_use` cap applies to the Claude
  API surface too, or is Claude-Code-specific — the overview doc's 1,024-char
  `description` limit and the best-practices/code.claude.com 1,536-char
  *listing* cap are stated in different docs for different surfaces and I did
  not find a single doc reconciling them.
- No independent (non-Anthropic) source was checked for corroboration since
  Anthropic's own docs are the primary/authoritative source for its own
  product; flagging per instructions rather than treating that as a gap.

## Knowledge base

New note: [[agent-skills]] — captures the SKILL.md frontmatter contract, the
three-level progressive-disclosure mechanism, the API-container vs.
Claude-Code-filesystem substrate distinction, and the "triggering is
inherently non-deterministic — test the mechanism, not the trigger" lesson
for future example design. Linked from `knowledge/INDEX.md` under Skills, and
cross-referenced from [[tool-use-loop]].
