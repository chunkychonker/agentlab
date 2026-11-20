# nightloop milestone 7b: claim wiring

Date: 2026-11-20. Mode: project:nightloop. Milestone 7b (the topmost `not-started`;
m7 is `done 103`).

## Question

How should `nightloop run` call m6's two reconcilers, and own the git steps m6
leaves to the caller, without a night ever committing something that is not a
backlog edit?

## Findings

No web research this cycle. The question is internal: the only sources are this
repo's own code, read 2026-11-20. No external API fact is involved. Sources:

- `.pipeline/run.sh` (bash reference):
  - `reconcile_stranded_claims` runs before the loop (line 1420) and after
    every cycle's `reset_to_clean_main` (1451), *before* the usage-limit
    `break`. It commits with `git add BACKLOG.md && git commit && git push
    origin main`. On failure it logs and returns 0, and the "postflight
    snapshot will rescue the edit".
  - `reconcile_shipped_claim` runs only after `gh pr merge` succeeds (988).
- `nightloop/claims/reconcile.py` (m6): both reconcilers edit the file only.
  They never commit. They need a clean, fetched main (documented known limit).
- `nightloop/cycle/night.py`: `run_night` owns the cycle loop, so a
  between-cycles step cannot live in the CLI without a hook.
- `nightloop/cycle/git_workspace.py` (m5): `recover` counts
  `<remote>/main..main` and snapshots commits ahead of origin to a branch.
  So a backlog commit whose push failed is not lost: the next recovery
  carries it to a branch. If *that* push fails too, `RecoveryError` stops
  the night.

Three facts shape the design:

1. **`reconcile_shipped` cannot mark tonight's PRs.** nightloop opens PRs and
   never merges them (m5: "It never merges"). `GitClaimSource.merged_claim`
   reads `mergeCommit`, which an open PR does not have, so it returns
   `Unreadable`. In bash the call sits after `gh pr merge`; nightloop has no
   such moment. The PLAN's wording ("`reconcile_shipped` for each shipped PR")
   would therefore always yield `UNREADABLE` for a PR from the same night.
   Making it useful needs memory of PRs shipped on *earlier* nights.
   Nothing records that: the metrics lines carry no PR number. I propose a
   decision-log entry (see Build proposal, D4) instead of guessing.
2. **The backlog commit must be path-limited.** `git commit -- <backlog>`
   (pathspec) or `git add <backlog>` followed by `commit -- <backlog>` keeps
   unrelated dirt out of it. `GitWorkspace.recover` is the thing that handles
   dirt, not this step.
3. **Where it runs matters.** After a cycle, `recover` has just returned main
   to clean, current state. Before the first cycle, nothing has. So the
   pre-night step needs its own precondition check.

## Build proposal

Extends `projects/nightloop/`. No new example directory. Touches `cycle/night.py`
(one optional parameter), adds `nightloop/claims/` modules, and wires
`cli/main.py`. Does not touch `.pipeline/`.

### Layer 1: intent

Make a night keep the backlog honest: before the cycles and after each cycle,
mark recovery branches' claims as stranded and publish that edit to `main`, and
after the night try to mark each PR it opened done. **Out of scope:** merging
PRs; a ledger of PRs from earlier nights (7c candidate); replenishment and health
findings (not ported, see m6 limits); deleting branches; conflict resolution;
`.pipeline/` changes.

### Layer 2: behavioural spec

Inputs: a `ClaimSource`, a `BacklogStore`, a `BacklogPublisher` (new port,
below), and the night's `NightResult`.

Outcomes: `ClaimStepResult = Published(count) | Nothing | Skipped(reason) |
PublishFailed(reason)`.

Invariants and acceptance criteria (each checkable):

- **W1** With no recovery branches and nothing to mark, the step leaves the
  repo's refs, tree and backlog bytes unchanged and returns `Nothing`. It makes
  no commit and no push.
- **W2** If `reconcile_stranded` reports any outcome with `changed`, the
  publisher is called once with a message naming the count, e.g. `chore(backlog):
  reconcile 2 claim(s) stranded on unshipped branches`. Test on a temp repo
  with a bare `origin`: `origin/main` gains exactly one commit, and that
  commit touches only the backlog path.
- **W3** Unrelated dirt cannot leak. Create an untracked file and a modified
  tracked non-backlog file before the step. After a publish, both are
  byte-identical and absent from the new commit.
- **W4** Precondition: the checkout is on `main` and `main` is not behind
  `origin/main` after a fetch. If it is on another branch, or behind, or the
  fetch fails, the step is `Skipped(reason)` and **writes nothing**. This
  includes the backlog file. The night itself continues, because claim
  bookkeeping is not a reason to waste a night. (Bash would abort in
  preflight; this is a deliberate difference, recorded as D1.)
- **W5** Push rejected or fails: `PublishFailed(reason)`. The local commit is
  left in place, not reset. It then rides the existing m5 path: the next
  `recover` sees main ahead of `origin/main` and snapshots it. The step
  never force-pushes and never touches any ref but `main`.
- **W6** Idempotent: running the step twice in a row, with the first one
  published, makes the second `Nothing` (m6 I1 plus a published tree).
- **W7** Order in the night: pre-night step → cycle 1 → step → cycle 2 → step.
  The step after a `UsageLimited` cycle still runs, then the night stops, as
  in bash. Test with `FakeRunner`/recording publisher and assert the call
  order.
- **W8** `run_night` without the new parameter behaves exactly as before; all
  424 existing tests pass unchanged (expand/contract).
- **W9** Post-night shipped step: for each `Shipped(pr)` in the night, call
  `reconcile_shipped`. `UNREADABLE` for an unmerged PR is reported as
  `pending (PR not merged)` and is **not** an error and **not** a publish
  trigger. If an outcome is `MARKED_DONE` (a PR merged during the night, e.g. by a
  human), publish it as in W2.
- **W10** Failures of the environment (`OSError`, `ClaimSourceError`) propagate
  as for the rest of the night and map to `ExitCode.ENVIRONMENT` for `OSError`,
  as in m7. A `ClaimSourceError` raised in the pre-night step is `Skipped`,
  not a crash, because nothing has run yet that needs recovering. Choose and
  state this in the docstring (D2).
- **W11** `--dry-run` shows the claim steps in the plan and runs none of them
  (D2 of m7 extended; a tree digest shows no writes).
- **W12** A new CLI flag `--no-claims` skips every claim step, for repos
  without a backlog file. A config whose backlog file does not exist makes the
  step `Skipped("no backlog file ...")` rather than an error. Decide in the
  build whether the path is configurable; the minimum is the constant
  `BACKLOG.md`, matching `BacklogFile(repo, relative="BACKLOG.md")`.

### Layer 3: interfaces (no bodies)

```python
# nightloop/claims/publish.py   (pure: the port + result types)
@dataclass(frozen=True) class Published: count: int
@dataclass(frozen=True) class Nothing: ...
@dataclass(frozen=True) class Skipped: reason: str          # one line
@dataclass(frozen=True) class PublishFailed: reason: str    # one line
ClaimStepResult = Union[Published, Nothing, Skipped, PublishFailed]

class BacklogPublisher(Protocol):
    def ready(self) -> Skipped | None: ...      # on main? fetched? not behind?
    def publish(self, message: str) -> Published | PublishFailed: ...
    # both: OSError if git cannot start

# nightloop/claims/step.py      (pure: decides, via the ports)
def claim_step(source: ClaimSource, backlog: BacklogStore,
               publisher: BacklogPublisher) -> ClaimStepResult: ...
def shipped_step(source: ClaimSource, backlog: BacklogStore,
                 publisher: BacklogPublisher, prs: Sequence[PrNumber]
                 ) -> tuple[ClaimStepResult, tuple[ShippedOutcome, ...]]: ...

# nightloop/claims/git_publisher.py   (edge: the only new module that runs git)
class GitBacklogPublisher:
    def __init__(self, backlog: BacklogFile, *, main="main", remote="origin",
                 git="git", timeout_s=GIT_TIMEOUT_S) -> None: ...
    # ready(): fetch <remote>, HEAD is <main>, rev-list --count main..<remote>/main == 0
    # publish(): git add -- <rel>; git commit -m ... -- <rel>; git push <remote> main

# nightloop/cycle/night.py   (one added keyword, default keeps old behaviour)
def run_night(..., between_cycles: Callable[[int], None] = _no_step) -> NightResult
# Also called once with cycle 0 before the first cycle, or add a separate
# `before_night` param; the builder picks one and records it as D3.
```

The CLI builds `GitClaimSource`, `BacklogFile` and `GitBacklogPublisher` in
`main.py`, injectable like the existing factories. Add a `night_text` line per
claim step.

### Decisions to log (builder appends dated entries; do not edit old ones)

- D1 Skipped, not abort, when main is not ready (W4).
- D2 `ClaimSourceError` pre-night is `Skipped` (W10).
- D3 Hook shape in `run_night` (W7).
- D4 **`reconcile_shipped` and unmerged PRs.** Record the finding above: nightloop
  never merges, so same-night PRs read `UNREADABLE`. Add milestone 7c
  ("pending-PR ledger: remember shipped PR numbers across nights and mark them
  done once merged") to PLAN.md as `not-started`. This is the owner's call, so
  the researcher does not add it here.

### Tests (against layer 2, no model, no GitHub)

Temp repo with a local bare `origin` (reuse `tests/git_support.py` and
`tests/claims_support.py`). Cover W1–W12. Add mutation checks, each failing at
least one test: `git add -A` instead of the pathspec; skipping the fetch; reset
after a failed push; force-push; running the step only before cycle 1; marking a
shipped PR done on `UNREADABLE`; the dry-run executing the step. Purity test:
`step.py` and `publish.py` import no I/O module (extend the existing
`test_*_purity.py` pattern). Run: `cd projects/nightloop && python3 -I -m unittest discover -v`.

## Open questions

- Whether the pre-night `Skipped` (instead of aborting) is the owner's wish.
  Bash aborts at preflight on a dirty main.
- Whether the backlog path should be a config field now or stay a constant.
- Real `gh pr view --json mergeCommit` output is still unverified live (m6 limit).
- 7c (the ledger) needs an owner decision before anyone builds it.
