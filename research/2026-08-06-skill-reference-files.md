# Packaging a skill with reference files the model loads on demand

## Question

What is the documented mechanism and set of authoring rules for a Claude Code
skill's Level-3 **reference files** (`.md` files bundled alongside `SKILL.md`,
distinct from the executable scripts already covered by the 2026-08-06
`skill-script-execution` research cycle), and what's the smallest same-day
increment that demonstrates the pattern with a genuinely offline-testable
validator?

## Findings

Primary sources, fetched today (2026-08-06):

- [Skill authoring best practices](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/best-practices) — "Progressive disclosure patterns," "Avoid deeply nested references," "Structure longer reference files with table of contents," and "Runtime environment" sections.
- [Agent Skills overview](https://platform.claude.com/docs/en/agents-and-tools/agent-skills/overview) — "How Skills work" / Level 3 table, "The Skills architecture."
- [anthropics/skills](https://github.com/anthropics/skills) (public repo, tree fetched via GitHub API today) — real, currently-shipped skills that bundle reference files: `skills/pdf/{forms.md,reference.md}`, `skills/mcp-builder/reference/*.md`, `skills/skill-creator/references/schemas.md`, `skills/internal-comms/examples/*.md`. `pdf/SKILL.md`, `pdf/forms.md`, and `pdf/reference.md` fetched directly (raw.githubusercontent.com) to check real usage against the documented guidance.

No source found older than a few months; the docs' own frontmatter version gates (cited in the two prior skill research notes) confirm this is still an actively-iterated surface — re-check before relying on anything not reconfirmed here.

### The mechanism (confirms and extends [[agent-skills]])

Reference files are Level 3 of progressive disclosure: zero token cost until
read. The overview doc, verbatim: "Claude accesses these files only when
referenced... If those instructions reference other files (such as FORMS.md
or a database schema), Claude reads those files too using additional bash
commands." Mechanically this is `bash: cat <skill-dir>/REFERENCE.md`, exactly
like the `SKILL.md` read itself — same primitive, later trigger. No context
penalty for files that are never read; a skill "can include dozens of
reference files" and only the ones a given task needs get loaded (Level-3
table, overview doc).

### Two documented organization patterns (best-practices doc, "Progressive disclosure patterns")

1. **Pattern 1 — high-level guide with references**: SKILL.md stays a short
   overview and links out to `FORMS.md`, `REFERENCE.md`, `EXAMPLES.md` at the
   top level of the skill directory. This is exactly what the real `pdf`
   skill does (see below).
2. **Pattern 2 — domain-specific organization**: reference files live under a
   `reference/` subdirectory, one file per domain, so a task about one domain
   never pulls in the others' tokens. Worked example in the docs:
   `bigquery-skill/reference/{finance,sales,product,marketing}.md`. This is
   what the real `mcp-builder` skill does (`reference/{evaluation,
   mcp_best_practices,node_mcp_server,python_mcp_server}.md`).

### Two hard, checkable rules from the best-practices doc

- **"Avoid deeply nested references" / keep references one level deep**: all
  reference files should be linked directly from `SKILL.md`. If `SKILL.md` →
  `advanced.md` → `details.md`, "Claude might use commands like `head -100` to
  preview content rather than reading entire files, resulting in incomplete
  information" — so a two-hop chain is a real correctness risk (partial
  reads), not just a style nit. I checked this empirically against
  `mcp-builder`'s four `reference/*.md` files (grepped each for markdown
  links): none contain links to other local `.md` files — real-world
  confirmation of the one-level-deep pattern actually being followed.
- **Table of contents for files >100 lines**: "For reference files longer
  than 100 lines, include a table of contents at the top... This ensures
  Claude can see the full scope of available information even when previewing
  with partial reads." **This rule is not universally followed even by
  Anthropic's own shipped skills**: `skills/pdf/reference.md` is 611 lines and
  opens straight into `# PDF Processing Advanced Reference` / an intro
  paragraph / `## pypdfium2 Library` — no `## Contents` heading anywhere near
  the top (checked directly, first 30 lines). Treat this rule as a
  best-practice **warning**, not an error a validator should hard-fail on —
  the docs' own example doesn't pass it.

### A gap between the docs' illustrative syntax and real shipped skills

The best-practices doc's worked examples always use markdown link syntax —
`See [FORMS.md](FORMS.md) for complete guide`. The real, currently-shipped
`skills/pdf/SKILL.md` does **not**: it says "For advanced features... see
REFERENCE.md" and "read FORMS.md and follow its instructions" — bare filename
mentions, no `[...]()` markdown link at all. Both work identically at
runtime because the actual mechanism is Claude reading a filename via bash,
not a renderer following a hyperlink — but it means an automated checker that
only looks for markdown-link syntax would miss a real, working reference and
falsely report it as unused/dead content. Anthropic's own `mcp-builder` skill,
by contrast, does use real markdown links (`[📋 View Best Practices](./reference/mcp_best_practices.md)`)
throughout. **Both forms are real, current usage** — a validator has to check
for both a markdown link `[text](path)` and a bare mention of an existing
bundled filename as a token in the body text.

### Runtime/architecture notes reused from [[agent-skills]], restated for this note's scope

- File paths: forward slashes only, even conceptually on Windows (Unix paths
  work everywhere; backslashes break on Unix) — "Avoid Windows-style paths"
  anti-pattern, explicit rule.
- Naming: "Name files descriptively... not `doc2.md`" — organize by
  domain/feature (`reference/finance.md`), not generic numbering.
- Claude Code's runtime for scripts has full local network access (per the
  overview's "Runtime environment constraints" table); not directly relevant
  to reference files (pure markdown, never executed) but worth restating so a
  reader of this note doesn't conflate the two Level-3 content types — see
  [[agent-skills]] for the script/`allowed-tools` half already covered.

### What's not independently verifiable offline

Whether Claude, in a live session, actually reads only the one relevant
reference file and skips the others (the overview's "zero token cost until
accessed" claim) is a live, model-dependent behavior — same class of
non-offline-testable claim as skill triggering itself, already flagged in
[[agent-skills]]. What **is** deterministically checkable offline is whether a
skill's reference-file *structure* conforms to the documented rules above
(broken links, nesting depth, TOC presence, path style, naming) — that's the
target of today's build.

## Build proposal

### Intent

Ship one real, installable Claude Code skill,
`looking-up-http-status-codes`, that demonstrates Level-3 reference files
correctly — a short `SKILL.md` (Pattern 1) linking out to a `reference/`
directory (Pattern 2's naming, since this repo's HTTP-status content splits
naturally by response class) — plus an offline structural validator,
`check_references.py`, that checks any skill directory's reference-file
graph against the documented, checkable rules above. Out of scope: proving
Claude actually reads files on demand and skips unreferenced ones live (not
offline-testable, same caveat class as triggering — documented as a manual
verification step in the README, per the convention already established in
`skill-anatomy` and `skill-script-execution`); anything about bundled
*scripts* (already covered by `skill-script-execution`); and any change to
either existing skill example.

### Behavioral spec

**`examples/skill-reference-files/skills/looking-up-http-status-codes/`**

- `SKILL.md`: `name: looking-up-http-status-codes` (gerund form, conforms to
  the `name` rules), `description` third-person, states what ("Looks up the
  meaning, cause, and correct client/server handling of an HTTP status code")
  and when ("Use when the user asks what a status code means, why a request
  returned a given code, or which code to return for a given situation").
  Body under ~40 lines: a quick-reference table of the most common codes
  inline, then three links out — one per response-class file — following
  Pattern 1. At least one link uses real markdown-link syntax
  (`[reference/client-errors.md](reference/client-errors.md)`) and the body
  additionally mentions a filename bare (no brackets) at least once, so the
  fixture itself exercises both real-world reference styles found in
  Anthropic's own `pdf` and `mcp-builder` skills.
- `reference/success-and-redirection.md` (2xx/3xx): short, under 100 lines,
  no TOC required.
- `reference/client-errors.md` (4xx): long enough to exceed 100 lines with
  real content (each common 4xx code gets a short cause + fix), **with** a
  `## Contents` heading in the first 15 lines listing the codes covered —
  demonstrates the TOC rule being followed correctly.
- `reference/server-errors.md` (5xx): short, under 100 lines, no TOC
  required.
- No reference file links to another reference file (one-level-deep, verified
  by the validator against the fixture itself as part of its own passing
  self-test).

**`examples/skill-reference-files/check_references.py`** — pure offline
static analysis, no network, no `anthropic` import, no API key:

- Input: a path to a skill directory (must contain `SKILL.md`).
- Extracts local references from `SKILL.md`'s body: markdown links
  `[text](path)` whose `path` doesn't start with `http://`/`https://`, **and**
  bare mentions of any filename that exists somewhere under the skill
  directory (case-sensitive substring/token match) — covering both real-world
  styles found above. Documented as a heuristic with a stated limitation (a
  filename mentioned in prose unrelated to referencing it is a false
  positive) in the module docstring, not silently assumed reliable.
- Checks, each producing `Finding`s:
  - **Broken link** (error): a markdown-link path that doesn't resolve to a
    real file under the skill directory.
  - **Path escapes skill directory** (error): a link path that resolves
    (via `..`) outside the skill directory root — reported, never opened/read
    by the checker (a real boundary the checker itself must not cross).
  - **Nested reference** (error): a file referenced from `SKILL.md` that
    itself contains a local reference (markdown link or bare filename
    mention) to another `.md` file under the skill directory.
  - **Missing table of contents** (warning): a referenced file exceeding 100
    lines with no line matching `^#+\s*(table of )?contents` (case
    insensitive) in its first 15 lines.
  - **Windows-style path** (error): any markdown-link path containing a
    backslash.
  - **Generic filename** (warning): any bundled file whose name matches
    `doc\d+\.md`, `file\d+\.md`, or is exactly `notes.md`/`misc.md`.
- Exit code 0 iff zero `error`-level findings.
- Failure modes stated in the module docstring: missing `SKILL.md` at the
  given path → `FileNotFoundError` propagates unchanged (boundary failure,
  never swallowed); a link that resolves outside the skill directory is
  reported as a finding and never followed (the checker must not read
  arbitrary filesystem paths just because a skill's markdown claims one).

**Acceptance criteria ("it works"):**

1. `python3 check_references.py skills/looking-up-http-status-codes` exits 0
   with zero `error`-level findings against the shipped fixture skill (a
   `missing-toc` or `generic-filename` warning is not expected either, since
   the fixture is designed to satisfy every rule).
2. `python3 test_check_references.py` (stdlib `unittest`, offline, no key, no
   network — same convention as the two prior skill examples) passes,
   constructing fixtures with `tempfile` and asserting the *specific* finding
   kind appears, not just "some finding":
   - a well-formed reference skill (valid links, ≤100-line files, no TOC
     needed) → zero findings
   - `SKILL.md` linking to a file that doesn't exist → `broken-link` error
   - a reference file that itself links to a second local `.md` file →
     `nested-reference` error
   - a >100-line referenced file with no `Contents` heading → `missing-toc`
     warning
   - a >100-line referenced file *with* a `## Contents` heading in the first
     15 lines → no `missing-toc` finding for that file
   - a markdown-link path containing a backslash → `windows-path` error
   - a bundled file named `doc2.md` → `generic-filename` warning
   - a markdown-link path of `../../etc/passwd` → `path-escapes-skill-dir`
     error, and the test asserts the checker never attempts to open that path
     (e.g. by pointing it at a real file outside a tempdir sandbox and
     asserting no read occurred / no unrelated exception)
3. `examples/skill-reference-files/README.md` documents: the two
   organization patterns and which one the fixture uses; the one-level-deep
   and TOC-for->100-lines rules with citations; the honest caveat that even
   Anthropic's own shipped `pdf/reference.md` doesn't carry a TOC (so the
   check is a warning, not an error); how to install the skill; and the
   manual/live verification step (open `claude` in any directory, ask "what
   does a 429 status code mean?", confirm the skill triggers and Claude reads
   `reference/client-errors.md` specifically rather than the other two files)
   — explicitly labeled manual since it is not offline-testable, per the
   convention in the two prior skill research notes.

### Interfaces (stubs only — builder implements bodies)

```python
# examples/skill-reference-files/check_references.py
"""Validate a skill's Level-3 reference-file structure against documented
Agent Skills authoring rules (broken links, nesting depth, TOC presence,
path style, naming).

Reference detection is a heuristic: a local file is considered "referenced"
by a markdown body if it appears as a markdown link [text](path), or if its
filename appears as a bare token in the body text. The latter can false-
positive on an unrelated prose mention of a filename; this is a stated,
accepted limitation, not a silent assumption.

Failure modes:
- Missing SKILL.md at the given skill directory: FileNotFoundError
  propagates unchanged (boundary failure).
- A reference path that resolves outside the skill directory: reported as a
  path-escapes-skill-dir Finding and never opened/read.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

@dataclass(frozen=True)
class Finding:
    level: str   # "error" | "warning"
    kind: str    # "broken-link" | "nested-reference" | "missing-toc"
                 # | "windows-path" | "generic-filename"
                 # | "path-escapes-skill-dir"
    file: str    # path relative to the skill directory
    message: str

def extract_local_references(markdown_text: str, skill_dir: Path) -> list[Path]:
    """Local files referenced by markdown_text: markdown links (excluding
    http(s) targets) plus bare filename mentions matching a real file under
    skill_dir. Returns resolved, skill_dir-relative Paths; never returns a
    path that has been read from outside skill_dir.
    """

def check_broken_links(skill_dir: Path, skill_md_text: str) -> list[Finding]: ...
def check_path_escapes(skill_dir: Path, skill_md_text: str) -> list[Finding]: ...
def check_one_level_deep(skill_dir: Path, skill_md_text: str) -> list[Finding]: ...
def check_table_of_contents(referenced_file: Path) -> list[Finding]: ...
def check_windows_paths(skill_md_text: str) -> list[Finding]: ...
def check_generic_filenames(skill_dir: Path) -> list[Finding]: ...

def check_skill(skill_dir: Path) -> list[Finding]:
    """Run all checks against skill_dir (must contain SKILL.md).

    Failure modes: FileNotFoundError if skill_dir/SKILL.md is missing.
    """

def main(argv: list[str]) -> int:
    """CLI: check_references.py <skill_dir>. Returns 0 iff no error findings."""
```

### Open questions

- Whether Claude Code genuinely reads only the referenced file(s) a task
  needs and never the others (the "zero token cost until accessed" claim) is
  not verifiable in this offline research environment — it requires a live
  session with a way to observe which `bash: cat` calls actually happened.
  The README's manual verification step is exactly this check.
- The bare-filename-mention heuristic for reference detection is a genuine
  approximation, not a parser for "intent to reference" — the module
  docstring states this, and the builder should keep the fixture's prose
  unambiguous (only mention a bundled filename when actually pointing at it)
  so the shipped example doesn't trip its own false-positive risk.
- Whether Claude Code enforces "one level deep" or the TOC convention itself
  at runtime (vs. these being pure authoring guidance with no technical
  enforcement) is not stated in any primary source found — treat both as
  best-practice guidance a linter can check, not a hard platform constraint.
- `THIRD_PARTY_NOTICES.md`/`LICENSE.txt` in the `anthropics/skills` repo
  weren't reviewed — the document skills (`pdf`, `docx`, `pptx`, `xlsx`) are
  explicitly "source-available, not open source" per the overview doc; this
  research note quotes short excerpts of `pdf/SKILL.md`/`reference.md` for
  factual/critical commentary on documented authoring guidance, not as a
  basis for copying their content into the build — the fixture skill built
  today (`looking-up-http-status-codes`) is original content on an unrelated
  topic (HTTP status codes, not PDF processing).
