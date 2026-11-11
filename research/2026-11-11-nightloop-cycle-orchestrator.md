# nightloop milestone 4: cycle orchestrator

Mode: project:nightloop. Milestone 4 ("Cycle orchestrator"), the topmost
not-started one; 1-3 are done (#78, #83, #88). `logs/research-context.md` shows
no open PRs. The work extends `projects/nightloop/` and proposes no `examples/`
directory, so no name can collide. Read first: `PLAN.md`, `phase/execute.py`,
`phase/status.py`, `model.py`, and agentlab's `.pipeline/run.sh` (`run_cycle`
805-896, `snapshot_dirty_main` 584-620, the night loop 1329-1362) and
`.pipeline/usage_limit.sh`.

## Question

What is the smallest orchestrator that runs research, build and review with
their gates, snapshots a failed cycle to a recovery branch so it cannot leak into
the next, stops the night on a usage limit, and can be tested end to end with
the fake runner and no model?

## Findings

**What bash does today** (the behaviour to port, from `run.sh`):

- Per cycle: delete the handoff files (`last-review.md`, `last-pr.txt`) before
  anything runs. A stale PASS from the previous cycle must never authorise this
  cycle (run.sh:808-812). Then research (gated), build (gated), review. The review
  phase is ungated by artifact, but its verdict is parsed mechanically and only
  PASS goes on (run.sh:829-850). The first failing phase ends the cycle.
- After EVERY cycle, pass or fail, `snapshot_dirty_main`: if the tree is dirty,
  create `cycle/<date>-unshipped-<HHMMSS>-<seq>`, `git add -A`, commit, push, then
  switch back to main. The `seq` counter is there because two fast-failing cycles
  can land in the same second and the second `switch -c` would collide. Work is
  never discarded: if the commit fails, main is left dirty and the next run's
  preflight aborts. A branch created but not committed is deleted, and only
  when this call created it (run.sh:584-620, and the comment at 613-619).
  Then `reset_to_clean_main` (switch to main, pull `--ff-only`).
- A phase that stopped on a usage limit returns 75 (`USAGE_LIMIT_RC`) and the
  night loop stops after cleanup, because every later phase would hit the same
  limit (run.sh:1338-1340, 1358). The limit is recognised by a regex over that
  phase's own output only, `hit your (session|usage|weekly) limit|usage limit
  reached`, case-insensitive, taking the last matching line and the text after
  `resets` (`usage_limit.sh`). It is checked only when the CLI exited non-zero.
- Not in nightloop's three-phase config: maintain, auto-merge, claim marking
  (m5), replenish, health, observer.

**The usage-limit check needs one amendment.** Web search (2026-11-11) found an
open bug report, "headless claude -p API errors (e.g. rate limit) return subtype
success, exit code 0" (<https://claudeissues.com/issue/79500-bug-headless-claude-p-api-errors-e-g-rate-limit-return-subtype-success-exit-code>,
undated in the snippet; I did not fetch it, so treat it as unconfirmed). It says
the JSON can carry `is_error: true`, `subtype: "success"` and the error text only
in `result`, with exit 0. m3 already maps `is_error: true` to
`CLI_ERROR` whatever the exit code, so the limit check should look at the
result text for any `CLI_ERROR` outcome, not only rc != 0 as bash does. The headless
docs (<https://code.claude.com/docs/en/headless.md>, via search) list an `error`
field with values such as `rate_limit` and `billing_error`; whether a plan usage
limit maps to `rate_limit` is NOT confirmed, so match on text as bash does and do
not depend on that field. A second reported bug shows a false "session limit"
message at 11% usage (<https://claudeissues.com/issue/86272-headless-claude-p-reports-youve-hit-your-session-limit-while-the-account-is-at-1>).
Cost of a false positive: the night stops early (fail-safe, not fail-open).

**What m3 leaves the orchestrator.** `PhaseOutcome` keeps `status`, the
`GateResult`, and `record.cli` (a `CliResult` or `None`). It drops stdout and
stderr. A non-zero exit with non-JSON stdout (the 2026-10-19 limit case) leaves
`cli = None`, so the orchestrator cannot see the limit text from the outcome. To
avoid touching `phase/` (expand/contract), the orchestrator wraps the `Runner`
in a small recording decorator that remembers the last `RawRun`. The limit
check reads that. Records also lack `run_ts` and `cycle` (m3 "known limits"); the
orchestrator can add both by wrapping `metrics_sink` and re-emitting the line
with two extra keys.

**Design choices to settle.**

- Seams (CLAUDE.md section 3): the core defines two ports and the edge implements them.
  `Workspace` covers everything git: is it dirty, clear the handoff files, snapshot
  to a recovery branch, return to a clean main. `Shipper` opens the PR after PASS.
  The core imports no I/O module.
- The config has no `maintain` phase, so "PR opened" is not a model phase here.
  Propose the `Shipper` port returning `Shipped(pr: int) | NotShipped(reason)`.
  The real shipper (git push, `gh pr create`, review record, mergeable check) is
  NOT this milestone. m4 ships a `FakeShipper` and a documented gap. A cycle that
  passes review and is shipped is `SHIPPED`; one that passes review but is
  `NotShipped` is a failure whose work still goes to a recovery branch.
- Illegal states: `CycleResult` as a sum type, not `ok: bool` plus a nullable
  reason. A cycle ends in exactly one of `Shipped(pr)`, `Failed(phase, status)`,
  `ReviewRefused(verdict)`, `ShipRefused(reason)`, `UsageLimited(reset)`. The
  night stops iff the last result is `UsageLimited` or recovery failed.
- Recovery failure is not a cycle failure. If the snapshot cannot be committed,
  work would be lost or leak into the next cycle, so the night raises
  `RecoveryError` and stops (bash's equivalent is the next run aborting at
  preflight; here it stops sooner and says why).

## Build proposal

Extends `projects/nightloop/` with a new package `nightloop/cycle/`. Contradicts
no decisions-log entry: stdlib only, no real model in tests, `.pipeline/` is not
touched, and it uses m3's `run_phase` and m2's `evaluate` as given. No change to
`phase/` or `gates/`.

**Layer 1, intent.** Run a night of up to `cfg.cycles` cycles. Each cycle is
research, build, review with their gates, then a ship step on PASS. After every
cycle the workspace is back on a clean main, with any unshipped work on exactly one
recovery branch. A usage limit ends the night. Out of scope: the real git/`gh`
shipper, claim marking (m5), CLI and scheduling (m6, m8, m9), the run window check
(m6's `run` decides whether to start), replenish, health and observer phases,
auto-merge, retries within a cycle, parallel cycles.

**Layer 2, behaviour.** Inputs: a `Config`, a `Runner`, a `Workspace`, a `Shipper`,
a clock, a metrics sink, and a run id string. Output: a `NightResult` with one
`CycleResult` per cycle that ran, in order.

Invariants:

1. Before each cycle's research starts, `workspace.clear_handoff()` has run, so
   the verdict file does not exist. A review phase that writes nothing is MISSING.
2. Phases run in the order research, build, review, and the first one whose
   `PhaseStatus` is not `GATED_PASS` ends the cycle. Later phases do not run.
3. Review runs with the `review_verdict` gate from config. The ship step runs
   only if the review phase is `GATED_PASS`. FAIL, MISSING and MALFORMED never
   reach `Shipper`.
4. After every cycle, whatever the result, `workspace.recover(cycle_id)` runs
   once. A dirty tree produces exactly one recovery branch. A clean tree
   produces none. A `Shipped` cycle leaves the tree clean, so none.
5. Recovery branch names are unique within a night even when two cycles fail in
   the same second (a sequence number, as bash does).
6. If `recover` raises `RecoveryError`, the night stops and the exception
   propagates. Later cycles do not start.
7. A usage limit stops the night after that cycle's recovery. The limit is found
   from the phase's last `RawRun` (stdout and stderr) for any status that is
   `CLI_ERROR`, using the bash regex, case-insensitive, last matching line, reset
   text after `resets` or `"unknown"`. `GATED_PASS` output is never scanned, so a
   note that quotes the phrase cannot stop the night.
8. Every phase run emits one metrics line, with `run_ts` and `cycle` added. The
   orchestrator emits no line of its own.
9. A cycle never starts if the cycle count has been reached, so `len(results) <=
   cfg.cycles`.

Failure modes: `run_phase` exceptions (unstartable CLI, bad gate) propagate, but
`recover` still runs first (`try/finally`), so a crash does not strand work. If
`recover` also raises, the `RecoveryError` is raised with the first exception
chained. `Shipper` raising propagates the same way, after `recover`.

**Layer 3, interfaces** (no bodies):

```python
# nightloop/cycle/ports.py
class Workspace(Protocol):
    def clear_handoff(self) -> None: ...
    def recover(self, run_id: str, cycle: int) -> Recovery: ...   # raises RecoveryError

@dataclass(frozen=True)
class Recovery:                       # exactly one of the two
    branch: str | None                # None = tree was clean
    pushed: bool                      # False when branch is None or the push failed

class Shipper(Protocol):
    def ship(self, cycle: int) -> Shipped | NotShipped: ...

# nightloop/cycle/result.py  (sum type; one frozen dataclass per case)
CycleResult = Shipped | PhaseFailed | ReviewRefused | ShipRefused | UsageLimited
@dataclass(frozen=True)
class PhaseFailed: phase: str; status: PhaseStatus
@dataclass(frozen=True)
class ReviewRefused: outcome: Verdict
@dataclass(frozen=True)
class UsageLimited: phase: str; reset: str

# nightloop/cycle/usage_limit.py   (pure)
def usage_limit_reset(text: str) -> str | None: ...

# nightloop/cycle/cycle.py
def run_cycle(cfg, cycle, *, runner, gate, workspace, shipper, now, repo,
              verdict_path, sink, run_id) -> CycleReport: ...   # CycleReport = result + Recovery
# nightloop/cycle/night.py
def run_night(cfg, *, ...same ports...) -> NightResult: ...
# nightloop/cycle/git_workspace.py  (the only module that runs git)
class GitWorkspace: ...   # implements Workspace
```

The prompt text for each phase is a named constant per phase in one module,
`cycle/prompts.py`, not inline (magic-values rule).

**Layer 4/5 hints.** `GitWorkspace` is tested against a real temporary git repo
with a local bare repo as `origin`: no network, no `gh`. A dirty failed cycle
becomes a pushed branch that holds the file and leaves main clean and equal to
`origin/main`. `git switch main` and `pull --ff-only` as in bash. `git add -A`
respects `.gitignore`, so ignored build output is not committed.

**Acceptance criteria** (each a test; none calls `claude`):

- B1. All three phases pass and review returns PASS: `FakeShipper` is called once,
  result `Shipped(pr)`, no recovery branch.
- B2. Research gate STALE: result `PhaseFailed("research", GATE_FAILED)`; build
  and review runners are never invoked (assert on `FakeRunner.requests`).
- B3. Review FAIL, MISSING and MALFORMED each give `ReviewRefused` with that
  verdict, and `Shipper` is never called. MISSING is produced by leaving no verdict
  file, not by a mock.
- B4. A stale PASS from a previous cycle sitting in the verdict file does not
  authorise cycle 2: `clear_handoff` ran first, so a review that writes nothing
  is MISSING.
- B5. A failed cycle that left a file in the tree yields one recovery branch
  containing the file, the tree is clean, and the next cycle starts clean. Two
  cycles failing in the same fake second get different branch names.
- B6. A clean-tree failure creates no branch.
- B7. `recover` raising `RecoveryError` stops the night: cycle 2 never starts.
- B8. A usage-limit message in a non-zero-exit run, in a rc-0 `is_error: true` run,
  and in non-JSON stdout each give `UsageLimited` with the reset text; the night
  stops after that cycle's recovery, cycle 2 never runs. The same text inside
  a `GATED_PASS` result does not stop the night.
- B9. The pure `usage_limit_reset` matches the bash cases: session / usage /
  weekly wording, mixed case, the last matching line wins, `"unknown"` with no
  `resets`, `None` with no match.
- B10. Metrics lines carry `run_ts` and `cycle`, one per phase run, and every
  prior field is unchanged.
- B11. A `run_phase` exception still triggers `recover` (spy), then propagates.
- B12. Purity: `nightloop/cycle/` modules other than `git_workspace.py` import
  none of `subprocess`, `os`, `socket`, `urllib`, matching m3's
  `test_phase_purity.py`.
- Full suite: `cd projects/nightloop && python3 -I -m unittest discover -v`
  passes alongside the existing 164.

PLAN update for the builder: decisions-log entry for the ship-step port (the
config has no maintain phase) and for scanning limit text on every `CLI_ERROR`,
not only non-zero exit.

## Open questions

- The rc-0 rate-limit bug (issue 79500) was only seen in a search snippet, not
  fetched, and the CLI may have fixed it. The design works either way; confirm
  once against a real run (a human smoke step).
- Whether a plan usage limit appears under the JSON `error` field as
  `rate_limit`. Unconfirmed; text matching is used instead.
- Whether the real shipper belongs to m4's follow-up, m5 (claims) or m7 (the
  maintain agent in the plugin). The plan is silent. Recommend a later decision;
  this note leaves a documented port.
- Pushing recovery branches needs an `origin`; `Recovery.pushed` records a
  failed push as non-fatal, as bash does ("work is still safe locally").
