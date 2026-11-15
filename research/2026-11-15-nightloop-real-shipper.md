# nightloop m5: the real shipper (2026-11-15)

Mode: project:nightloop, milestone 5 (`not-started` -> `in-progress`).

## Question

How should `nightloop` push a passed cycle's branch, open the PR with the review record in its body, and run the mergeable check, so that any failure is `NotShipped(reason)`? And how should recovery also catch commits left on local `main` ahead of `origin/main`?

## Findings

**What the code already fixes.**
- The `Shipper` port is `ship(run_id, cycle) -> Shipped(pr) | NotShipped(reason)` (`nightloop/cycle/ports.py`). It may raise only for an environment error.
- `run_cycle` calls `ship` only after review is `GATED_PASS`, then always calls `recover`. `Shipped.pr` is a positive int. `NotShipped.reason` is one non-empty line.
- There is no `maintain` phase (decision 2026-11-11). The shipper is plain code, not a model, so it has to do the commit itself. In bash the maintainer model does that.
- `GitWorkspace.recover` only looks at `git status --porcelain`. It then runs `switch main` and `pull --ff-only`. If the model committed on `main`, those commits stay there. The pull does not fail when local `main` is only ahead.

**What bash does today** (`.pipeline/run.sh:769-914`, `.pipeline/review_record.sh`; read in the repo, not external):
- The maintainer pushes a branch and opens the PR, then writes the PR number to `logs/last-pr.txt`.
- `attach_review_to_pr` fails closed in these cases: the PR number is not a number, the body cannot be read, the verdict has no `Findings:` line, the section matches a credential pattern, the body leaves no room, `gh pr edit` fails, or the marker is missing when the body is read back. Any one of them means the PR is left open.
- It is idempotent through the hidden marker `<!-- agentlab-review -->`.
- The body limit is 65536 characters. Only the review section is truncated, never the maintainer's text, and a section that would keep fewer than 200 bytes is refused.
- The mergeable check polls `gh pr view N --json mergeable -q .mergeable` up to 5 times, 2s apart, while the value is `UNKNOWN`. Anything other than `MERGEABLE` leaves the PR open.
- bash's `reset_to_clean_main` has the same ahead-of-origin gap, as PLAN.md says.

**`gh` facts** (official manual, fetched 2026-11-15):
- [`gh pr create`](https://cli.github.com/manual/gh_pr_create) has `--title`, `--body`, `--body-file` (`-` reads stdin), `--base`, `--head` and `--fill`. On success it prints the new PR's URL.
- Without `--title`/`--body` or `--fill` it prompts. Without `--head` it may prompt about where to push. So an unattended call must pass `--title`, `--body-file`, `--base` and `--head` explicitly.
- The manual does not say what happens when a PR for the branch already exists. I did not verify it. Design the shipper to look first (`gh pr list --head <branch> --state open --json number,url`) rather than rely on gh's behaviour.
- [`gh pr view --json`](https://cli.github.com/manual/gh_pr_view) lists `mergeable`, `number`, `url` and `body` as fields, and `-q/--jq` filters. The manual does not list the values of `mergeable`. The `MERGEABLE` / `CONFLICTING` / `UNKNOWN` values are what `run.sh` relies on, and I did not confirm them from docs today. Treat any other string as "not mergeable" (fail closed).
- I did not verify that `GH_PROMPT_DISABLED=1` stops all prompts. The builder should check `gh help environment` before relying on it. Passing every flag explicitly is the primary defence.

No HN or blog research was needed. This is a port of known local behaviour plus two CLI facts.

## Build proposal

Extends `projects/nightloop/`. Standard library only, per the 2026-11-06 decision. No test touches GitHub or a real `gh`.

### Layer 1: intent

Add `GhShipper`, the real `Shipper`: on a passed review it commits the cycle's work on a new branch, pushes only that branch, opens a PR whose body carries the review record, and checks the PR is mergeable. Make `GitWorkspace.recover` treat commits on local `main` ahead of `origin/main` as a leak. The two changes are one milestone because the shipper must not push a failed cycle's commits.

Out of scope:
- merging, auto-merge and `gh pr merge`
- claim marking (m6)
- the CLI (m7)
- PR titles drawn from the research note
- fork workflows
- conflict resolution

### Layer 2: behavioural spec

Inputs: `run_id`, `cycle`, and constructor values `repo` (absolute), `verdict_path`, `main`, `remote`, `git` and `gh` program names, `timeout_s`, `sleep` (injected), `now`.

`ship` does these steps in order, and stops at the first failure with `NotShipped`:

1. Read the verdict file and build the review section. This is a pure port of `review_record_section`, `secret_hit` and `compose`. A missing `Findings:` line or a credential hit gives `NotShipped` before any git or gh call. Nothing is pushed.
2. Branch name: `cycle/<date>-<HHMMSS>-<cycle>-ship`, a pure function in the style of `naming.py`. Switch to a new branch from the current HEAD, so any commits already on `main` come along. Stage everything (`add -A`) and commit. If nothing is staged because the builder already committed, skip the commit. If there is nothing to ship at all (no staged change and `main` is not ahead), give `NotShipped("no changes to ship")`.
3. Push `<branch>:<branch>` to the remote. This is explicit and never pushes `main`. A failed or timed-out push gives `NotShipped`. The commit stays on the local branch.
4. Only after the push succeeds, move local `main` to `<remote>/main` with `git branch -f main <remote>/main`. This is safe because the pushed branch holds the commits. If the push failed this step is skipped, so `recover` still sees `main` ahead and snapshots it.
5. Reuse an open PR for the branch if `gh pr list --head` finds one (retry safety). Otherwise run `gh pr create --title --body-file --base --head`. The number comes from the URL gh prints (`.../pull/<n>`). If the output has no such URL, give `NotShipped`.
6. Read the body back with `gh pr view N --json body`. If the marker is missing, give `NotShipped`.
7. Mergeable poll: up to 5 reads, `sleep(2)` between them, while the value is `UNKNOWN`. `MERGEABLE` gives `Shipped(n)`. `CONFLICTING`, any other value, or still `UNKNOWN` gives `NotShipped("PR #n opened but mergeable=<value>")`.

Invariants:
- The shipper never pushes `main`, never force-pushes, and never discards work.
- `Shipped` is returned only if the PR exists, its body shows the marker, and `mergeable` is `MERGEABLE`.
- Calling `ship` again for the same run and cycle reuses the open PR and does not make a second one.
- Env, tokens and `gh` stderr beyond its last line are never logged or put in a reason.

Failure modes:
- `git` or `gh` cannot start: `OSError` propagates. This is an environment error, and the orchestrator recovers and re-raises.
- A command timeout: `NotShipped`. The PR may or may not exist, and the reason says so.
- A non-zero exit from any command: `NotShipped` with the last stderr line, shortened.

`GitWorkspace.recover` changes:
- It also counts `git rev-list --count <remote>/<main>..<main>`. This uses the existing tracking ref and runs no fetch.
- If the tree is dirty or `main` is ahead, it takes one snapshot branch, as today. It commits the dirty files only if any exist. It pushes the branch, then runs `git branch -f main <remote>/main` while on the snapshot branch, then `switch main`. The result is `Snapshotted(branch, pushed)`.
- If the tracking ref is missing or `rev-list` fails, raise `RecoveryError`. Do not guess "not ahead".
- If the push failed, do not move `main`. The commits are then still on `main` and on the local snapshot branch. Say so in the message, and raise `RecoveryError` if `main` cannot be restored to a state with no unpushed work. Decision for the builder to record: when the push fails, leave `main` ahead and raise `RecoveryError`. That stops the night and loses nothing, which is safer than a quiet carry-over.

### Layer 3: interfaces

```python
# nightloop/ship/review_section.py   (pure)
MARKER: Final[str]            # "<!-- agentlab-review -->"
BODY_LIMIT: Final[int]        # 65536
class ReviewRecordError(ValueError): ...   # no Findings, or credential hit
def review_section(verdict_text: str) -> str
def has_marker(body: str) -> bool
def compose_body(existing: str, section: str) -> str   # truncates section only

# nightloop/ship/mergeable.py        (pure)
class Mergeable(Enum): MERGEABLE; CONFLICTING; UNKNOWN; OTHER
def classify(raw: str) -> Mergeable
def poll(read: Callable[[], str], sleep: Callable[[float], None], *, attempts: int = 5, delay_s: float = 2.0) -> Mergeable

# nightloop/ship/pr_url.py           (pure)
def pr_number(gh_output: str) -> int | None

# nightloop/ship/naming.py           (pure)
def ship_branch(run_id: RunId, cycle: int, hhmmss: str) -> str

# nightloop/ship/gh.py               (edge: the only module that runs gh)
class Gh:
    def __init__(self, repo: Path, *, program: str, timeout_s: float) -> None
    def open_pr_for(self, branch: str) -> int | None
    def create_pr(self, *, branch: str, base: str, title: str, body: str) -> str
    def body_of(self, pr: int) -> str
    def mergeable_of(self, pr: int) -> str

# nightloop/ship/gh_shipper.py       (orchestrates the steps above)
class GhShipper:    # satisfies nightloop.cycle.ports.Shipper
    def __init__(self, repo: Path, verdict_path: Path, *, git: GitRunner, gh: Gh,
                 now: Callable[[], float], sleep: Callable[[float], None], main: str = "main") -> None
    def ship(self, run_id: RunId, cycle: int) -> ShipOutcome
```

`GitRunner` is a small, narrow wrapper that is shared with `GitWorkspace`. It can be extracted from `git_workspace._git` as a separate, behaviour-preserving commit (expand/contract) before the shipper uses it.

### Acceptance criteria (tests use a temp repo, a local bare `origin`, and a fake `gh` executable)

The fake `gh` is a Python script, passed as `program`. It reads a scripted state file and records its argv.

- S1. Happy path: `ship` returns `Shipped(n)`. The bare origin has the branch with the cycle's commit. `main` on origin is unchanged. The PR body passed to the fake `gh` contains the marker, the verdict word, and the Findings text.
- S2. A verdict with no `Findings:` line gives `NotShipped`, and the fake `gh` was never called and origin has no new branch.
- S3. A verdict containing `sk-ant-abcdefgh12345` gives `NotShipped`, and nothing is pushed or posted.
- S4. A pre-push hook that rejects gives `NotShipped`, the commit is on the local branch, `gh` was not called, and local `main` was not moved.
- S5. `mergeable` is `CONFLICTING`: `NotShipped` naming the PR number. `UNKNOWN` twice then `MERGEABLE` gives `Shipped`, with `sleep` called twice and no real sleep. `UNKNOWN` five times gives `NotShipped`.
- S6. A second `ship` call with the fake `gh` reporting an open PR for the branch does not call `pr create`.
- S7. A `gh` that prints no URL gives `NotShipped`. A body read back without the marker gives `NotShipped`.
- S8. A missing `gh` program raises `OSError`.
- S9. The shipper never runs `git push` with `main` as a refspec and never runs `--force` (assert on the recorded git argv via a wrapper program).
- R1. In a night where the model commits on `main` and the review fails, `recover` returns `Snapshotted`. The commit is on a `cycle/...-unshipped-...` branch on origin, local `main` equals `origin/main`, and the tree is clean.
- R2. Dirty files plus commits ahead go into one snapshot branch, not two.
- R3. A push failure in recovery raises `RecoveryError`, the commits are still reachable from local `main`, and nothing is lost.
- R4. A missing `origin/main` ref raises `RecoveryError`.
- R5. Existing 229 tests still pass, and the m4 clean-tree path still returns `Clean()`.
- R6. Purity tests: the `ship/` pure modules import no I/O module (extend the existing `test_*_purity.py` style).
- Mutation checks (the style PLAN.md reports for m4): dropping the marker check, or pushing `main`, or skipping the secret scan each fail at least one test.

Live check (documented human step, not a test): run once against a throwaway GitHub repo, and diff real `gh pr create` and `gh pr view --json mergeable` output against the fake's.

## Open questions

- The `mergeable` value set and gh's behaviour for a branch that already has a PR are unconfirmed from docs today. The design does not depend on either (unknown values fail closed, and the list-first check handles the duplicate).
- Whether `GH_PROMPT_DISABLED=1` is honoured should be checked with `gh help environment`.
- Does the ahead check need a `fetch` first? Without one it uses the ref from the last pull, which is what the cycle started from. A decision for the builder to log. I recommend no fetch, so recovery stays offline-safe.
- The PR title is generic (`nightloop: cycle N of run ID`). Taking it from the research note would need the spec path passed through the port, which is a later change.
- Pages fetched were only the two gh manual pages. No instruction in them asked for anything beyond reading.
