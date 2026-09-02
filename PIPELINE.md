# The build pipeline

A nightly pipeline that ships N increments: research → build → review →
maintain → auto-merge, repeated `.pipeline/cycles` times, bracketed by a
backlog replenishment check before and after the loop, plus two every-7th-night
observers — a lab-wide health check over the portfolio, and an observer over
the pipeline's own run logs. Each phase is a
headless Claude Code run (`claude -p`) that delegates to one specialist
subagent (except auto-merge, which is deterministic bash). The phases don't
talk to each other directly — they coordinate through this repo's files, which
is how the state actually flows:

```
  BACKLOG.md ──▶ [1] researcher ──▶ research/DATE-slug.md
                                          │
                     research note ──▶ [2] builder ──▶ examples/<name>/  (+ BACKLOG update)
                                                              │
                            working-tree diff ──▶ [3] reviewer ──▶ logs/last-review.md (PASS/FAIL)
                                                                          │
                                                          run.sh parses the verdict
                                                                          │
                                          PASS verdict ──▶ [4] maintainer ──▶ branch + commit + PR
                                                                                      │
                                                       clean, conflict-free ──▶ [5] auto-merge (run.sh)
```

1. **Researcher** (`agentlab-researcher`) — pulls the top backlog item, does
   real web research, writes a dated note to `research/` with sources (and their
   dates) and a concrete, small build proposal.
2. **Builder** (`agentlab-builder`) — reads the newest research note and builds
   ONE real, runnable increment under `examples/`, with its own README and a
   quick self-test. Updates the backlog.
3. **Reviewer** (`agentlab-reviewer`) — an independent gate: reviews the diff it
   didn't write, runs the increment's tests and lint, scans for stubs, secrets,
   and bugs, and writes a `PASS`/`FAIL` verdict to `logs/last-review.md`. It does
   not fix code — a broken increment gets `FAIL`ed with specifics.
4. **Maintainer** (`agentlab-maintainer`) — runs at all only if `run.sh` itself
   parsed `logs/last-review.md` and found exactly one `VERDICT: PASS` line
   (`.pipeline/verdict.sh`). The subagent is *also* told to read the verdict and
   that instruction stays — but an instruction is not a gate, and the phase that
   commits under your name should not be the phase that decides whether it may.
   The parse fails closed: a missing verdict (the review phase died), a
   malformed one, or a `FAIL` all skip this phase entirely and end the cycle.
   Then, on `PASS`, the maintainer reads the verdict itself. Only on `PASS`
   does it branch, commit **authored as Steve Ling `<steveylingy@gmail.com>`**,
   push, and open a PR. On `FAIL` (or missing verdict) it ships nothing and logs
   why. It never fabricates, backdates, or pads commits, and never overrules a
   FAIL. It never merges — it writes the PR number to `logs/last-pr.txt` and stops.
5. **Auto-merge** (`run.sh`, deterministic bash — not a subagent) — asks GitHub
   whether the PR is a clean, conflict-free merge (`gh pr view --json mergeable`)
   and merges only if so. A real conflict, or GitHub still computing the answer
   after a few retries, leaves the PR open instead.
6. **Health check** (`agentlab-health`) — a separate concern from the cycle
   above: it re-verifies the *whole accumulated portfolio*, not tonight's diff.
   Every example's self-test still passes in a fresh env, every `knowledge/`
   wikilink still resolves, every `BACKLOG.md` `[done #N]` still matches a
   merged PR. Gated to run every 7th calendar day (rerunning every example's
   tests every night doesn't scale with the portfolio's size) via
   `logs/lab-health-*.log` timestamps — no separate schedule state needed.
   The cadence is `HEALTH_CADENCE_DAYS` in `run.sh`, read by both the gate and
   the skip message so the two can't disagree.
   Report-only: findings never block, delay, or otherwise affect phases 1–5,
   and it never fixes anything itself. Report lands in `logs/last-health.md`
   (git-ignored, same handoff pattern as `logs/last-review.md`).
7. **File health findings** (`run.sh`, deterministic bash — not a subagent) —
   parses that report (`.pipeline/health.sh`) and appends each finding to
   `BACKLOG.md` as an unclaimed item under `## Health-check findings`, then
   commits and pushes. The health agent stays observational — it is forbidden
   to touch `BACKLOG.md`, and that rule is correct — but until this step
   existed, nothing ever filed the "future build cycle's job" this doc claims a
   finding becomes, so every finding was written and then dropped. Same split
   as replenishment: the agent produces the report, the script commits the
   consequence. Idempotent, keyed on the finding's subject, so the same rot
   re-reported next week adds nothing; a `[done #N]` fix deliberately does
   *not* suppress, since a recurrence is new information. Runs only after a
   health phase that exited clean — a partial report must not queue findings
   that were never established.
8. **Pipeline observer** (`agentlab-pipeline-observer`) — the sibling of the
   health check, pointed at the machine instead of the portfolio. Phase 6 asks
   "does what we shipped still work"; this asks "did the nights actually run,
   did they ship what they claimed, did the same failure recur unnoticed, did
   the bookkeeping keep up". It reads `logs/run-*.log` and pipeline state and
   reports: aborted and partial runs, abort causes seen more than once, phases
   that exited non-zero, claims stranded after their work merged, calendar days
   with no run log at all, and quarantined strays nobody revisited. Same 7-day
   cadence as the health check, keyed off `logs/lab-pipeline-*.log` timestamps
   (`PIPELINE_OBSERVER_CADENCE_DAYS`), and runs last because it reads what the
   night did. Cheap — everything it needs is on disk or behind `gh`; no venvs,
   no example re-runs. Report lands in `logs/last-pipeline-health.md`.

   Its window is bounded by the last observation rather than a fixed number of
   days, so cost stays proportional to elapsed time rather than to accumulated
   history. It is explicitly told to **exclude the current run's own log** —
   that log has no `=== done ===` line yet, and handing it over would make
   every night report itself as aborted, filing the same false finding forever.
   Why it exists: nothing in the pipeline read its own logs. Every pipeline
   improvement to date (#28, #29, #30, #31) arrived on a human-initiated
   branch, and the reachability probe's false negative sat misdiagnosed as "VPN
   off" across several nights before a human read the logs together.
9. **File pipeline findings** (`run.sh`, deterministic bash — not a subagent) —
   the exact analogue of phase 7, parsing `.pipeline/pipeline_health.sh` and
   filing through the same `backlog_file_health_finding` into the same
   `## Health-check findings` section. Both observers share that section and
   its dedupe namespace deliberately: rot queues behind planned work in one
   place, and the subjects are disjoint in practice (an example directory or a
   wikilink vs. a run log, an abort cause, or a claim line). The observer is
   told to make its subjects self-describing so they read correctly there.

Three gates protect code that carries your name: a mechanical parse of the
review verdict before the maintainer runs at all, the reviewer's own judgment
behind it, and either a mechanical clean-merge check or **you** resolving a
conflict by hand. Nothing reaches `main` without them — conflict resolution is
never automated, since it requires judgment about intent that no part of this
pipeline has. Note what the two mechanical gates have in common: neither asks a
model whether to proceed. One greps a file the reviewer wrote; the other asks
GitHub. The health check is not a gate — it's a separate, non-blocking
observability pass over what's already shipped.

## Cycles per night

`.pipeline/cycles` (an integer, default 1) sets how many increments a night
ships. Cycles run **strictly sequentially**, each cut from a freshly-merged
`main`. That ordering is load-bearing, not incidental:

- **It is what makes conflicts structurally impossible.** Every cycle touches
  the same shared files — `BACKLOG.md`, `knowledge/INDEX.md`, and backlinks
  into existing `knowledge/` notes. Running cycles concurrently would collide
  there. Auto-merge does not help with this: it *declines* conflicts (leaving
  the PR open for a human), it does not resolve them.
- **It is what keeps item selection correct.** Each researcher picks "the
  topmost unclaimed item" and marks it `[researching]` in a working tree no
  other cycle can see. Run in parallel, N researchers would all pick the *same*
  item. Sequential cycles see the previous cycle's claim because it lands on
  `main` as part of that cycle's merged PR.

Between cycles `run.sh` snapshots anything uncommitted to a recovery branch,
returns to a clean current `main`, and scrubs build artifacts — so a failed
cycle costs one slot rather than the night, and never leaks its diff into the
next cycle's PR. A failed cycle does not abort the loop.

Raising this number is only useful while real work is queued, which is what
the replenishment phase below exists to guarantee.

## Backlog replenishment

If `BACKLOG.md` has fewer unclaimed `- [ ] ` items than one night consumes, a
replenishment phase appends new ones (targeting three nights' worth) and
`run.sh` — not the agent — commits and pushes them straight to `main`. Without
this, throughput just drains the backlog and the researcher's empty-backlog
fallback re-picks stale items, which buys churn rather than portfolio. Demo
mode only: `project:<slug>` mode draws work from `projects/<slug>/PLAN.md`
milestones instead.

That check runs **twice a night: once before the cycle loop and once after**.
Before, because a night that starts short leaves its later cycles with nothing
to claim — topping up only afterwards is too late for exactly the cycles that
needed it. After, so tomorrow starts stocked. Each pass reads `BACKLOG.md` off
disk and tops up at most once, so the second is a no-op unless the loop
actually drained it.

Note the literal `- [ ] ` prefix is the contract between `BACKLOG.md` and the
researcher — an item written without it is invisible to the pipeline.

The decision itself (count, target, whether to act) lives in
`.pipeline/backlog.sh`, apart from the `claude -p` call and the `git` push it
triggers, so it can be tested with no network and no API key. Every decision
`run.sh` makes without a model in the loop follows that shape — six
sourceable libs, each doing one thing and doing no I/O beyond the file it is
handed:

| Lib | Decides | Test |
|---|---|---|
| `.pipeline/backlog.sh` | is the queue stocked, is a claim stranded, has this finding been filed | `bash .pipeline/test_backlog.sh` |
| `.pipeline/verdict.sh` | does the review authorise shipping | `bash .pipeline/test_gates.sh` |
| `.pipeline/health.sh` | what did the health check actually find | `bash .pipeline/test_gates.sh` |
| `.pipeline/pipeline_health.sh` | what did the pipeline observer actually find | `bash .pipeline/test_gates.sh` |
| `.pipeline/preflight.sh` | what does a dirty `main` mean for this run | `bash .pipeline/test_gates.sh` |
| `.pipeline/postcondition.sh` | did a phase actually produce the artifact the next one needs | `bash .pipeline/test_gates.sh` |

Run both suites by hand after editing `run.sh` or any lib. They are on-demand
only, like `eval/run_reviewer_eval.sh`: they live outside `examples/`, so the
health check below does not cover them.

## Mode: demo vs. project

`.pipeline/mode` picks the track for every cycle — `demo` (default) works
`BACKLOG.md` as above, one independent increment under `examples/` per cycle;
`project:<slug>` works `projects/<slug>/PLAN.md` instead, building one
milestone at a time into `projects/<slug>/`, which persists and grows across
cycles rather than starting fresh each time. Each subagent reads
`.pipeline/mode` itself as step 0, so this isn't threaded through the phase
prompts. See `projects/README.md` for how to start one.

## Model per phase

`run_phase` takes the model as its first argument (the third is the phase's
postcondition, below); nothing inherits the interactive default from
`~/.claude/settings.json`, so changing that default can't silently re-price the
nightly job.

| Phase | Model | Why |
|---|---|---|
| researcher | sonnet | web search and writing a note |
| builder | **opus** | writes the increment |
| reviewer | **opus** | the gate on code shipping under your name |
| maintainer | sonnet | reads a verdict, runs `git` and `gh` |
| replenish | sonnet | appends checklist lines |
| health | sonnet | runs existing test suites and reports |
| pipeline observer | sonnet | reads run logs and git state, reports |

The two opus phases are the ones holding judgment. The reviewer especially:
it is the only thing standing between a bad increment and a PR with your name
on it, so it is the last phase that should ever be downgraded to save tokens.
An unrecognised model name aborts that phase rather than falling back to a
default, since a silent fallback is exactly how the full-opus bill would
return unnoticed.

## Phase postconditions

A phase's exit code says the process ended, not that it produced anything. On
2026-09-01 the 600s `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS` ceiling killed the
researcher mid-phase, the `claude` CLI still exited 0, and the build phase ran
against a `research/` whose newest note was the previous night's — the cycle
went on to review and ship an increment built from stale input.

So `run_phase` takes a **required** third argument: the name of a function from
`.pipeline/postcondition.sh` that answers "did this phase leave the artifact the
next phase hard-depends on?" It is called only after a clean exit, with the time
the phase *started*, and a non-zero answer fails the phase.

| Phase | Postcondition | Answers |
|---|---|---|
| researcher | `research_note_fresh` | is there a `research/*.md` newer than this phase |
| builder | `increment_built` | is there a file under `examples/` or `projects/` newer than this phase |
| every other phase | `phase_no_postcondition` | nothing downstream hard-depends on this phase's artifact |

Four properties are load-bearing, and each is a test in `test_gates.sh`:

- **STALE is not MISSING.** `research/` was full on the failure night — of the
  *previous* nights' notes. Only "newer than this phase started" catches it;
  "does the directory have anything in it" passes on the exact failure it is for.
  Both are non-zero, but the log says which.
- **The floor is per phase, not per run.** With `CYCLES=2` both cycles' notes
  carry the same date, so a run-start floor would let cycle 1's note satisfy
  cycle 2's gate and the bug would survive its own fix.
- **It fails closed**, like `verdict.sh`: an artifact the postcondition cannot
  confirm is not a licence to advance.
- **The argument is required, and checked.** `phase_no_postcondition` is how a
  phase spells "no gate" explicitly, so a phase added later inherits one by
  decision rather than by omission — the timing/order coupling `CLAUDE.md` ranks
  worst. And a name that is not a defined shell function aborts the phase
  unrun, for the same reason an unrecognised model does: a typo would otherwise
  turn the gate into a silent no-op.

One thing this does not catch. `increment_built` looks under `examples/` and
`projects/`, but an increment that targets the pipeline itself lands in
`.pipeline/` — this change did — and then no file under either root is newer, so
the gate fails a build phase that in fact succeeded. It is not a corner case:
eight of the fifteen merges before this one touched neither root, and one of
those (#26, `cycle/2026-08-12-backlog-replenish-ordering`) was a builder cycle.
The error direction is the safe one — a loud stop, not a silent ship on stale
input — and the log names the postcondition and the state, so the failure is
legible. Widening the roots is deliberately not done here: `run.sh` writes into
`.pipeline/strays` when it stashes an untracked file, so measuring `.pipeline`
would let a run that stashed a stray forge its own freshness, turning the gate
into a no-op on exactly the nights something went wrong. Doing it safely needs
its own increment and its own tests.

The five `phase_no_postcondition` phases are not ungated, they are gated
elsewhere and more narrowly — the reviewer by `review_verdict` on a
`logs/last-review.md` that was `rm -f`d beforehand, replenish by a
`git status --porcelain BACKLOG.md` check on the next line, health and the
observer by reading their own reports on the success branch only, the maintainer
by the auto-merge gate re-reading git and the PR. Each call site says so in a
comment; duplicating those as freshness checks would give one fact two
enforcement points to drift apart at.

## Tools available to the pipeline

Beyond the built-in file/search/web tools, the researcher also has the
`hn-search` MCP server (`examples/mcp-hn-search/`, registered in `.mcp.json`)
for practitioner discussion/reception, not just vendor docs.

## Running it

- Manually (recommended first, to shake out PATH/auth): `bash .pipeline/run.sh`
- On a schedule: the launchd job `com.steeb.agentlab.daily` runs `run.sh` daily
  at 02:47. See the repo setup notes for how to load/unload it. It runs
  overnight deliberately — a job this long sharing a rolling 5-hour usage
  window with interactive daytime work is what makes both feel starved.
  launchd runs a missed job on next wake, so a Mac asleep at 02:47 will start
  the run whenever the lid next opens; `pmset repeat wakeorpoweron` keeps that
  from landing in the middle of a workday.
- Budget roughly 20–25 min per cycle, plus ~12 min when the health check runs
  and a few minutes more when the pipeline observer runs alongside it.

Logs land in `logs/` (git-ignored).

### What a dirty `main` does to the night

Before any phase runs, the preflight classifies the working tree
(`worktree_disposition` in `.pipeline/preflight.sh`) and the two dirty outcomes
are treated as opposites:

- **A modified tracked file aborts the run.** The per-cycle and postflight
  snapshots already rescue a failed cycle, so a tracked edit surviving to the
  next night means something outside the pipeline changed the repo. That wants
  a human, not a guess.
- **Untracked-only dirt does not.** Strays are moved to
  `.pipeline/strays/<timestamp>/` (git-ignored, never deleted, never committed)
  and the night proceeds. Aborting over a leftover scratch file costs a full
  cycle and protects nothing.

The second case is why this split exists: on 2026-08-14 a single untracked
`.claude/settings.local.json` ended the run three seconds in. If a file you
wanted goes missing from the repo root, look in `.pipeline/strays/` before
assuming the pipeline ate it.

### If the log says NETWORK UNREACHABLE

`check_reachable` sends a HEAD bounded by `--connect-timeout`, so it fails only
when DNS, TCP or TLS fails — a genuinely absent network, usually the VPN.

Trust that message only from the current implementation. Until 2026-08-15 the
check was a full GET under `--max-time 5`, which bounded the whole transfer
rather than the connection, so it also fired when the link merely ran slow:
github.com's homepage is ~574KB, and the run that night died with `curl: (28)
... with 435800 bytes received` — connected, mid-download, reported as
unreachable. Nights logged as VPN-off before that date (2026-08-02/03/04) may
have been this instead.
