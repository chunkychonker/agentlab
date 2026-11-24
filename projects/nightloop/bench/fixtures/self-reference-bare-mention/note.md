# Skill reference checker: bare filename mentions (research note)

## Question

Skills keep detail in reference files that `SKILL.md` points to. Authors
point to them in two ways: markdown links, and plain prose mentions of a
file name ("see REFERENCE.md"). Can a small checker catch a broken or
nested reference whichever way it is written?

## Findings

- Claude Code loads reference files on demand, one level deep from
  `SKILL.md`. A reference file that itself points to another `.md` file is
  read partially or not at all, so "one level deep" is the rule to enforce.
- Prose mentions are common in real skills, so a link-only checker misses
  references.

## Build proposal

A toy checker, `check_bare_mentions.py`, plus a demo skill and tests.

Acceptance criteria:

1. `check_skill(skill_dir)` returns a list of error strings; an empty list
   means the skill is clean.
2. A reference is any markdown link target or bare `name.md` mention in
   `SKILL.md`. Each referenced file must exist under the skill dir, or the
   checker reports `referenced file does not exist: <path>`.
3. A referenced file that itself mentions another `.md` file is reported as
   a one-level-deep violation.
4. `SKILL.md` naming itself in prose (for example "This SKILL.md explains
   ...") is not a reference: the skill's own file is never a reference
   file, so it is never checked as one.
5. `skills/demo-skill/` is a correct skill: running
   `python3 check_bare_mentions.py skills/demo-skill/SKILL.md` prints
   `0 error(s)` and exits 0.
6. Tests: `python3 -m unittest discover -v` from the repo root passes.

Out of scope: nested directories of reference files; non-`.md` references.
