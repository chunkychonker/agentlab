# Shipping a cycle's work safely (nightloop m5)

Durable points from the 2026-11-15 research ([[cycle-orchestration-gates]], [[pipeline-claim-lifecycle]]).

- Push only the branch you made, by explicit refspec. Never push `main`, so a failed cycle's commits cannot ride along.
- Move local `main` back to `origin/main` with `git branch -f main origin/main` only *after* the commits are safe on a pushed branch, and only while `main` is not checked out. It discards nothing, unlike `reset --hard`.
- `git pull --ff-only` does not fail when local `main` is merely ahead, so a recovery that only checks `git status --porcelain` lets committed work leak into the next cycle. Count `origin/main..main` too.
- `gh pr create` prompts unless `--title`, `--body-file`, `--base` and `--head` are all given. Its success output is the PR URL ([manual](https://cli.github.com/manual/gh_pr_create)). The manual does not say what happens if the branch already has a PR, so list first and reuse (retry-safe).
- `gh pr view --json mergeable` is a real field, but the manual does not list its values. The `MERGEABLE`/`CONFLICTING`/`UNKNOWN` set is relied on by `run.sh` only; fail closed on anything else and poll while `UNKNOWN`.
- Put the review record in the body at creation and read it back for the hidden marker. Scan it for credentials first, and truncate only the review section.
