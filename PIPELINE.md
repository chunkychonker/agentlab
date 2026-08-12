# The build pipeline

A nightly pipeline that ships N increments: research → build → review →
maintain → auto-merge, repeated `.pipeline/cycles` times, bracketed by a
backlog replenishment check before and after the loop, plus a lab-wide health
check every 3rd night. Each phase is a
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
4. **Maintainer** (`agentlab-maintainer`) — reads the verdict. Only on `PASS`
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
   merged PR. Gated to run every 3rd calendar day (rerunning every example's
   tests every night doesn't scale with the portfolio's size) via
   `logs/lab-health-*.log` timestamps — no separate schedule state needed.
   Report-only: findings never block, delay, or otherwise affect phases 1–5,
   and it never fixes anything itself. Report lands in `logs/last-health.md`
   (git-ignored, same handoff pattern as `logs/last-review.md`).

Two gates protect code that carries your name: the reviewer before the PR, and
either a mechanical clean-merge check or **you** resolving a conflict by hand.
Nothing reaches `main` without both — conflict resolution is never automated,
since it requires judgment about intent that no part of this pipeline has.
The health check is not a gate — it's a separate, non-blocking observability
pass over what's already shipped.

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
triggers, so it can be tested with no network and no API key. Run
`bash .pipeline/test_backlog.sh` by hand after editing `run.sh` or
`backlog.sh`; it is on-demand only, like `eval/run_reviewer_eval.sh`, and the
health check below does not cover it.

## Mode: demo vs. project

`.pipeline/mode` picks the track for every cycle — `demo` (default) works
`BACKLOG.md` as above, one independent increment under `examples/` per cycle;
`project:<slug>` works `projects/<slug>/PLAN.md` instead, building one
milestone at a time into `projects/<slug>/`, which persists and grows across
cycles rather than starting fresh each time. Each subagent reads
`.pipeline/mode` itself as step 0, so this isn't threaded through the phase
prompts. See `projects/README.md` for how to start one.

## Tools available to the pipeline

Beyond the built-in file/search/web tools, the researcher also has the
`hn-search` MCP server (`examples/mcp-hn-search/`, registered in `.mcp.json`)
for practitioner discussion/reception, not just vendor docs.

## Running it

- Manually (recommended first, to shake out PATH/auth): `bash .pipeline/run.sh`
- On a schedule: the launchd job `com.steeb.agentlab.daily` runs `run.sh` daily.
  See the repo setup notes for how to load/unload it.
- Budget roughly 20–25 min per cycle, plus ~12 min when the health check runs.

Logs land in `logs/` (git-ignored).
