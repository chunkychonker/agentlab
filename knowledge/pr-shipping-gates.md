# PR shipping gates (nightloop m5)

Durable facts from `research/2026-11-12-nightloop-real-shipper.md`, read 2026-11-12.

- `gh pr create` has no `--json` flag. It prints the PR URL on success, so parse the number from the URL's last path segment and treat anything else as a failure. Its behaviour when a PR already exists for the head branch is not in the manual, so check with `gh pr list --head <branch>` first (verify those flags with `--help`).
- `gh pr view <n> --json mergeable -q .mergeable` returns `MERGEABLE`, `CONFLICTING` or `UNKNOWN`, with `UNKNOWN` while GitHub is still computing. Poll on `UNKNOWN` only. Treat every other non-`MERGEABLE` value as blocked.
- Compose the review record into the PR body *before* the PR exists, so a PR can never be open without it. Truncate only the review section to the 65536-char body limit, never the author text, and scan it for credential patterns first (bare 40-hex SHAs must not match).
- `git pull --ff-only` succeeds when local `main` is *ahead* of origin, so "main is clean and fast-forwarded" does not mean "main has no unpushed commits". Check `rev-list --count origin/main..main` after a fetch, and move those commits to a branch before resetting `main`.
- A build phase leaves work uncommitted; the shipper must `switch -c` before committing, so a failed push never leaves commits on `main`.

Related: [[pipeline-claim-lifecycle]], [[cycle-orchestration-gates]], [[claude-p-phase-contract]].
