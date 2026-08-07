# Reference files a skill loads on demand (`looking-up-http-status-codes`)

A Claude Code **Agent Skill** that demonstrates Level-3 reference files —
`.md` files bundled alongside `SKILL.md` that cost zero tokens until Claude
actually reads them — plus an offline structural validator,
`check_references.py`, that checks any skill directory's reference-file
graph against the documented, checkable authoring rules.

From the research note:
[`research/2026-08-06-skill-reference-files.md`](../../research/2026-08-06-skill-reference-files.md).
Builds on
[`examples/skill-anatomy`](../skill-anatomy/) and
[`examples/skill-script-execution`](../skill-script-execution/); background
in [`knowledge/agent-skills.md`](../../knowledge/agent-skills.md).

## What's here

| File | What it is |
|------|-----------|
| `skills/looking-up-http-status-codes/SKILL.md` | The skill: a short overview with an inline quick-reference table, linking out to three reference files. |
| `skills/looking-up-http-status-codes/reference/success-and-redirection.md` | 2xx/3xx codes. Under 100 lines — no TOC required. |
| `skills/looking-up-http-status-codes/reference/client-errors.md` | 4xx codes, per-code cause + fix. Deliberately over 100 lines, with a `## Contents` heading in the first 15 lines. |
| `skills/looking-up-http-status-codes/reference/server-errors.md` | 5xx codes. Under 100 lines — no TOC required. |
| `check_references.py` | Pure offline static analyzer — no network, no `anthropic` import, no API key. Checks broken links, nesting depth, TOC presence, path style, and generic filenames. |
| `test_check_references.py` | Offline self-test (stdlib `unittest`) — asserts the specific finding kind for each documented rule. |

## Two organization patterns, and which one this fixture uses

The best-practices doc documents two patterns for progressive disclosure
(research note, "Two documented organization patterns"):

1. **Pattern 1 — high-level guide with references**: `SKILL.md` stays a
   short overview and links out to top-level files. This is what the real,
   shipped `pdf` skill does.
2. **Pattern 2 — domain-specific organization**: reference files live under
   a `reference/` subdirectory, one file per domain.

This fixture uses **both, combined**: `SKILL.md` itself is Pattern 1 (short
overview + links out, nothing more), but the files it links to live in a
`reference/` subdirectory split by domain (response class) — Pattern 2's
naming convention, since HTTP status codes split naturally into three
non-overlapping classes (2xx/3xx, 4xx, 5xx) the way the docs' own worked
example splits BigQuery reference material by business domain
(`reference/{finance,sales,product,marketing}.md`).

`SKILL.md`'s body also exercises **both real-world reference styles** the
research note found in Anthropic's own shipped skills: a real markdown link
(`[reference/client-errors.md](reference/client-errors.md)`, matching
`mcp-builder`'s style) and two bare filename mentions with no brackets
(`reference/success-and-redirection.md`, `reference/server-errors.md`,
matching the real, shipped `pdf/SKILL.md`'s style). `check_references.py`
detects both.

## The two hard rules this validator checks

- **Keep references one level deep** ("Avoid deeply nested references",
  best-practices doc, cited in the research note): every reference file
  `SKILL.md` links to must not itself link to another local `.md` file. A
  two-hop chain risks Claude previewing with `head -100` instead of reading
  the full file, per the docs' own stated reasoning. `check_one_level_deep`
  enforces this as an **error** (`nested-reference`), and the shipped
  fixture is checked against its own rule as part of its clean-pass
  acceptance criterion — none of the three `reference/*.md` files link to
  each other.
- **Table of contents for files over 100 lines** ("Structure longer
  reference files with table of contents", same doc): a referenced file
  over 100 lines should open with a `## Contents` (or `# Table of
  Contents`) heading in its first 15 lines. `check_table_of_contents`
  enforces this as a **warning** (`missing-toc`), not an error — **the
  research note found Anthropic's own shipped `pdf/reference.md` (611
  lines) does not carry this heading either**, so this is honest
  best-practice guidance the docs' own example doesn't follow, not a hard
  platform constraint a linter should hard-fail on. `client-errors.md` in
  this fixture is 198 lines and *does* carry `## Contents` at line 8,
  demonstrating the rule followed correctly; `check_references.py` verifies
  it produces zero `missing-toc` findings.

## What `check_references.py` checks

| Finding kind | Level | Trigger |
|---|---|---|
| `broken-link` | error | A markdown-link path that doesn't resolve to a real file under the skill directory. |
| `path-escapes-skill-dir` | error | A link path that resolves outside the skill directory via `..`. Reported, **never opened or read** — see below. |
| `nested-reference` | error | A file referenced from `SKILL.md` that itself links to a different local `.md` file. |
| `missing-toc` | warning | A referenced file over 100 lines with no TOC heading in its first 15 lines. |
| `windows-path` | error | A markdown-link path containing a backslash. |
| `generic-filename` | warning | A bundled file named `doc<N>.md`, `file<N>.md`, `notes.md`, or `misc.md`. |

Exit code is `0` iff there are zero error-level findings.

**The path-escape boundary is real, not just reported.** `check_path_escapes`
and `check_broken_links` both normalize a link's target path with pure
string manipulation (`os.path.normpath`/`os.path.join`) *before* deciding
whether it falls inside or outside the skill directory — no filesystem call
happens on a path until it's already known to be inside the sandbox. A path
that escapes is reported as a `path-escapes-skill-dir` finding and is never
passed to `Path.is_file()`, `.read_text()`, or anything else that touches
disk. `test_check_references.py` proves this directly: it points a link at
`../../etc/passwd`-style paths at a **real file** it wrote outside the
tempdir sandbox and asserts the checker reports the escape without ever
reading that file's content (and without raising an unrelated exception).

**Reference detection is a documented heuristic, not a parser.** A local
file counts as "referenced" if it appears in a markdown link, or if its
filename appears as a bare token in the body text — this is stated plainly
in `check_references.py`'s module docstring, including the known
false-positive risk (an unrelated prose mention of a filename would count
as a reference). The fixture's own prose was written to avoid tripping this
false-positive risk.

## Installing the skill

Project-local (committed, shared with the team):

```bash
mkdir -p .claude/skills
cp -r examples/skill-reference-files/skills/looking-up-http-status-codes .claude/skills/
```

Personal (available in every project):

```bash
cp -r examples/skill-reference-files/skills/looking-up-http-status-codes ~/.claude/skills/
```

## Run the self-test (no API key, no network)

```bash
cd examples/skill-reference-files
python3 test_check_references.py
```

Expected: `Ran 13 tests in 0.0Xs` / `OK`, covering every acceptance-criteria
case from the research note — a well-formed skill and the shipped fixture
both produce zero findings; a broken link, a nested reference, a backslash
path, and an escaping `../` path each produce exactly the matching error
finding; a `doc2.md` bundled file and a >100-line file with no TOC each
produce exactly the matching warning finding; a >100-line file *with* a
`## Contents` heading produces no `missing-toc` finding; a SKILL.md that
mentions its own filename and a skill directory passed as `.` each produce
zero findings (regression tests for the two false positives caught in
review).

## Run the validator directly

```bash
python3 check_references.py skills/looking-up-http-status-codes
```

```
ok  no findings

0 error(s), 0 warning(s)
```

Exit code `0`. Point it at any other skill directory (containing a
`SKILL.md`) to check it the same way; a missing `SKILL.md` propagates
`FileNotFoundError` unchanged rather than returning an empty result.

## Verifying on-demand loading (manual, live — NOT offline-testable)

Whether Claude, in a live session, actually reads only the one reference
file a task needs and never the other two (the docs' "zero token cost until
accessed" claim) is a live, model-dependent behavior — the same class of
claim as skill *triggering* itself, already flagged as non-offline-testable
in the `skill-anatomy` and `skill-script-execution` READMEs. This is **not**
part of the automated self-test above. To confirm it manually:

```bash
mkdir -p /tmp/status-code-demo/.claude/skills && cd /tmp/status-code-demo
cp -r /path/to/agentlab/examples/skill-reference-files/skills/looking-up-http-status-codes .claude/skills/
claude
```

Then, in the session, ask: *"what does a 429 status code mean?"* Confirm:

1. The skill triggers automatically from its `description`.
2. Claude's answer matches `reference/client-errors.md`'s 429 entry
   specifically (cause: rate limiting; fix: back off per `Retry-After`) —
   not a generic/from-training-data answer.
3. If your Claude Code build surfaces which files were read (e.g. via
   `--verbose` or a transcript), confirm only `reference/client-errors.md`
   was read, not `success-and-redirection.md` or `server-errors.md` — this
   is the specific "zero token cost until accessed" claim this build cannot
   verify offline.

## Explicitly out of scope

- Proving Claude reads only the relevant reference file live (see above —
  not deterministically testable offline).
- Bundled *scripts* (already covered by `examples/skill-script-execution`).
- Any change to `examples/skill-anatomy` or `examples/skill-script-execution`
  — separate, standalone examples.
- Whether Claude Code enforces the one-level-deep or TOC conventions itself
  at runtime, vs. these being pure authoring guidance with no technical
  enforcement — not stated in any primary source found by the research
  note; treated here as best-practice guidance a linter can check, not a
  hard platform constraint.
