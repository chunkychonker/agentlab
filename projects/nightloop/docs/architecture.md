# Architecture: a night, its gates, and why each gate exists

This page explains how nightloop works and why it is built this way. For
the steps to run it, see the [quickstart](quickstart.md). For every flag,
see `nightloop <command> --help`.

The short version: a model does the work in each phase, and plain Python
decides what happens next. Each decision is a gate. A gate is a small
function that can be tested without a model, and each one was added after a
real night went wrong in the bash pipeline nightloop was ported from
([`PIPELINE.md`](../../../PIPELINE.md)). Where no such night is on record,
the gate's section says "no recorded incident" and gives the reason it
exists instead.

## A night, step by step

`nightloop run` does this, in this order. The order is fixed in code
(`nightloop/cli/main.py`, `nightloop/cycle/night.py` and
`nightloop/cycle/cycle.py`), not left to a caller.

1. **Run window.** Read the clock once. Outside the config's window, refuse
   and exit 3. ([gate](#gate-run-window))
2. **Claim step, before cycle 1.** Mark any claim a failed earlier cycle
   left behind on a recovery branch as stranded, and commit and push only
   the backlog. ([gate](#gate-stranded-claim-reconcile))
3. For each of up to `cycles` cycles:
   1. **Clear the handoff.** Delete the old verdict file.
      ([gate](#gate-clear-the-handoff))
   2. **Research**, then **build**, then **review**, each one a
      `claude -p --agent <agent> --permission-mode dontAsk --allowedTools ...`
      process with a timeout and one metrics line. After each, its gate
      must pass, or the cycle ends there:
      [freshness](#gate-phase-artifact-freshness) for research and build,
      the [verdict parse](#gate-fail-closed-verdict-parse) for review. A
      [usage limit](#gate-usage-limit-stop) in any phase ends the night
      after this cycle.
   3. **Ship**, only on a review PASS: push a fresh branch, open the PR
      with the [review record](#gate-review-record-and-secret-scan) in its
      body, and report it shipped only if GitHub says it
      [merges cleanly](#gate-clean-merge-check).
   4. **Recover**, always, also after a crash: save any leftovers to a
      unique branch and put `main` back. ([gate](#gate-recovery-to-a-unique-branch))
   5. **Claim step**, as in step 2.
4. **Shipped step, after the night.** For each PR shipped tonight, mark its
   claim done if it has already merged.
   ([gate](#gate-shipped-claim-reconcile))

The phases themselves run the plugin's agents (`plugin/agents/`), and the
plugin's [publish guard](#gate-publish-guard) blocks `git push` and GitHub
writes inside every phase. Publishing is the shipper's job, and the shipper
is not a model.

## How the code is laid out

nightloop is a functional core with an imperative shell. The decisions
(window, verdict, freshness, plan, claim edits, mergeable, review record)
are pure functions in their own modules, and purity tests (`test_*_purity.py`)
check that those modules import no I/O. Effects (the `claude` process,
git, `gh`, files, the clock) sit behind small ports and are passed in, so
every test drives the real decisions with fakes. `nightloop/cli/main.py` is
the only module that reads argv and the clock and builds the real runner,
workspace, shipper and claim ports.

| Package | What it holds |
|---|---|
| `nightloop/config.py`, `nightloop/model.py` | The `nightloop.toml` schema and its validated types |
| `nightloop/gates/` | Verdict parse and freshness checks |
| `nightloop/phase/` | One phase: argv, the subprocess runner, the CLI's JSON, metrics |
| `nightloop/cycle/` | The cycle and the night, recovery, usage limits |
| `nightloop/ship/` | The real shipper: push, PR, review record, mergeable poll |
| `nightloop/claims/` | Backlog claim reconcile and publish |
| `nightloop/cli/` | The `nightloop` command and its text output |
| `nightloop/schedule/`, `nightloop/schedule_gha/` | launchd and GitHub Actions schedulers |
| `nightloop/bench/` | The reviewer benchmark |
| `plugin/` | The Claude Code plugin: agents, publish guard, `run_silent` |

Every gate below has the same four fields. Module and Tests paths are
relative to `projects/nightloop/`. Source links point at this repo's own
records. `tests/test_docs.py` checks that every module, test file and source
named here exists.

## Gate: Run window

What it checks: the local hour when `run` starts is inside
`[window_start_hour, window_end_hour)`. On failure: `run` prints the reason
and exits 3 without building a runner. A clock that cannot be read also
refuses, even with `--ignore-window`.

- **Module:** `nightloop/cli/window.py`
- **Tests:** `tests/test_cli_window.py`
- **Incident:** between 2026-08-29 and 2026-09-03, six runs in a row of
  the bash pipeline started in the 11:47 and 13:47 hours instead of
  overnight. Each one spent the usage window that daytime work needed. The
  cause was never proven; the window does not depend on it.
- **Source:** [`PIPELINE.md`, Running it](../../../PIPELINE.md#running-it)

## Gate: Stranded-claim reconcile

What it checks: before cycle 1 and after each cycle, every
`cycle/*-unshipped-*` recovery branch is read for the backlog claim it
carries, and that item on `main` is rewritten to `[stranded <branch>]`. On
failure: a `main` that is not current, a fetch that fails or a backlog with
local edits makes the step `skipped`. The night goes on, and the next step
tries again.

- **Module:** `nightloop/claims/reconcile.py`, `nightloop/claims/step.py`
- **Tests:** `tests/test_claims_reconcile.py`, `tests/test_claims_step.py`
- **Incident:** on 2026-08-12, cycle 1 researched and built an item, then
  the maintain phase died on an API error. Recovery moved the work to a
  branch, and the backlog item read `[ ]` again, so the next cycle would
  have built it a second time.
- **Source:** [`knowledge/pipeline-claim-lifecycle.md`](../../../knowledge/pipeline-claim-lifecycle.md#failure-1--a-failed-cycle-silently-releases-its-claim)

## Gate: Clear the handoff

What it checks: nothing. It acts: at the start of every cycle, before any
phase runs, the verdict file (`logs/last-review.md`) is deleted. The
workspace deletes the same path the review gate reads, so the two cannot
differ. On failure: a verdict that exists but cannot be deleted raises,
and the cycle stops after recovery.

- **Module:** `nightloop/cycle/cycle.py`, `nightloop/cycle/git_workspace.py`
- **Tests:** `tests/test_cycle_night.py`, `tests/test_cycle_git_workspace.py`
- **Incident:** no recorded incident

Why it exists anyway: if a reviewer dies without writing, the previous
cycle's `PASS` would still be in the file and would approve unreviewed
work. The bash pipeline added the clear on 2026-08-11 as a risk, not after
a failure ([`CASE_STUDY.md`](../../../CASE_STUDY.md#what-broke-and-what-changed-because-of-it)
lists it as "the risk").

## Gate: Phase artifact freshness

What it checks: after research, a `research/*.md` file modified after the
phase started (`research_note_fresh`); after build, any file under
`examples/` or `projects/` modified after the phase started
(`increment_built`). The floor is the phase's own start time minus one
second, so an earlier cycle's note cannot pass a later cycle's gate. On
failure: `STALE` (files exist, none new) or `MISSING`, and the cycle ends.

These directories are hard-wired (`nightloop/gates/constants.py`), copied
from agentlab's layout. A repo with a different layout cannot use these two
postconditions yet. It can set `postcondition = "none"` for research and
build, and lose the gate.

- **Module:** `nightloop/gates/freshness.py`, `nightloop/gates/fs.py`, `nightloop/gates/constants.py`
- **Tests:** `tests/test_gates_freshness.py`
- **Incident:** on 2026-09-01, a 600 s background-task limit killed the
  researcher mid-phase, and the CLI still exited 0. The build phase then ran
  on the previous night's note, and the cycle went on to review an
  increment built from stale input. "Is there a note?" would have passed,
  because `research/` was full of old ones.
- **Source:** [`PIPELINE.md`, Phase postconditions](../../../PIPELINE.md#phase-postconditions),
  [`CASE_STUDY.md`](../../../CASE_STUDY.md#what-broke-and-what-changed-because-of-it)

## Gate: Fail-closed verdict parse

What it checks: exactly one line of the verdict file starts with
`VERDICT:` at column 0, and the word after it is exactly `PASS` or `FAIL`.
Only `PASS` advances. On failure:
`FAIL`, `MISSING` (no file) or `MALFORMED` (no such line, two of them, or a
word like `PASSED`) all end the cycle without shipping.

- **Module:** `nightloop/gates/verdict.py`
- **Tests:** `tests/test_gates_verdict.py`
- **Incident:** no recorded incident

Why it exists anyway: until 2026-08-14 the only thing that stopped the bash
pipeline shipping a failed review was an instruction in the maintainer's
prompt. The phase that commits was also the phase deciding whether it may
([`PIPELINE.md`](../../../PIPELINE.md), step 4). The parse moved that
decision into code. Later nights did show the case it exists for: on
2026-09-13 both cycles read the verdict as `MISSING` after the researcher
was killed ([`BACKLOG.md`](../../../BACKLOG.md#health-check-findings)), and
nothing shipped.

## Gate: Usage-limit stop

What it checks: a phase that ends as a CLI error is scanned for Claude's
usage-limit text (`hit your session limit` and similar), including a run
that exits 0 with `is_error: true`. On failure, which here means a match:
that cycle recovers, the night stops, and `run` exits 4 with the reset time.

- **Module:** `nightloop/cycle/usage_limit.py`
- **Tests:** `tests/test_cycle_usage_limit.py`
- **Incident:** "You've hit your session limit" ended phases on eight
  nights between 2026-09-03 and 2026-09-13. Every phase type was hit at
  least once, and on most of those nights both cycles were lost.
- **Source:** [`BACKLOG.md`, health-check findings](../../../BACKLOG.md#health-check-findings),
  [`knowledge/cycle-orchestration-gates.md`](../../../knowledge/cycle-orchestration-gates.md)

## Gate: Review record and secret scan

What it checks: before the PR is opened, the reviewer's verdict, `Ran:`
lines and `Findings:` bullets are built into an "Independent review"
section of the PR body. The text is scanned for credential patterns
(`sk-ant-...`, GitHub tokens, AWS key ids, private keys). After creation,
the body is read back and checked for the section's marker. On failure:
no `Findings:` line, a credential match, no room in the body, or a missing
marker is `NotShipped`.

- **Module:** `nightloop/ship/review_section.py`
- **Tests:** `tests/test_ship_review_section.py`, `tests/test_ship_gh_shipper.py`
- **Incident:** before 2026-11-08, the review findings lived only in the
  git-ignored verdict file, which each cycle overwrites. PRs merged with
  warnings that nobody could read afterwards.
- **Source:** [`PIPELINE.md`, The review record on the PR](../../../PIPELINE.md#the-review-record-on-the-pr)

## Gate: Clean-merge check

What it checks: after the PR exists, GitHub's `mergeable` value, read up
to 5 times, 2 s apart, while it is `UNKNOWN`. Only `MERGEABLE` is
`Shipped`. On failure: `CONFLICTING`, an `UNKNOWN` that never settles, or
any other value is `NotShipped`, and the PR stays open. nightloop never
merges, so this check only decides what the night reports.

- **Module:** `nightloop/ship/mergeable.py`
- **Tests:** `tests/test_ship_values.py`, `tests/test_ship_gh_shipper.py`
- **Incident:** on 2026-08-10, two PRs (#19 and #20) edited the same file
  and conflicted.
- **Source:** [`CASE_STUDY.md`](../../../CASE_STUDY.md#what-broke-and-what-changed-because-of-it)

## Gate: Recovery to a unique branch

What it checks: after every cycle, whatever happened, whether the tree has
uncommitted changes or local `main` has commits that `origin/main` lacks.
If either is true, the leftovers go to one new branch,
`cycle/<date>-unshipped-<HHMMSS>-<cycle>`, which is pushed. Only after the
push is `main` moved back to `origin/main`. When there are only uncommitted
changes and the push fails, the branch stays local and nothing is lost. On
failure: a push that fails while `main` is ahead,
a missing `origin/main` ref, or commits that moving `main` would orphan
raise `RecoveryError` and stop the night with `main` untouched. Work is
never discarded.

- **Module:** `nightloop/cycle/git_workspace.py`
- **Tests:** `tests/test_cycle_git_workspace.py`
- **Incident:** on 2026-11-12, the review of milestone 4 (PR #89) found
  that recovery treated only uncommitted changes as leftovers. Commits left
  on local `main` would have carried into the next cycle and been pushed
  with its PR. The bash pipeline has the same gap. It was fixed in
  milestone 5.
- **Source:** [`PLAN.md`, decisions log, 2026-11-12](../PLAN.md#decisions-log)

## Gate: Shipped-claim reconcile

What it checks: after the night, for each PR shipped tonight, whether it
has merged, and if so it rewrites that claim from `[building]` to
`[done #N]`. On failure: a PR that has not merged reads as pending and
nothing is marked. Because nightloop never merges, tonight's PRs are always
pending today. A ledger that remembers them for a later night is milestone
7c, not built.

- **Module:** `nightloop/claims/reconcile.py`, `nightloop/claims/step.py`
- **Tests:** `tests/test_claims_reconcile.py`, `tests/test_cli_claims.py`
- **Incident:** PRs #33 (2026-08-16), #37 and #38 (2026-09-02) and #41
  (2026-09-09) merged and left their backlog items `[building]`. Each was
  corrected by hand until 2026-09-10.
- **Source:** [`knowledge/pipeline-claim-lifecycle.md`](../../../knowledge/pipeline-claim-lifecycle.md#failure-3--a-shipped-claim-is-never-marked-done-n)

## Gate: Publish guard

What it checks: before every Bash call in a phase where the plugin is
loaded, the command text. It blocks `git push` in any form it can parse
(through `rtk`, `env`, `sudo`, chains, `$( )`, nested `bash -c`) and every
`gh` command that is not read-only. On failure: the hook exits 2 and the
call is refused. Anything it cannot read is also refused. It matches text,
so it is a backstop, not a security boundary.

- **Module:** `plugin/hooks/publish_guard.py`
- **Tests:** `tests/test_plugin_guard.py`
- **Incident:** on 2026-11-08, in testing, a deny rule for plain `git push`
  was bypassed because the RTK hook rewrites the command to `rtk git push`
  before permissions are checked. The guard sees through the `rtk` prefix
  for that reason.
- **Source:** [`PIPELINE.md`, Permissions per phase](../../../PIPELINE.md#permissions-per-phase)

## Not in nightloop yet

The bash pipeline has these and nightloop does not. Do not assume them.

- **Verify gate.** agentlab runs the increment's tests itself between
  build and review, with no model involved
  ([`PIPELINE.md`](../../../PIPELINE.md#the-verify-gate)). nightloop relies
  on the reviewer running them.
- **Fix round.** agentlab gives the builder one more go after a failed
  review ([`PIPELINE.md`](../../../PIPELINE.md#the-fix-round)). In nightloop
  a failed review ends the cycle.
- **Spec gate.** agentlab checks that the verdict marks every acceptance
  criterion of the research note `MET` or `CUT`. nightloop parses only the
  `VERDICT:` line.
- **CI wait and auto-merge.** nightloop never merges and does not wait for
  CI ([`PIPELINE.md`](../../../PIPELINE.md#ci-and-the-merge-gate)).
- **The pending-PR ledger** (milestone 7c), so the
  [shipped-claim reconcile](#gate-shipped-claim-reconcile) never marks a PR
  done on its own.
- **`--plugin-dir` in the phase argv.** The plugin's agents and guard must
  be enabled for `claude` some other way ([quickstart step 6](quickstart.md#6-make-the-agents-resolve-human-step)).
- **A live run.** None of the gates above has run in a real nightloop
  night. Each one is tested offline only ([status](../README.md#status)).
