---
name: commit-message
description: Drafts a Conventional-Commits-style commit message from the currently staged git diff. Use when the user asks for a commit message, asks what to commit, or asks you to describe staged changes.
---

## Staged changes

Short status:

!`git status --short`

Full staged diff:

!`git diff --cached`

## Instructions

1. If both blocks above are empty, say plainly that nothing is staged — do
   not invent a message.
2. Otherwise, read the diff and status and draft one commit message:
   - A header in `type(scope): summary` form. `type` is one of `feat`, `fix`,
     `refactor`, `docs`, `test`, `chore`, `build`, `ci`. `scope` is the
     directory or module most of the diff touches (omit if it spans many).
     `summary` is imperative mood, lowercase, no trailing period, under ~72
     chars.
   - An optional body (blank line, then a few bullet points) only if the
     diff has more than one logically distinct change worth calling out.
3. Do not describe files that only changed mode/whitespace unless that is
   the only change.
4. Present the message in a fenced code block so it can be copied straight
   into `git commit -m`.
