# nightloop milestone 5: the real shipper

Date: 2026-11-13. Project mode, `projects/nightloop/`, milestone 5.

## Question

How should a `Shipper` push a passed cycle's branch, open the PR with the review record in its body, and run the mergeable check, so that every failure is `NotShipped(reason)`? And how should `recover` also catch commits left on local `main` ahead of `origin/main`?

## Findings

Sources: agentlab's own `.pipeline/run.sh` (lines 756-920, read today), `.pipeline/review_record.sh`, and the `gh` manual pages fetched 2026-11-13.

1. **`gh pr create` prints only the PR URL on success.** Per the [gh_pr_create manual](https://cli.github.com/manual/gh_pr_create), fetched 2026-11-13, the URL goes to stdout. The page lists no `--json` option, so the PR number must be parsed from the URL's trailing `/pull/<N>`. A URL that does not match is `NotShipped`, never a guess.
   - `--head <branch>` skips forking and pushing. The shipper pushes explicitly and passes `--head` and `--base`, so `gh` never prompts or pushes on its own.
   - `--title` and `--body-file` (`-` for stdin) skip the interactive prompts.
   - The manual does not say what happens when a PR already exists for the branch, nor does it list exit codes. Treat any non-zero exit as `NotShipped`, and unit-test that against the fake `gh`. The existing-PR behaviour is an open question.
2. **`gh pr view` supports `--json body,mergeable,number,url`.** Per the [gh_pr_view manual](https://cli.github.com/manual/gh_pr_view), fetched 2026-11-13, the PR can be selected by number, URL or branch. The manual does not list the values `mergeable` takes. A [web search on 2026-11-13](https://github.com/cli/cli/pull/5999) found community and GraphQL-schema sources that agree on `MERGEABLE`, `CONFLICTING` and `UNKNOWN`, but I did not find an official `gh` statement. Bash only proceeds on exactly `MERGEABLE` and retries while `UNKNOWN`, up to 5 times, 2s apart. Port exactly that: `MERGEABLE` passes; any other value, including a value not in the three, is a refusal. The retry count and the sleep are injected (a `sleep` callable), so tests do not wait.
3. **Request the JSON object and parse it in Python.** Bash uses `-q .body`, which depends on `gh`'s built-in jq. `--json body,mergeable` with no `-q`, parsed by `json.loads`, keeps the shell thin and lets the fake `gh` be a plain script that prints JSON. Anything that does not parse into a dict with the expected types is `NotShipped("unreadable gh output")`.
4. **The review record is already specified in bash and is pure.** `.pipeline/review_record.sh` defines:
   - the marker `<!-- agentlab-review -->`;
   - the section (heading `## Independent review`, the verdict word, the verdict file's `Ran:` and `Findings:` blocks verbatim);
   - a refusal when there is no `Findings:` line;
   - a credential-pattern scan (patterns listed in that file);
   - truncation at 65536 bytes that cuts only the review section, refuses if fewer than 200 bytes are left, and never splits a multi-byte character.

   Port it as one pure module that takes text and returns `Body | Refusal`. One difference from bash: bash attaches the record to an existing PR with `gh pr edit` after the maintainer opened it. Here the shipper owns PR creation, so the record goes into the body at creation, and the shipper reads the body back and checks for the marker (bash's read-back check). That removes the `gh pr edit` step and one failure mode.
5. **The build phase leaves work uncommitted, so the shipper does the commit.** In bash the maintainer model commits, pushes and opens the PR. The nightloop config has no maintain phase (decisions log, 2026-11-11), so this is deterministic git. The sequence is `switch -c <branch>`, `add -A`, `commit`, `push -u`, `gh pr create`, `gh pr view`, then `switch main` and `pull --ff-only`. On any failure after the commit, the branch holds the work and the later `recover` finds a clean tree.
6. **The local-main-ahead gap.** `git pull --ff-only origin main` on a `main` that is ahead of `origin/main` succeeds as a no-op, so `_restore_main` today accepts it and the commits carry into the next cycle. The fix needs the remote-tracking ref to be fresh, so fetch first. Then `git rev-list --count origin/main..main` is the number of stray commits. Recovery for that case, with nothing ever discarded:
   - stay on `main` and `switch -c <recovery-branch>`, which keeps the commits;
   - commit any dirty files on it, as today;
   - push;
   - `git branch -f main origin/main` (allowed because `main` is not checked out), then `switch main`.

   The branch tip must contain `main`'s old tip. Check `git merge-base --is-ancestor <old-main-sha> <branch>` before moving `main`, and raise `RecoveryError` if it does not hold. This is also the reason a shipper can never push a failed cycle's commits: it pushes only its own branch, and `ship` refuses up front if `main` is ahead (see the spec).
7. **Divergence is a separate case.** If `main` has commits not in `origin/main` and `origin/main` has commits not in `main`, `--ff-only` fails. That already raises `RecoveryError`. Keep it, but the new ahead-check runs first, so the stray commits are saved to a branch before the pull is attempted.
8. **Practitioner view.** Not consulted. The mechanics here are `git` and `gh` behaviour that the manuals and agentlab's own bash already pin down.

## Build proposal

Extends `projects/nightloop/nightloop/cycle/`, which is the existing code. It does not contradict the decisions log. The 2026-11-12 owner entry assigns this milestone, and the 2026-11-11 entry keeps `NotShipped` as a `CycleResult` case. A new dated decisions entry is needed for the three interface notes below.

### Layer 1: intent

Replace `FakeShipper` in production with a `GhShipper` that turns a passed cycle's uncommitted work into a pushed branch and an open PR, with the review record in the body, then checks the PR is cleanly mergeable. Also close the recovery gap that lets commits on local `main` leak into the next cycle. Out of scope: merging the PR (bash's `gh pr merge --delete-branch` stays unported; the milestone says only "runs the mergeable check"), claim marking (m6), the CLI (m7), conflict resolution, and posting to anything but the repo's own `origin`.

### Layer 2: behavioral spec

Inputs to `ship(run_id, cycle)`: the checkout, a verdict file path, a spec path, and config (`main`, `remote`, `gh`, `git` programs, timeouts) given at construction. Outputs: `Shipped(pr)` or `NotShipped(reason)`.

Invariants:
- **S1** `ship` never raises for a refused or failed step (push, `gh`, parse, mergeable). It raises only for an environment error (a program that cannot be started), as the port docstring says.
- **S2** `ship` pushes only the branch it created, `cycle/<date>-<HHMMSS>-<cycle>-ship`, never `main`. It never passes `--force`, and never passes `--force-with-lease`.
- **S3** If local `main` is ahead of `origin/main` on entry, `ship` returns `NotShipped` before creating anything and pushes nothing.
- **S4** The PR body contains the marker and the verdict file's `Findings:` block verbatim. If the review text matches a credential pattern, the body has no review text, `gh pr create` is never called, and the result is `NotShipped`.
- **S5** After `gh pr create`, the shipper reads the body back. A missing marker means `NotShipped` naming the PR number.
- **S6** Mergeable: `UNKNOWN` is retried up to 5 times with an injected sleep. Only `MERGEABLE` is `Shipped`. `CONFLICTING`, still `UNKNOWN`, or any other value gives `NotShipped("PR #N left open: mergeable=<value>")`. The PR number is in the reason because the PR exists.
- **S7** Whatever the outcome, the checkout is back on `main` with a clean tree. If the shipper's own `commit` fails, its empty branch is deleted and the work stays uncommitted for `recover`.
- **S8** Idempotence on re-run in the same second: a branch-name collision gives `NotShipped`, never an overwrite.

Recovery additions:
- **R1** `recover` fetches, then counts `origin/main..main`. A count greater than 0 snapshots those commits (and any dirty files) to the recovery branch, pushes it, and resets `main` to `origin/main` with `branch -f`, only after the ancestor check passes. Result: `Snapshotted(branch, pushed)`.
- **R2** If the ancestor check fails, or the branch could not be created, `RecoveryError` and `main` is untouched. Nothing is ever discarded.
- **R3** A failed fetch is a `RecoveryError` (the check cannot be trusted), not a skip.
- **R4** The existing dirty-only behaviour and all 229 existing tests are unchanged.

Acceptance criteria (checkable):
- **A1** Against a local bare `origin` and a fake `gh` that prints `https://github.com/o/r/pull/7` and serves `{"body":..., "mergeable":"MERGEABLE"}`: the result is `Shipped(7)`. `origin` has the ship branch. The `gh pr create` argv has `--head`, `--base main` and a `--body-file` whose content has the marker and the findings. Main is clean and equal to `origin/main`.
- **A2** `CONFLICTING` gives `NotShipped` with `mergeable=CONFLICTING`. `UNKNOWN` three times then `MERGEABLE` gives `Shipped` after exactly 3 sleeps. `UNKNOWN` six times gives `NotShipped`.
- **A3** A pre-push hook that rejects gives `NotShipped("push failed...")`, and `gh` is never called.
- **A4** The fake `gh` exits 1 on `pr create`, or prints a non-URL, or prints invalid JSON on `pr view`, or returns the body without the marker: each gives `NotShipped`, never an exception.
- **A5** Findings containing `sk-ant-aaaaaaaaaaaa` give `NotShipped` and `gh` is never called.
- **A6** A verdict file without `Findings:` gives `NotShipped`.
- **A7** With a commit on local `main` ahead of `origin/main`: `ship` returns `NotShipped` and pushes nothing. `recover` returns `Snapshotted` with a branch containing the commit. Afterwards `main == origin/main`, the tree is clean, and the commit is reachable from the branch and from `origin`'s copy of it.
- **A8** The recovery ancestor check is tested by making `main` unmovable (for example, a failing `branch -f` stand-in git program). It must give `RecoveryError`, with `main` still pointing at its old tip.
- **A9** Review-record unit tests are ported from `.pipeline/test_review_record.sh`: truncation cuts only the review section, the 200-byte floor, the multi-byte boundary, and each secret pattern.
- **A10** The purity test (`test_cycle_purity.py` style) shows the record composer and the `mergeable`/URL parsers import no I/O module.
- **A11** Mutation check: dropping the ahead-check in `ship`, the marker read-back, or the `UNKNOWN` retry each fails at least one test.

### Layer 3: interfaces (no bodies)

```python
# nightloop/cycle/review_record.py  (pure)
@dataclass(frozen=True)
class PrBody: text: str
@dataclass(frozen=True)
class RecordRefused: reason: str          # one line
def compose_pr_body(summary: str, verdict_text: str) -> PrBody | RecordRefused: ...
def has_marker(body: str) -> bool: ...
REVIEW_MARKER: Final[str]; BODY_LIMIT: Final[int] = 65536

# nightloop/cycle/gh_parse.py  (pure)
def parse_pr_number(stdout: str) -> int | None: ...        # trailing /pull/<N> of the URL
@dataclass(frozen=True)
class PrView: body: str; mergeable: str
def parse_pr_view(stdout: str) -> PrView | None: ...       # None: not a dict, wrong types

# nightloop/cycle/mergeable.py  (pure decision)
class MergeCheck(Enum): MERGEABLE; RETRY; REFUSED
def decide(value: str, attempt: int, max_attempts: int) -> MergeCheck: ...

# nightloop/cycle/gh_shipper.py  (the only new module that runs processes)
class GhShipper:
    def __init__(self, repo: Path, *, verdict_path: Path, title: Callable[[], str],
                 now: Callable[[], float], sleep: Callable[[float], None],
                 main: str = "main", remote: str = "origin",
                 git: str = "git", gh: str = "gh", timeout_s: float = 120.0) -> None: ...
    def ship(self, run_id: RunId, cycle: int) -> ShipOutcome: ...
```

`GitWorkspace.recover` gets its new branch (fetch, count, snapshot, `branch -f`). Its signature is unchanged. Three interface notes for the decisions log:

1. `compose_pr_body` takes the verdict text, not a path, so the file read stays in the shell.
2. The ship branch is named `cycle/<date>-<HHMMSS>-<cycle>-ship` so it can never collide with the `-unshipped-` recovery branch.
3. The PR number is parsed from the URL because `gh pr create` has no JSON output.

Tests live in `tests/test_cycle_review_record.py`, `test_cycle_gh_parse.py`, `test_cycle_gh_shipper.py` and additions to `test_cycle_git_workspace.py`. The fake `gh` is a Python stand-in executable in the style of `tests/phase_support.py`: it records argv to a file and answers from a scripted state file.

Failure domain: a `GhShipper` bug can open a bad PR or leave a pushed branch. It cannot touch `main` (S2), and the unmerged PR is the human gate. Nothing here merges.

## Open questions

- What does `gh pr create` do when a PR for the head branch already exists? The manual does not say. The spec treats any non-zero exit as `NotShipped`. A human smoke run should confirm.
- Whether `gh pr view --json mergeable` can return a value outside `MERGEABLE`/`CONFLICTING`/`UNKNOWN` is unconfirmed. The design refuses anything but `MERGEABLE`.
- Where does the PR title come from? Proposed as an injected `title()` callable (m7's CLI would supply the spec note's first heading). A fixed placeholder is acceptable for this milestone.
- Whether main-ahead should make `ship` refuse or snapshot-and-continue is a judgement call. The spec picks refuse (S3) as the conservative choice.
