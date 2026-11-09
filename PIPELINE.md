# The build pipeline

A nightly pipeline that ships N increments: research → build → review →
maintain → auto-merge, repeated `.pipeline/cycles` times, bracketed by a
backlog replenishment check before and after the loop, plus two every-7th-night
observers — a lab-wide health check over the portfolio, and an observer over
the pipeline's own run logs. Each phase is a
headless Claude Code run (`claude -p --agent agentlab-<phase>`) of one
specialist agent, with only the tools that phase needs (see "Permissions per
phase"); auto-merge is deterministic bash. The phases don't
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
5. **Auto-merge** (`run.sh`, deterministic bash — not a subagent) — first adds
   the reviewer's findings to the PR body (see "The review record on the PR";
   if that fails the PR stays open), then asks GitHub
   whether the PR is a clean, conflict-free merge (`gh pr view --json mergeable`)
   and merges only if so. A real conflict, or GitHub still computing the answer
   after a few retries, leaves the PR open instead. On a merge it then closes
   the claim's last arrow: `reconcile_shipped_claim` reads the item out of the
   merge commit's diff and rewrites `- [building] …` to `- [done #N] …` on main
   (`backlog_mark_done`), so a shipped increment stops reading as in-progress
   before the next cycle's researcher opens `BACKLOG.md`. Until 2026-09-10
   nothing did this and it was corrected by hand — see
   `knowledge/pipeline-claim-lifecycle.md`.
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
   that were never established. When `.pipeline/file-issues` says `on`, each
   finding is *also* filed as a GitHub issue (see "Findings as GitHub issues").
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
   Issues, when switched on, carry the `pipeline` label instead of `health`.

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
`run.sh` makes without a model in the loop follows that shape — small
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
| `.pipeline/auth.sh` | which login the night uses, and did the auth probe fail on it | `bash .pipeline/test_auth_alert.sh` |
| `.pipeline/alert.sh` | does the finished night need a human's attention | `bash .pipeline/test_auth_alert.sh` |
| `.pipeline/runner.sh` | what launchd job runs the night from the runner clone | `bash .pipeline/test_runner.sh` |
| `.pipeline/metrics.sh` | what one phase's JSON output says it cost, and the per-phase medians | `bash .pipeline/test_metrics.sh` |
| `.pipeline/phase_tools.sh` | which tools each phase may use | `bash .pipeline/test_phase_tools.sh` |
| `.pipeline/issues.sh` | which GitHub issue a finding belongs to, and whether to open one or comment | `bash .pipeline/test_issues.sh` |
| `.pipeline/migrate_backlog_findings.sh` | which backlog lines are unclaimed findings, and which label each gets (a one-off you run by hand) | `bash .pipeline/test_migrate_backlog_findings.sh` |
| `.pipeline/review_record.sh` | what the reviewer's findings look like in the PR body, and is that text safe to post | `bash .pipeline/test_review_record.sh` |

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

`run_phase` takes the model as its first argument (the second is the phase's
permission key, below; the fourth is its postcondition); nothing inherits the interactive default from
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

## Phase metrics

Every phase that reaches the CLI appends one JSON line to `logs/metrics.jsonl`:
when it ran (`run_ts`, `cycle`, `phase`, `model`), how it ended (`rc` —
0, 1, or 75 for a usage limit — and `postcondition_state`, null when the CLI
failed before the gate ran), and what it used (`duration_ms`, `num_turns`,
`tokens.{input,output,cache_read,cache_creation}`, `total_cost_usd`,
`session_id`, `is_error`), and how many tool calls its permission rules
refused (`denials`, see "Permissions per phase"). `run_phase` gets these by running the CLI with
`--output-format json`; only the answer text (`.result`) goes into the run log,
so the log reads exactly as before.

`total_cost_usd` is the CLI's API-price estimate. On a subscription nobody is
billed that amount; it is still a fair relative measure of which phase is
heavy.

Metrics never change a phase's result. If the CLI's output is not JSON, the
raw output goes into the log (so the usage-limit check still sees it) and the
line is written with `parse_error: true` and every CLI field null. If the line
cannot be written at all, the log gets a `WARN: no metrics line` and the night
goes on. `--max-budget-usd` is deliberately not used: a spending cap would end
phases early, which is exactly the outcome change metrics must not cause.

To read it:

```bash
bash ~/agentlab-runner/.pipeline/metrics_report.sh      # per-phase medians, last 7 nights
bash ~/agentlab-runner/.pipeline/metrics_report.sh 30   # ...last 30
```

The nights run in the runner clone, so the file is
`~/agentlab-runner/logs/metrics.jsonl`; the copy of the script in `~/agentlab`
reads `~/agentlab/logs/`, which only has lines from runs started there.

Each row is a phase (research, build, review, maintain, replenish, health,
pipeline observer) with its record count, how many ended rc 0, and the median
minutes, turns, output tokens and cost. Medians skip parse-error records.

## Permissions per phase

Every phase runs under `--permission-mode dontAsk`: a tool call that is not on
the phase's allow list is refused on the spot, never asked about (nobody is
awake to answer). The lists live in `.pipeline/phase_tools.sh`, picked by the
key `run_phase` is given:

| Phase (key) | May use | Notably refused |
|---|---|---|
| researcher (`research`) | WebSearch, WebFetch, hn-search, Read/Glob/Grep; Edit/Write only in `research/`, `knowledge/`, `BACKLOG.md`, `projects/*/PLAN.md` | **any shell** |
| builder (`build`) | Read/Glob/Grep/Edit/Write, Bash | `git commit`, `git push`, `gh` |
| reviewer (`review`) | Read/Glob/Grep, Bash; Write only `logs/last-review.md` | `git commit`, `git push`, `gh` |
| maintainer (`maintain`) | Read; a fixed list of `git` commands incl. `push`; `gh pr create/view/list`; Write `logs/last-pr.txt`, `logs/pr-body.md`, `projects/*/PLAN.md` | web tools, `gh pr merge`, force push |
| replenish (`replenish`) | Read/Glob/Grep; Edit/Write only `BACKLOG.md` | any shell, web tools |
| health / observer (`health`, `observe`) | Read/Glob/Grep, Bash; Write only their own reports | `git commit/push`, any `gh` that writes |

The researcher's open-PR and branch checks now read
`logs/research-context.md`, which `run.sh` writes (`write_research_context`)
before research and again before build. If it cannot be written — `gh` is
down, say — the cycle stops, because the duplicate-topic checks depend on it.
Every refused tool call is listed in the run log (`denied: Bash: git push …`)
and counted in the metrics line (`denials`).

A second, independent check: `.claude/hooks/publish_guard.py` runs before
every shell command in a nightly phase and blocks `git push` and any `gh`
that writes unless the phase is `maintain`. It is registered only through
`.pipeline/phase_settings.json`, which `run_phase` passes with `--settings`,
so your own interactive sessions in this repo never see it.

**This is not a security boundary, and does not claim to be.** The deny rules
and the hook both match command *text*, and any phase with a shell can get
around a text match (`python3 -c`, a script file). Two things to know:

- The real protection against the main threat — a fetched web page with
  planted instructions — is that **the only phase that reads the open web has
  no shell at all.** That is enforced by the tool list, not by a text match.
- Your RTK hook (`~/.claude/settings.json`) rewrites `git push` to
  `rtk git push` *before* permissions are checked. A deny rule for only the
  plain form was bypassed by this in testing (2026-11-08), so every deny is
  written in both forms, and the hook sees through `rtk`.

Accepted risk: a builder or reviewer that deliberately works around the text
checks could still push, even straight to `main`. Neither reads the web; what
they act on is the research note and the repo, so a planted instruction would
have to survive the researcher writing it into a note first.

The runner clone is an *untrusted* workspace, so the project's
`.claude/settings.json` allow list does not apply there; each list in
`phase_tools.sh` is complete on its own. `--model` overrides an agent file's
`model:` line, so the table in "Model per phase" is what runs.

## The review record on the PR

Before `run.sh` merges a PR, it adds the reviewer's findings to the PR body
under an **Independent review** heading: the verdict, the `Ran:` lines (what
the reviewer ran to check the code), and every `Findings:` bullet, copied
word for word from `logs/last-review.md`. Before this, the findings lived
only in that file, which is git-ignored and overwritten every cycle, so a PR
merged with warnings and nits nobody could read afterwards.

This step is part of the merge gate. `attach_review_to_pr` (in `run.sh`)
leaves the PR **open, unmerged,** if any of these happen, and says which in
the run log:

- the PR body cannot be read, or `gh pr edit` fails, or the section is not
  there when the body is read back afterwards;
- `logs/last-review.md` has no `Findings:` block;
- the review text matches a credential pattern (`sk-ant-…`, a GitHub token,
  an AWS key id, a private key, `API_KEY=…`). Nothing is posted; read the
  verdict file yourself. Long hex strings are not flagged, because findings
  cite commit SHAs;
- the maintainer's body is so long there is no room left for the section.

The section starts with a hidden line, `<!-- agentlab-review -->`. If it is
already there (a rerun, or a night that died after posting), nothing is
added again. If the body plus the section would pass GitHub's 65,536-character
limit, only the section is cut, never the maintainer's text, and it ends with
a note saying so. The format code is `.pipeline/review_record.sh`.

External mode is not covered: it never merges, so it has no merge to gate.

## Findings as GitHub issues (spec #6, step 1 of 3, off by default)

The plan is for `BACKLOG.md` to hold only portfolio topics, with health and
pipeline findings living in GitHub issues labeled `health` or `pipeline`. It
rolls out in three steps:

1. **(this step)** File findings to issues **and** to the backlog, as before.
2. **(ready, not run)** A one-off migration moves the unclaimed
   `fix (health …)` lines already in `BACKLOG.md` to issues. See below.
3. Stop filing findings to the backlog.

**It is off until you turn it on.** The issues are public, so `run.sh` files
them only when `.pipeline/file-issues` holds `on`. The file ships holding
`off`. A missing file also means off; anything else is logged and treated as
off. To turn it on:

```bash
gh label create health   --description "Found by the nightly health check"
gh label create pipeline --description "Found by the nightly pipeline observer"
echo on > .pipeline/file-issues   # then commit and push to main
```

When on, for each finding (`issue_file_finding` in `.pipeline/issues.sh`):

- The issue title is the finding's subject, with whitespace squeezed and cut
  to 256 characters. It is the dedupe key, as the subject is in the backlog.
- **An open issue with that exact title and label already exists:** a
  "Re-observed <date> (run <ts>)" comment is added. No new issue.
- **Only a closed one exists:** a new issue opens and links the old one. A
  finding that comes back after its fix is new information.
- **None exists:** a new issue opens. Its body holds the detail, the date, the
  run, and which report it came from.
- The text goes through the same credential scan as the review record. A
  match is not posted.
- **gh fails, or the scan matched:** the finding is appended to
  `logs/unfiled-findings.md`, the night raises one alert per observer naming
  how many were unfiled, and the night carries on. Findings are report-only;
  a GitHub outage must not cost a night's work. Unfiled findings are not
  retried; file them by hand from that file.

The issue is filed whatever the backlog step said, so a finding already
queued in `BACKLOG.md` still gets its "re-observed" comment.

### Step 2: moving the existing findings

```bash
bash .pipeline/migrate_backlog_findings.sh --dry-run   # lists, files nothing
bash .pipeline/migrate_backlog_findings.sh             # files and deletes
```

For each `- [ ] fix (health <date>): <subject> — <detail>` line it files an
issue through the same `issue_file_finding` (so an already-open issue gets a
comment, never a twin) and deletes the line once GitHub has it. `[building]`
and `[done …]` lines stay, as does everything under `## Stranded work`. It
does not commit: read `git diff BACKLOG.md` and commit it yourself. Do not run
it while a night is running.

- **Label:** both observers filed under one prefix, so the label is guessed
  from the subject: `health` if it names `examples/`, `knowledge/` or a
  `[[wikilink]]`, `pipeline` otherwise. The dry run prints every guess.
- **The live run refuses** unless `.pipeline/file-issues` says `on`. The dry
  run works either way and calls no `gh`.
- **Safe to re-run.** A line that failed to file stays and is retried next
  time. A line whose issue was filed but whose delete failed gets a comment
  next time, not a second issue. Exit code 1 means at least one line stayed.
- It pauses 2 seconds between issues to stay under GitHub's limit on how fast
  an account may create content.

Run it close to step 3. Until step 3, the nightly filing still writes
findings into `BACKLOG.md`, so a migrated finding seen again comes back as a
new line.

## Tools available to the pipeline

Beyond the built-in file/search/web tools, the researcher also has the
`hn-search` MCP server (`examples/mcp-hn-search/`, registered in `.mcp.json`)
for practitioner discussion/reception, not just vendor docs.

## Running it

- Manually (recommended first, to shake out PATH/auth):
  `AGENTLAB_IGNORE_SCHEDULE=1 bash ~/agentlab-runner/.pipeline/run.sh` — in the
  runner clone, not your working copy (see "The runner clone" below). The env var is required
  outside 01:00-05:59 — see the run window below. Any non-empty value counts,
  so `=0` and `=false` also mean "run anyway"; unset it to put the guard back.
- On a schedule: the launchd job `com.steeb.agentlab.daily` runs the runner
  clone's `run.sh` daily at 02:00 local time (America/Chicago).
  `.pipeline/setup_runner.sh` writes and loads it. It runs overnight deliberately — a job this long sharing a
  rolling 5-hour usage window with interactive daytime work is what makes both
  feel starved.
  launchd runs a missed job on next wake, so a Mac asleep at 02:00 will start
  the run whenever the lid next opens; `pmset repeat wakeorpoweron` keeps that
  from landing in the middle of a workday. That wake must stay EARLIER than the
  02:00 slot, but not so much earlier that the `pmset -g` idle `sleep` timer
  puts the Mac back down before the job fires (idle sleep is 10 min, so 01:55
  is the intended wake — 01:50 would race its own trigger).
- Run window: `run.sh` refuses to start outside **01:00-05:59** and exits 0
  (a skipped night is a correct no-op, not a crashed job — exiting 1 would
  make launchd's own accounting treat every daytime wake as a failure). The
  window is defined once in `.pipeline/schedule.sh`; the 02:00 launchd slot
  must sit inside it. This is the belt to `pmset`'s braces: `pmset` stops the
  Mac being asleep at 02:00, and the window stops a run that starts late from
  eating the afternoon regardless of why it was late. Between 2026-08-29 and
  2026-09-03 six consecutive runs fired in the 11:47 and 13:47 hours with the
  minute still pinned to the configured `:47` — a pattern a lid-open wake does
  not explain, and still unproven as of 2026-09-04. The window does not depend
  on knowing the cause. The tradeoff it accepts: a missed night stays missed.
- Budget roughly 20–25 min per cycle, plus ~12 min when the health check runs
  and a few minutes more when the pipeline observer runs alongside it.

Logs land in `logs/` (git-ignored).

### The runner clone

The night runs from **`~/agentlab-runner`**, a full clone of `origin` that
nothing but the pipeline touches. `~/agentlab` is yours: a tracked edit left
there no longer aborts the night, and the night's scrub (`git clean -fdx`) can
no longer delete your untracked work. Your changes reach the runner only
through merged PRs, which the night pulls before it starts. A git worktree
would not do: two worktrees cannot both check out `main`.

Nothing hardcodes the location. `run.sh` and `eval/run_reviewer_eval.sh`
find the repo from their own path and export it as `AGENTLAB_REPO`; the phase
agents and `.mcp.json` read that variable (`.mcp.json` falls back to
`/Users/steeb/agentlab` for interactive sessions, where it is unset).

Set it up, or check it, from `~/agentlab`:

```bash
bash .pipeline/setup_runner.sh --dry-run      # what would change
bash .pipeline/setup_runner.sh --no-launchd   # build the clone, leave the job alone
bash .pipeline/setup_runner.sh                # ...and point the launchd job at it
```

It clones if needed, copies your local git identity, copies `logs/` once (with
mtimes, so the weekly health and observer cadence carry on instead of
restarting from "never"), builds the hn-search venv, and rewrites and reloads
the job, saving the old plist to `logs/launchd-plist-before-runner.plist`.
Each step checks before it acts, so a second run prints "Nothing to do". It
refuses to touch the job while a night is running. To go back, restore the
saved plist and `launchctl bootout` / `bootstrap` it.

The hn-search venv (`examples/mcp-hn-search/.venv`) is the one ignored path
besides the logs that the nightly scrub spares. Before this, the scrub deleted
it every night, which is why the researcher ran without hn-search from
2026-11-02 on. `.pipeline/deps.sh` rebuilds it when it is missing; a failed
build warns and the night goes on without it.

**Failure mode:** if `~/agentlab-runner` is deleted or moved, launchd fails
before `run.sh` starts, so no morning alert fires. Rerun `setup_runner.sh`.

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

### Login: the long-lived token and the auth probe

On 2026-10-30, 10-31 and 11-01 every phase died with `Failed to authenticate:
OAuth session expired and could not be refreshed`. The headless run had been
borrowing the interactive login, and nothing checked it before the first
phase. Two preflights now run after the network check:

- **Token.** `run.sh` reads a long-lived token from the login Keychain item
  `agentlab-claude-oauth` and exports it as `CLAUDE_CODE_OAUTH_TOKEN` for
  every `claude` call. It is never echoed or logged. With no such item it
  falls back to the interactive login and says so in the log; an item that
  exists but cannot be read (a locked Keychain) aborts the night.
- **Probe.** One `claude -p` call on haiku, with no MCP servers and no tool
  permission, must succeed before any phase spends anything. An auth failure
  logs `AUTH FAILED: <the CLI's message>` and aborts; a usage limit exits 75
  like any other.

One-time setup (by hand, in a terminal — never paste the token anywhere else):

```bash
claude setup-token                                            # prints a token valid for about a year
security add-generic-password -a "$USER" -s agentlab-claude-oauth -w   # paste it at the prompt
```

To replace it later, delete the old item first
(`security delete-generic-password -a "$USER" -s agentlab-claude-oauth`), then
repeat both steps. If the log says `AUTH FAILED`, that is the fix.

### Morning alert

Any night that gets past the run window and does not ship everything it set
out to — an abort, a usage limit, `shipped 0/1` — writes
`logs/ALERT-<timestamp>.md` and posts a macOS notification titled
"agentlab". A night that ships everything is silent, and so is a daytime wake
that the run window skips. The decision is `night_alert_summary` in
`.pipeline/alert.sh`; it works from the exit code and the shipped/cycles
counts, not the log's prose, so external mode (which never prints
`=== done ===`) alerts only when it exits non-zero. The alert never writes to
the run log, which must still end on the `=== done ===` line. A night killed
with SIGKILL cannot run the alert and stays silent; the pipeline observer is
the backstop for that.

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
