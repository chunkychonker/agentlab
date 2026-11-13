# gh PR shipping contract

What a program that opens and checks PRs through `gh` can rely on (read 2026-11-13, nightloop m5, `research/2026-11-13-nightloop-real-shipper.md`).

- `gh pr create` prints only the PR URL on success and has no `--json`. Parse the number from the trailing `/pull/<N>`. `--head` skips any forking or pushing, so push yourself. The manual documents neither exit codes nor the already-exists case: treat any non-zero exit as a refusal. ([manual](https://cli.github.com/manual/gh_pr_create))
- `gh pr view [<number>|<url>|<branch>] --json body,mergeable,number,url` works. Parse the JSON object in your own code rather than leaning on `-q` and jq. ([manual](https://cli.github.com/manual/gh_pr_view))
- `mergeable` is `MERGEABLE`, `CONFLICTING` or `UNKNOWN` according to community and GraphQL-schema sources only, not the `gh` manual. `UNKNOWN` means GitHub is still computing: retry a bounded number of times, and treat everything but `MERGEABLE` as "leave it open".
- `git pull --ff-only` on a local `main` that is **ahead** of origin succeeds as a no-op, so it does not catch stray local commits. Fetch, then count `origin/main..main`.
- Move `main` back with `git branch -f main origin/main` from another branch, after confirming `git merge-base --is-ancestor <old-main> <branch>`, so nothing is ever discarded.

Related: [[cycle-orchestration-gates]], [[pipeline-claim-lifecycle]].
