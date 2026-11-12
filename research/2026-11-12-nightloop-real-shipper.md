# nightloop milestone 5: the real shipper

Date: 2026-11-12. Mode: project:nightloop. Extends `projects/nightloop/` (no new `examples/` dir).

## Question

How does a stdlib-only Python `Shipper` turn a passed review into a pushed branch and an open PR (review record in the body, mergeable check), without ever crashing or silently succeeding? And how does recovery also catch commits left on local `main`?

## Findings

**What bash does (read 2026-11-12, `.pipeline/run.sh` lines 756-920, `.pipeline/review_record.sh`).** A `maintain` model phase commits, pushes and opens the PR, then writes the PR number to `logs/last-pr.txt`. Then `run.sh` does the following, mechanically:
- `attach_review_to_pr` reads the PR body, builds an "## Independent review" section from the verdict file (`Verdict`, `Ran:` block, `Findings:` block), scans it for credential patterns, composes it under the 65536-char body limit, runs `gh pr edit --body-file`, then reads the body back and checks for the hidden marker `<!-- agentlab-review -->`.
- Any failure means "left open, not merging".
- `gh pr view N --json mergeable -q .mergeable` is polled up to 5 times, 2s apart, while the value is `UNKNOWN`. Only `MERGEABLE` proceeds. Merging is auto-merge's job, not the shipper's.

nightloop's config has no `maintain` phase (decision 2026-11-11), so the shipper does the git and `gh` work directly, deterministically, with no model. That drops one model call and one trust boundary. Bash's maintainer is told not to merge, and its tool allowlist enforces that. Here the shipper has no code path that merges.

**`gh` facts (official manual, fetched 2026-11-12).**
- `gh pr create` has `--head`, `--base`, `--title`, `--body-file` (`-` reads stdin), `--draft` and `--fill`. It has **no `--json` flag**. On success it prints the PR URL to stdout, so the number is parsed from the URL's last path segment (`.../pull/123`). Parse strictly: anything else is `NotShipped`, not a guess.
- If some `--attach` uploads fail, the PR is still created, the exit is non-zero, and the URL is still printed. We pass no `--attach`, so this does not apply, but it is the reason to parse the URL and not trust the exit code alone. When the exit is non-zero, do not hunt for a URL; treat it as a failure.
- `gh pr view <number|url|branch> --json mergeable,mergeStateStatus,body,number,url -q ...` exists. `--json` and `-q/--jq` are confirmed in the manual. The `mergeable` values `MERGEABLE`, `CONFLICTING` and `UNKNOWN` are the ones bash already relies on. I could not confirm that list in the manual itself (the page only names the field), so treat any other value as "not mergeable".
- The manual does not say what `gh pr create` does when a PR already exists for the head branch, and a web search did not settle it. So the shipper must not rely on it. Make it idempotent explicitly: before creating, run `gh pr list --head <branch> --state all --json number` and reuse or refuse. The `--head` and `--state` flags of `gh pr list` are not verified in this note's fetches. The builder should check `gh pr list --help` (or the manual page) before relying on them.
- Sources: https://cli.github.com/manual/gh_pr_create and https://cli.github.com/manual/gh_pr_view, both fetched 2026-11-12.

**Existing nightloop seams (read 2026-11-12).**
- `cycle/ports.py`: `Shipper.ship(run_id, cycle) -> Shipped(pr) | NotShipped(reason)`. It may raise only for an environment error, and then the orchestrator still recovers.
- `Shipped.pr` is a positive int. `NotShipped.reason` is one non-empty line.
- `GitWorkspace` is the only module that runs git. `recover()` checks only `git status --porcelain`. `pull --ff-only` succeeds when local `main` is *ahead* of origin, so commits on local `main` carry silently into the next cycle. That is the gap #89's review found.
- `tests/git_support.py` gives a real clone plus a local bare `origin`, with the global git config isolated. Reuse it.
- Decision 2026-11-10: if a program cannot start (`OSError`), raise. It is an environment error, not a model failure. The same rule applies to a missing `gh` or `git`.

**Ordering consequence.** The build phase leaves its work *uncommitted* on `main` (the review phase reviews the working-tree diff). So `ship` must: create the PR branch, commit there, push, create the PR, then switch back to `main`. After that, `recover()` runs and finds a clean tree. If the shipper commits on `main` and then fails, those commits are exactly the leak the recovery fix must catch. The shipper therefore never commits on `main` (it uses `switch -c` first), and recovery independently defends against it.

## Build proposal

### Intent

Add `nightloop/ship/`: a `GitHubShipper` that implements the existing `Shipper` port on real git and `gh`. It publishes the cycle's uncommitted work as a branch, opens a PR whose body carries the review record, and checks mergeability. Every failure that is not an environment error is `NotShipped(reason)`. In the same increment, extend `GitWorkspace.recover` so commits on local `main` ahead of `origin/main` are moved to a recovery branch and `main` is reset to `origin/main`.

Out of scope: merging; waiting for CI; conflict resolution; labels, reviewers, drafts; a `maintain` model phase; claim marking (milestone 6); CLI wiring (milestone 7); changing the `Shipped`/`NotShipped` types; handling commits on a branch other than `main`.

### Behavioural spec

Inputs, via the constructor (the edge builds typed values; `ship` itself reads no env):
- `repo: Path` (absolute), the same checkout as `GitWorkspace`.
- `read_verdict: Callable[[], str]`, which gives the verdict file's text (an edge function, so the core stays pure).
- `git`, `gh`: the two edge ports below.
- `sleep: Callable[[float], None]` and `mergeable_attempts: int = 5`, `mergeable_wait_s: float = 2.0` (same numbers as bash).

Pure pieces (no I/O, each in its own module):
- `review_record.py`: `review_section(verdict_text) -> str | None`. It returns the marker plus an "## Independent review" section with Verdict, Ran and Findings. It returns `None` if there is no `Findings:` line. `has_secret(text) -> bool` uses the bash pattern list ported verbatim, including that bare hex is not matched. `compose_body(base, section) -> str | None` truncates only the section, never the base, to the 65536 limit with a note, and returns `None` if less than 200 chars of room remain.
- `pr_text.py`: `branch_name(run_id, cycle) -> str` is `nightloop/<run_id.value>-<cycle>`, valid as a git ref. `pr_title(...)` and `pr_base_body(...)` give one-line, deterministic text. `pr_number_from_url(stdout) -> int | None` accepts only `https://<host>/<owner>/<repo>/pull/<n>` on the last non-blank line.
- `mergeable.py`: `decide(value: str) -> Mergeable.READY | WAIT | BLOCKED(value)`. Only `MERGEABLE` is READY, only `UNKNOWN` is WAIT, and everything else is BLOCKED.

Ports (declared in `ship/ports.py`):
- `BranchPublisher.publish(branch, message) -> Published | PublishFailed(reason)`. The real one runs `switch -c`, `add -A`, `commit`, `push -u origin <branch>`, then `switch main`.
- `PullRequests.find(branch) -> list[int]`, `create(branch, title, body) -> str` (stdout), `body_of(pr) -> str`, `mergeable_of(pr) -> str`. Each raises `GhFailed(reason)` on non-zero exit or timeout, and `OSError` if `gh` cannot start.

`ship(run_id, cycle)` steps, in order, with the first failure ending it:
1. `section = review_section(read_verdict())`. If it is `None`, or `has_secret(section)`, or `compose_body` is `None`: `NotShipped`. **No branch is pushed and nothing is committed**, so the work is left for recovery to snapshot. The record is composed *before* any push, so a PR can never exist without it.
2. `publish(branch, ...)`. If it fails, `NotShipped("push failed: ...")`. After it returns, the tree is on `main` and clean, whether it succeeded or not (the failed-push work is safe in a local commit on the PR branch, never on `main`).
3. `find(branch)`. If a PR already exists, `NotShipped("a PR already exists for <branch>")`. A retry must not open a second PR.
4. `create(...)`. Parse the number from stdout. If it cannot be parsed, `NotShipped`, and the reason says a PR may exist.
5. Read the body back. If the marker is missing, `NotShipped` with the PR number in the reason.
6. Poll `mergeable_of` up to `mergeable_attempts` times, sleeping between `WAIT`s. `READY` gives `Shipped(pr)`. `BLOCKED` or still `WAIT` gives `NotShipped("PR #N opened but mergeable=<v>")`. The PR stays open for a human, as in bash. (`Shipped` is therefore the claim "PR is open, reviewed and mergeable". No change to the result types.)

Environment errors (`OSError` from git or gh, i.e. the program is missing) are raised, not returned.

Recovery extension (`GitWorkspace.recover`):
- After the existing dirty check, run `fetch <remote> <main>` and `rev-list --count <remote>/<main>..<main>`.
- If the count is > 0, the commits go to a recovery branch (the same `recovery_branch` name) *before* `main` moves. Steps: `switch -c <branch>`, commit any dirty work on it (same message path as today), `push -u`, `switch main`, then check that `rev-parse <branch>` contains the old `main` tip (`merge-base --is-ancestor`) before `reset --hard <remote>/<main>`. If that check fails, raise `RecoveryError` and do not reset.
- A failed fetch is `RecoveryError`. It is not skipped, because skipping would silently reopen the gap.
- `Snapshotted(branch, pushed)` is returned in this case too. No new recovery type.
- Clean and in sync: unchanged behaviour, with one extra read-only call.

Invariants:
- No code path in `ship/` runs `gh pr merge`, `git push --force`, or a push to `main`. A test greps the module sources and asserts this, in the style of the existing `test_*_purity.py`.
- `ship/` core modules import no I/O module (extend the purity test).
- A PR never exists without the review marker in its body at creation.
- After `ship` returns, the checkout is on `main` with a clean tree and `main == origin/main`, on every outcome (Shipped, NotShipped, or the environment-error raise where possible).

### Acceptance criteria (checkable)

Tests use a real clone with a local bare `origin` (`tests/git_support.py`) and a **fake `gh`**: a Python stand-in executable (`tests/fake_gh.py`) that records argv to a JSON file and answers from a scenario file. No test reaches GitHub.
- S1. Happy path: a dirty tree plus a PASS verdict file gives `Shipped(7)`. `origin` has the branch `nightloop/<run>-1`, `main` equals `origin/main`, the tree is clean, and the fake `gh` saw `pr create` with a `--body-file` containing the marker and the Findings text.
- S2. `compose_body` truncation: a 70k-char Findings block gives a body of at most 65536 chars whose base text is intact and which ends with the truncation note. 100 chars of room gives `None`.
- S3. A verdict with no `Findings:` gives `NotShipped`, no branch on `origin`, the work still uncommitted, and **zero `gh` calls**.
- S4. A verdict containing `sk-ant-` plus 20 chars, or `ghp_` plus 36 chars, gives `NotShipped`, and the secret never appears in any recorded `gh` argv or in the reason string. A 40-hex commit SHA in Findings does not trigger it.
- S5. A pre-push hook that rejects gives `NotShipped("push failed...")`, the commit is on the local PR branch, none is on `main`, `main == origin/main`, and `gh` made no `pr create` call.
- S6. `gh pr list` reports an existing PR for the branch gives `NotShipped`, and `pr create` is not called.
- S7. `pr create` prints garbage or a URL with no number gives `NotShipped`. `pr create` exits 1 gives `NotShipped`. The reason is one line in every case.
- S8. The body read back lacks the marker gives `NotShipped` naming the PR number.
- S9. `mergeable` is `UNKNOWN`, `UNKNOWN`, then `MERGEABLE` gives `Shipped`, with exactly 2 injected `sleep(2.0)` calls. Always `UNKNOWN` gives `NotShipped` after exactly `mergeable_attempts` reads. `CONFLICTING` gives `NotShipped` on the first read, with no sleep.
- S10. `gh` missing (nonexistent program path) raises `OSError`. It is not `NotShipped`.
- R1. Recovery gap, the reproduction that fails on current `main`: commit on local `main` ahead of `origin/main` with a clean tree. After `recover`, the result is `Snapshotted`, the branch holds the commit, `main == origin/main`, and the commit is not reachable from `main`. Write this test **first** and watch it fail (Protocol section 6).
- R2. Ahead commits plus dirty tree gives one branch holding both. `main` is reset and clean.
- R3. A failed fetch (remote path removed) raises `RecoveryError`, and `main` is not reset.
- R4. In sync and clean gives `Clean()`, as before. The existing 229 tests still pass.
- R5. End to end through `run_night` with the real `GitWorkspace`, the real `GitHubShipper` and the fake `gh`: a shipper that left a commit on `main` (simulated by a test shipper wrapping the real one) does not leak into cycle 2's tree.
- P1. Purity test: no `ship/` core module imports `subprocess`, `os`, `socket` or `pathlib` I/O calls, and no `ship/` source contains `pr merge`, `--force`, or `push origin main`.
- Run: `cd projects/nightloop && python3 -I -m unittest discover -v`, stdlib only, passing on 3.11-3.13.

### Interfaces (stubs only)

```python
# nightloop/ship/mergeable.py
class Mergeable(Enum): READY; WAIT; BLOCKED
def decide(value: str) -> Mergeable: ...

# nightloop/ship/review_record.py
REVIEW_MARKER = "<!-- nightloop-review -->"   # one constant, defined once
BODY_LIMIT = 65536
def review_section(verdict_text: str) -> str | None: ...
def has_secret(text: str) -> bool: ...
def compose_body(base: str, section: str) -> str | None: ...

# nightloop/ship/pr_text.py
def branch_name(run_id: RunId, cycle: int) -> str: ...
def pr_title(run_id: RunId, cycle: int) -> str: ...
def pr_number_from_url(stdout: str) -> int | None: ...

# nightloop/ship/ports.py
@dataclass(frozen=True) class Published: ...
@dataclass(frozen=True) class PublishFailed: reason: str
class BranchPublisher(Protocol): def publish(self, branch: str, message: str) -> Published | PublishFailed: ...
class GhFailed(RuntimeError): ...
class PullRequests(Protocol):
    def find(self, branch: str) -> list[int]: ...
    def create(self, branch: str, title: str, body: str) -> str: ...
    def body_of(self, pr: int) -> str: ...
    def mergeable_of(self, pr: int) -> str: ...

# nightloop/ship/shipper.py
class GitHubShipper:
    def __init__(self, *, read_verdict: Callable[[], str], git: BranchPublisher, gh: PullRequests,
                 sleep: Callable[[float], None], mergeable_attempts: int = 5, mergeable_wait_s: float = 2.0): ...
    def ship(self, run_id: RunId, cycle: int) -> ShipOutcome: ...

# edge: nightloop/ship/git_publisher.py (GitBranchPublisher), nightloop/ship/gh_cli.py (GhCli)
```

Note on the marker: bash's is `<!-- agentlab-review -->`. nightloop uses its own, named once, so the two tools never confuse each other's PRs. Record this in the decisions log.

Failure domain: one PR branch plus one PR per cycle. If `gh` is down, the cycle's work sits on a pushed branch and the night continues.

## Open questions

- `gh pr create`'s behaviour when a PR already exists for the head branch is undocumented in the manual. The `find` precheck sidesteps it, but a race (a PR created between `find` and `create`) is untested.
- The exact `gh pr list --head/--state/--json` flags were not verified here. The builder should check `gh pr list --help` before relying on them.
- Commit authorship. Bash's maintainer commits as a fixed identity. This proposal uses the repo's own git config and leaves identity to the owner. If an owner wants a fixed author, that is a later config field.
- Commits left on a branch other than `main` are not handled by the recovery extension. They are noted as a limit.
- Whether `Shipped` should carry the mergeable state is left alone. This proposal avoids changing the result types, so a conflicting PR comes back as `NotShipped` with the number in the reason. If the owner prefers a PR-opened-but-conflicting case to be a `Shipped` variant, that is a decisions-log change.
