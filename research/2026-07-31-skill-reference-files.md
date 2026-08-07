# Packaging a skill with reference files the model loads on demand

> Shipped as `examples/skill-reference-files-bigquery/` — a separate, later
> cycle (2026-08-06) independently built a second increment on this same
> backlog topic (`research/2026-08-06-skill-reference-files.md`) and also
> claimed `examples/skill-reference-files/`; that one merged first, so this
> one's directory was disambiguated at merge time. Both examples stand:
> this one demonstrates selective on-demand loading via a hand-built
> `read_reference` tool, the other adds a static reference-graph validator.

## Question

What does it actually mean for a Skill to bundle **reference files** (level-3
"resources," as distinct from executable scripts), how does Anthropic say to
organize and author them so Claude loads only what a given task needs, and
what is the smallest runnable, offline-testable increment this repo can ship
to demonstrate that selective-loading mechanism today?

## Backlog note on picking this item

Per BACKLOG.md on `main` at the start of this cycle, the topmost unclaimed
`[ ]` item was "Anatomy of a skill: a minimal model-invoked skill with a clear
trigger" (Skills, item 1). I checked git history before starting and found
that item — plus item 2, "A skill that shells out to a local script" — already
has research (`research/2026-07-29-anatomy-of-a-skill.md`) and a full build
(`examples/skill-script-execution/`, PR #5) sitting on an **unmerged** branch
(`cycle/2026-07-30-skill-script-execution`), not yet reflected in `main`'s
`BACKLOG.md`. That branch's own README explicitly signposts the next
increment: *"The natural next increment... is the read-not-execute sibling:
'Packaging a skill with reference files the model loads on demand.'"*
Redoing items 1–2 today would duplicate in-flight work and produce merge
conflicts once PR #5 lands, so I picked item 3 instead — the topmost item
*not* already covered by pending work — and marked it `[researching]` on
`main`. (Items 1–2 are left as `[ ]`/untouched on `main`; PR #5 will resolve
them on merge.)

## Findings

### Level 3 has two distinct kinds of content — don't conflate them

The Skills overview draws this distinction explicitly in its level-3 table
and prose: **code** (scripts, executed via bash, only stdout ever enters
context — already covered by `examples/skill-script-execution` on the
unmerged branch) vs. **instructions/resources** (additional markdown files,
database schemas, API docs, templates — *read* via bash into context, in
full, when referenced). [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview)
(platform.claude.com, fetched 2026-07-31 — undated but actively maintained,
matches the version fetched 2026-07-29/30 on the prior cycle, no drift
observed).

> "Claude accesses these files only when referenced. The filesystem model
> means each content type has different strengths: instructions for flexible
> guidance, code for reliability, resources for factual lookup."

The mechanics, per the overview's worked example ("Loading a PDF processing
Skill"): once SKILL.md is triggered and read, if its body links to another
file (`[FORMS.md](FORMS.md)`), Claude issues a further `bash: cat FORMS.md`
(or equivalent Read) *only if the current task needs it* — this is a second,
independent judgment call by the model, not an automatic cascade. The doc's
own worked example has Claude read `SKILL.md` but explicitly *not* read
`FORMS.md` because "form filling is not needed" for that particular request.
That's the checkable claim this increment should demonstrate: **selective
loading among several sibling reference files, not just the SKILL.md-body
on/off switch already covered by the (unmerged) anatomy-of-a-skill note.**

### Authoring guidance for reference files (from best-practices, fetched 2026-07-31)

[Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices):

- **Keep references one level deep from SKILL.md.** Every reference file
  should be linked directly from SKILL.md. Chained references
  (`SKILL.md` → `advanced.md` → `details.md`) risk Claude only partially
  reading a nested file — the doc says Claude "might use commands like
  `head -100` to preview content rather than reading entire files" when
  following a reference from a reference, "resulting in incomplete
  information."
- **Table of contents for files over 100 lines**, at the top, so a partial
  read (`head`) still shows Claude the full scope of what's available.
- **Domain-specific organization** — split by topic/domain so an
  irrelevant-to-the-task file is never read: the doc's own worked example is
  `reference/finance.md`, `reference/sales.md`, `reference/product.md`,
  `reference/marketing.md` for a BigQuery skill, with SKILL.md's body
  pointing at each by name and a worked scenario: *"When the user asks about
  revenue, Claude reads SKILL.md, sees the reference to
  `reference/finance.md`, and calls bash to read just that file. The
  sales.md and product.md files remain on the filesystem, consuming zero
  context tokens until needed."* This is the exact selective-loading claim
  to make checkable.
- **Descriptive file names** (`form_validation_rules.md`, not `doc2.md`);
  **forward slashes always**, even conceptually on Windows-hosted content
  (`reference/guide.md`, not `reference\guide.md`) — Unix paths work
  everywhere, backslashes don't.
- **No context penalty for bundled-but-unread content** — "Bundle
  comprehensive resources: Include complete API docs, extensive examples,
  large datasets; no context penalty until accessed." This is the same
  "None until accessed" row already in the level-3 cost table from the prior
  cycle's note, now with the authoring implication spelled out: it's a
  reason to bundle *generously*, not a reason to worry about size.

### What the docs do *not* say: no reference-file sandboxing/traversal guidance

Neither the overview nor the best-practices doc says anything about
constraining *which* files on disk a reference-read may resolve to. The only
security section in either doc (overview, "Security considerations") talks
about auditing skill *sources* for malicious instructions/scripts — nothing
about the read-a-reference-file operation itself potentially resolving
outside the skill's own directory (e.g. a body instruction, or a
model-supplied file name, containing `../../../etc/passwd` or an absolute
path). This is a genuine gap worth flagging, not something I could confirm
from Anthropic's docs either way — for the real Claude Code/API substrates,
this is presumably contained by the sandbox/VM boundary the docs describe
("Skills run in a code execution environment... filesystem access"), but
this repo's example is a **hand-built tool on the raw Messages API**, with no
sandbox, so it is the example code's own responsibility not to let a
`read_reference` tool escape the skill's reference directory. Treating this
as a real failure mode (not just a docs-mirroring exercise) is this cycle's
one added value beyond restating the docs.

### Consistency check against the prior (unmerged) cycle's notes

Cross-checked the frontmatter contract, 3-level table, and script-execution
findings already recorded in `knowledge/agent-skills.md` on branch
`cycle/2026-07-30-skill-script-execution` — no contradictions found against
today's fresh fetch of the same two docs; nothing has changed since
2026-07-29/30. I did not re-derive those facts here; see that note (once
merged) or this cycle's knowledge note below for the reference-file-specific
additions only.

### Sources

- [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) — platform.claude.com, fetched 2026-07-31
- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) — platform.claude.com, fetched 2026-07-31 (progressive-disclosure patterns, one-level-deep rule, TOC guidance, domain-organization worked example, runtime-environment section)
- [anthropics/skills](https://github.com/anthropics/skills) — official repo; confirmed top-level layout (`skills/`, `spec/`, `template/`) via fetch 2026-07-31, but could **not** confirm the actual internal file layout of the production `docx`/`pdf`/`pptx`/`xlsx` skills (GitHub's rendered page didn't expose it to the fetch tool) — flagged in Open questions.
- Secondary/aggregator sources surfaced by search (Nimble, Atlan, aibuilderclub, Medium posts, dated July 2026) corroborate the same "push non-essential content into reference files, load lazily" guidance in their own words but add no primary facts beyond Anthropic's own docs, so not cited individually as sources of fact.

None of the primary sources are stale by the ~1-year bar; both were re-fetched
today and match the prior cycle's fetch two days ago with no drift.

## Build proposal

### Intent

Demonstrate the level-3 **reference-file** mechanism — Claude selectively
reading named markdown files from a skill's `reference/` directory into
context, and *not* reading sibling files it doesn't need — as a small,
hand-written tool on the raw Messages API, in the same style as every
existing example (`minimal-agent-loop`, `typed-tool-registry`,
`orchestrator-subagents`, and the unmerged `skill-script-execution`).

**Out of scope** (explicit, matching the established convention for this
family of examples): SKILL.md frontmatter parsing/the trigger decision itself
(that's the separate, not-yet-built "anatomy of a skill" item); the beta
Claude API Skills/code-execution container; Claude Code's own filesystem
auto-discovery; script execution (already covered); nested/nested-reference
nesting depth beyond one level; nothing that requires a live model call to
pass — the self-test is fully offline.

### Where: `examples/skill-reference-files-bigquery/`

```
examples/skill-reference-files-bigquery/
├── README.md
├── requirements.txt              # anthropic (only needed for the live path)
├── skills/
│   └── bigquery-analysis/
│       ├── SKILL.md               # body links to all 3 reference files, one level deep
│       └── reference/
│           ├── finance.md         # revenue/ARR/billing metrics (with a short TOC)
│           ├── sales.md           # pipeline/opportunities
│           └── product.md         # API usage/feature adoption
├── agent.py
└── test_agent.py
```

`skills/bigquery-analysis/SKILL.md` mirrors the best-practices doc's own
worked example verbatim in spirit (domain-specific organization,
finance/sales/product split), with a real, non-trivial body for each
reference file (a handful of made-up but internally consistent metric
definitions — enough content to make "which file got read" meaningfully
checkable, not placeholder text).

### Interfaces (layer 3 — for the builder to implement)

```python
# Root the tool at the skill's own reference/ directory. A constant, not a
# parameter the model controls — the model only ever supplies a filename.
SKILL_DIR = "skills/bigquery-analysis"
REFERENCE_DIR = os.path.join(SKILL_DIR, "reference")

def read_reference(filename: str) -> str:
    """Read one reference file's full content, if and only if it resolves to
    a real file inside REFERENCE_DIR.

    Failure modes (all returned as a plain "Error: ..." string, never
    raised — same convention as minimal-agent-loop's calculator tool, so a
    misbehaving model gets a usable tool_result instead of a crash):
      - filename resolves (via realpath) outside REFERENCE_DIR, whether by
        `../` traversal or an absolute path -> "Error: ... outside the
        reference directory" (no content from the target path is ever read).
      - resolved path does not exist -> "Error: no such reference file: ..."
      - resolved path is a directory, not a file -> "Error: ... is a
        directory, not a file"
    On success: returns the file's full text content, verbatim.
    """

READ_REFERENCE_TOOL: dict  # Messages API tool schema: read_reference(filename: str)

def run_agent(
    client, user_message: str, *, max_turns: int = 5
) -> tuple[str, list[str]]:
    """Run the manual tool-use loop; return (final_text, filenames_read).

    filenames_read records, in call order, every filename for which
    read_reference() actually returned file content (i.e. not an error) —
    the observable proxy for "which reference files actually entered
    context," and the thing the self-test asserts selectivity on.
    """
```

`MODEL = "claude-haiku-4-5"` per `knowledge/anthropic-models.md`. Same manual
loop shape as `examples/minimal-agent-loop/agent.py` (`TOOL_FUNCTIONS` dict,
`tool_use_id` echoing, `max_turns` cap, unknown-tool-name handled as an
`Error:` tool_result rather than a crash).

### What "it works" means (acceptance criteria / self-test, offline, no network)

1. `read_reference("finance.md")` returns exactly the on-disk content of
   `skills/bigquery-analysis/reference/finance.md`.
2. **Path-traversal is rejected, not silently sandboxed-in**:
   `read_reference("../SKILL.md")` and `read_reference("../../../../etc/passwd")`
   and an absolute path (e.g. `/etc/hosts`) all return an `"Error: ..."`
   string, and — checked directly, not just by the error string — the return
   value never contains real content from those out-of-bounds paths (assert
   e.g. `"root:"` never appears for the `/etc/passwd` case, and the returned
   string does not equal the real `SKILL.md` content for the `../SKILL.md`
   case).
3. `read_reference("marketing.md")` (a plausible-sounding but nonexistent
   file, matching the "sibling domain that doesn't exist for this skill"
   case) returns an `"Error: ..."` string naming the file, not a crash.
4. `read_reference("reference")` (a directory, not a file) returns an
   `"Error: ..."` string, not a traceback and not silently-empty content.
5. **Selective-loading transcript test** (scripted fake client, exactly the
   `tool-use-loop` "inject a fake client" pattern): turn 1 = `tool_use`
   calling `read_reference(filename="finance.md")`; turn 2 = `end_turn` text.
   Assert `run_agent()` returns `filenames_read == ["finance.md"]`, **and**
   assert that no message sent to the fake client at any point contains a
   distinctive substring from `sales.md` or `product.md`'s content — the
   checkable form of "the sales.md and product.md files remain on the
   filesystem, consuming zero context tokens until needed."
6. **Multi-read test**: scripted fake client requests `finance.md` then
   `sales.md` across two turns before ending. Assert
   `filenames_read == ["finance.md", "sales.md"]` in call order, and that
   `product.md`'s content still never appears anywhere in the transcript.
7. No tool call at all (single `end_turn` response) -> `filenames_read == []`
   — the "no context penalty until accessed" claim at the top of the loop,
   not just deep inside one tool call.

`agent.py`'s `main()` follows the established convention: if
`ANTHROPIC_API_KEY` is unset, print a one-line note and exit 0 (self-test
covers correctness, no live call required); if set, run one live prompt that
should trigger exactly one reference read (e.g. "what's our Q3 revenue
policy?" -> expect only `finance.md`) and print which file(s) were read —
labeled honestly as a best-effort demo, not a test, per the same
non-determinism caveat already established for real model decisions.

### README must state

That this demonstrates level-3 **reference-file** selective loading
specifically — the read-a-file-into-context counterpart to the (unmerged)
`skill-script-execution` example's execute-a-script-for-stdout-only pattern —
and link both the "anatomy of a skill" and "skill script execution" notes as
siblings, plus explicitly call out the path-traversal guard as something
*this example's own code* is responsible for, not something Anthropic's
documented Skills mechanism itself promises for a hand-built (non-sandboxed)
tool.

## Open questions

- Could not confirm the actual internal file layout of Anthropic's production
  `docx`/`pdf`/`pptx`/`xlsx` skills in the `anthropics/skills` GitHub repo
  (marked "source-available, not open source" by the fetch) — would need a
  direct `git clone`/browse to see whether they use `reference/` files in
  practice and how deep, rather than relying on the best-practices doc's
  worked (possibly illustrative-only) BigQuery example.
- Whether real Claude Code / Claude API Skills sandboxes actually *do*
  constrain a reference-file read to the skill's own directory server-side
  (making my proposed path-traversal guard redundant-but-still-correct-to-
  demonstrate in a non-sandboxed hand-built example), or whether an
  untrusted Skill's SKILL.md body could genuinely direct a real read outside
  its own directory in those substrates — not stated either way in the two
  primary docs I read; the overview's "Security considerations" section
  talks only about auditing skill sources, not this specific boundary.

## Knowledge base

New note (main has no Skills notes yet): [[agent-skills]] — the SKILL.md
frontmatter contract, the three-level progressive-disclosure mechanism
(metadata/instructions/resources), the reference-file authoring patterns
(one-level-deep, TOC-for-long-files, domain-specific organization,
no-context-penalty-until-accessed), and the path-traversal gotcha this
cycle's build proposal turns into a concrete acceptance criterion. Linked
from `knowledge/INDEX.md` under Skills and cross-referenced from
[[tool-use-loop]]. (Note: a similarly-named note already exists, more fully
developed, on the not-yet-merged `cycle/2026-07-30-skill-script-execution`
branch — the two will need reconciling by the maintainer when that PR
merges; this note is written to stand on its own against `main`'s current
state in the meantime.)
