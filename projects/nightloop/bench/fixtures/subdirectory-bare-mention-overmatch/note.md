# Skill reference checker: subdirectory layouts (research note)

## Question

Skills keep detail in reference files that `SKILL.md` points to, often in
a subdirectory (`reference/finance.md`). Can a small checker validate those
references, whether written as markdown links or as bare file names?

## Findings

- Claude Code loads reference files on demand, one level deep from
  `SKILL.md`. A reference file that itself points to another `.md` file is
  read partially or not at all, so "one level deep" is the rule to enforce.
- The subdirectory layout (`reference/<domain>.md`) is the documented way
  to organise many reference files.

## Build proposal

A toy checker, `check_bare_mentions.py`, plus a demo skill in the
subdirectory layout, and tests.

Acceptance criteria:

1. `check_skill(skill_dir)` returns a list of error strings; an empty list
   means the skill is clean.
2. A reference is any markdown link target or bare `name.md` mention in
   `SKILL.md`, resolved relative to the skill dir **with its directory
   part kept**: `[x](reference/finance.md)` refers to
   `<skill>/reference/finance.md`. Each referenced file must exist, or the
   checker reports `referenced file does not exist: <path>`.
3. A referenced file that itself mentions another `.md` file is reported as
   a one-level-deep violation.
4. `skills/demo-skill/` (a `SKILL.md` that links `reference/finance.md`, a
   leaf file) is a correct skill: running
   `python3 check_bare_mentions.py skills/demo-skill/SKILL.md` prints
   `0 error(s)` and exits 0.
5. Tests: `python3 -m unittest discover -v` from the repo root passes.

Out of scope: references outside the skill dir; non-`.md` references.
