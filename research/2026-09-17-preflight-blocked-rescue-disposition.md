# Preflight's BLOCKED dirty-main abort: give it the one piece of evidence it's missing

## Question

`BACKLOG.md`'s topmost unclaimed item (line 212) is a health finding: the
2026-08-30 pipeline run aborted at preflight because `main` had a tracked
(not just untracked) dirty change, "downstream of run-2026-08-29's t[rouble]"
(the finding is itself truncated in `BACKLOG.md`). Is there a real, buildable
fix here, or was preflight's abort just correct behavior that happens to cost
a night sometimes?

## Note on sourcing for this cycle

This topic is entirely internal to the `agentlab` pipeline's own bash/git
tooling — no Claude/Anthropic API surface, no external library. "Primary
sources" here means the actual repo code, the actual run logs, and a fresh
`gh pr diff`/`gh pr list` read against the real GitHub state, not vendor docs.
No `WebSearch`/`WebFetch` calls were made; none would have told me anything
this repo's own artifacts didn't already answer more precisely. The
`claude-api` skill is not applicable — nothing here touches a model call.
The `hn-search` MCP server was unavailable this session (missing venv at
`examples/mcp-hn-search/.venv/bin/python3`, per the task notes) — moot for
the same reason.

## Findings

**The incident, read from the actual logs, not the truncated backlog line.**
`logs/run-2026-08-29_114701.log` (186 lines) ends mid `--- phase: cycle 2/2:
review (model: opus) ---` with nothing after it — the process stopped
existing mid-phase, leaving `main` with uncommitted tracked changes.
`logs/run-2026-08-30_114704.log` (5 lines total) is the entire next night:
preflight ran `git status --porcelain`, found the tracked leftovers, and
printed exactly the BLOCKED message before exiting — 3 seconds of a run that,
per `PIPELINE.md`, normally budgets ~20-25 minutes per cycle. That's the
"downstream of run-2026-08-29's t..." the truncated line is pointing at.

**This is not a one-off; `run.sh`'s own postflight comment already names three
earlier recurrences.** Line ~858 of `.pipeline/run.sh`: "On FAIL the
maintainer deliberately leaves a diff uncommitted on main for a human to
inspect — correct in isolation, but it means the *next* run's preflight
aborts on 'main not clean', which is what happened on 2026-08-02/03/07."
2026-08-29→08-30 is (at least) the fourth occurrence of the same shape.

**`PIPELINE.md`'s documented rationale for the hard-abort is, empirically,
sometimes false.** `PIPELINE.md:248-251`: "A modified tracked file aborts the
run. The per-cycle and postflight snapshots already rescue a failed cycle, so
a tracked edit surviving to the next night means something outside the
pipeline changed the repo." `.pipeline/run.sh:125-129` states the identical
premise inline. Both `snapshot_dirty_main` call sites (`run.sh:754` inside
the per-cycle loop, `run.sh:861` at postflight) are ordinary sequential lines
in the script's own control flow — not a signal trap. When the process is
killed mid-phase (the 2026-08-29 shape: log stops at a `--- phase: ... ---`
header with nothing after it), execution never reaches either call site, so
the "snapshots already rescue a failed cycle" premise the abort message
relies on is false for exactly the case that produced this backlog item.
This was independently confirmed, on this exact machine, by the researcher
who shipped PR #44 earlier today (2026-09-17): `knowledge/pipeline-run-log-shapes.md`
records a direct experiment (`GNU bash 3.2.57(1)-release`, the version
`run.sh` actually runs under) showing a `trap` does not fire until a
foreground child returns, and cannot fire against a same-process-group
`SIGKILL` at all — so no code added *inside* `run.sh`'s execution can
guarantee cleanup ran before the process died.

**PR #44 (merged this morning, 2026-09-17 00:27, commit `906b14d`) built
exactly the missing piece and explicitly deferred using it.** `classify_run_log`
in `.pipeline/run_log.sh` reads one `logs/run-*.log` file and returns which of
four states it reflects, no model involved:

| rc | state | meaning |
|----|-------|---------|
| 0 | OK | reached `=== done` with everything shipped |
| 1 | PARTIAL | reached `=== done` with `X < N` shipped |
| 2 | ABORTED | never reached `=== done` — includes the silent shape |
| 3 | input error | path missing/unreadable — not evidence of anything |

`run_log.sh`'s own header says plainly: "this file is not wired into `run.sh`
or any agent yet." Its research note
(`research/2026-09-17-pipeline-run-log-classifier.md`, "Explicitly out of
scope") gives the reason: wiring it into `run.sh`/`test_gates.sh` this cycle
"would collide with [PR #36] rather than build alongside it" — PR #36's own
diff touches every `run_phase` call site.

**That collision risk is real and, as of today, worse than the prior
researcher could have known: a second open PR contests the same region.**
I read both PRs' actual diffs rather than trusting their titles:

- **PR #36** ("gate each phase on the artifact it was supposed to produce",
  opened 2026-09-02, still open) touches `.pipeline/run.sh`,
  `.pipeline/test_gates.sh`, `.pipeline/postcondition.sh`, `PIPELINE.md`. One
  hunk is `@@ -39,18 +39,19 @@ CLAUDE="claude -p --permission-mode
  bypassPermissions"` through the `for lib in backlog verdict health
  pipeline_health preflight; do` loop — it adds `postcondition` to that list.
- **PR #39** ("pipeline: run at 02:00 CT, and refuse to start outside the
  01:00-05:59 window", opened 2026-09-04, still open) *also* touches
  `.pipeline/run.sh` and `.pipeline/test_gates.sh`, with a hunk at the exact
  same `@@ -39,18 +42,19 @@ CLAUDE=...` / `@@ -59,6 +63,26 @@ for lib in
  backlog verdict health pipeline_health preflight; do` location — it adds a
  new `schedule` lib to the same loop.

So the lib-sourcing loop in `run.sh` (~lines 42-59) is a live hot zone with
two independent, stale (13-15 days old), unmerged PRs both wanting to extend
it. A third addition to that exact loop this cycle would all but guarantee a
three-way conflict for whoever merges next. `gh pr view <n> --json mergeable`
returns `UNKNOWN` for all three open PRs (GitHub hasn't computed it, or it's
genuinely stale) — no signal either way on whether they're still intended to
land.

**Conclusion: the fix is real, but wiring it into `run.sh` is not this
cycle's job.** The correct, precedented move — PR #44 already modeled it for
`classify_run_log` itself — is to ship the *decision logic* this backlog item
needs as new, standalone, fully offline-tested files that touch neither
`run.sh` nor `test_gates.sh` nor `PIPELINE.md`, and say plainly that wiring it
into preflight's BLOCKED branch is the next increment once #36 (and now #39)
land or are closed.

## Build proposal

**What it is.** A new, small pure-logic library,
`.pipeline/rescue.sh`, plus its own offline test file,
`.pipeline/test_rescue.sh` — new files only, no existing file edited. It
answers the one question preflight's BLOCKED branch is currently missing:
*given what the immediately-preceding run log says about how that run ended,
is this dirty tracked `main` expected leftover from a killed cycle, or
something a human needs to look at?*

**Where it goes.** `.pipeline/rescue.sh` (new) and `.pipeline/test_rescue.sh`
(new). Not `.pipeline/preflight.sh` — that file's own header states "Sourcing
this file defines one function and one constant," a contract already true and
worth preserving as-is. Not `.pipeline/test_gates.sh` or `.pipeline/run.sh` —
both are live edit targets of PR #36 and PR #39 per the findings above.

**Behavioral spec.**

- `preflight_blocked_disposition <classify_run_log_rc>` — pure, no I/O.
  Input: the integer return code `classify_run_log` (from
  `.pipeline/run_log.sh`, sourced separately by the caller) produced for the
  run log immediately preceding the current one. Output: prints `RESCUE` or
  `BLOCK` on stdout; return code mirrors it (0 RESCUE, 1 BLOCK).
  - rc 2 (ABORTED, either sub-shape) → `RESCUE`. The only state in which the
    "postflight/per-cycle snapshot already rescued it" assumption is known,
    structurally, to be false.
  - rc 0 (OK) or rc 1 (PARTIAL) → `BLOCK`. Both guarantee `snapshot_dirty_main`
    ran (loop iteration or postflight), so a dirty tracked `main` surviving
    past either one really is unexplained — current behavior, unchanged.
  - rc 3 (unreadable/missing prior log) or any other input → `BLOCK`. Fails
    closed: no evidence of safety is not evidence of safety, same posture
    `classify_run_log` itself already takes on its own rc 3.
- `most_recent_other_run_log <exclude-path> <path...>` — pure, no I/O (the
  caller enumerates `logs/run-*.log` and passes the list in; this function
  does no `ls`/`find` itself). Prints the lexicographically-greatest path in
  the list that isn't `<exclude-path>`, and returns 0; if nothing remains
  after excluding it, prints nothing and returns 1. Safe because
  `run-YYYY-MM-DD_HHMMSS.log` is fixed-width and zero-padded, so lexical order
  is chronological order (confirmed against real filenames in `logs/` today).

**Invariants / failure modes.**
- Sourcing `.pipeline/rescue.sh` alone defines functions only: no output, exit
  0, no filesystem/git/network touched — the same contract every other
  `.pipeline/*.sh` lib already documents and `test_gates.sh` already asserts
  for the other five (this file deliberately isn't added to that shared
  assertion this cycle, to avoid touching `test_gates.sh` — `test_rescue.sh`
  asserts it standalone instead).
  `preflight_blocked_disposition` given anything other than `0`/`1`/`2`/`3`
  (empty string, non-numeric, `4`) must fail closed to `BLOCK`, not error out
  and not default to `RESCUE`.
- This increment makes **no** behavioral change to a running `agentlab`
  pipeline: nothing in `run.sh` calls either function yet. That is
  deliberate, matching `run_log.sh`'s own precedent, and must be stated as
  plainly in `rescue.sh`'s header as it is there.

**Acceptance criteria ("it works").**
1. `bash .pipeline/test_rescue.sh` exits 0, all cases pass, runs in well under
   a second, no network, no `ANTHROPIC_API_KEY`, no `claude` CLI.
2. Test cases for `preflight_blocked_disposition`: rc 0 → BLOCK, rc 1 → BLOCK,
   rc 2 → RESCUE, rc 3 → BLOCK, and at least one fail-closed case (empty
   string or `9` → BLOCK).
3. Test cases for `most_recent_other_run_log`: excludes the given path;
   picks the lexically-latest remaining path from a synthetic list of
   `run-*.log`-shaped names in scrambled order; prints nothing and returns 1
   when the excluded path is the only one given (models the very-first-run
   case).
4. One case reproduces the actual incident this backlog item is about:
   source `.pipeline/run_log.sh` (already built, already tested, unmodified),
   feed `classify_run_log` a synthetic heredoc reproducing
   `run-2026-08-29_114701.log`'s exact shape (a `--- phase: cycle 2/2: review
   (model: opus) ---` line with nothing after it — `test_run_log.sh` case 3
   already proves this classifies ABORTED/rc 2), and assert
   `preflight_blocked_disposition` on that rc returns `RESCUE`.
5. `bash -n` is clean on `rescue.sh` and `test_rescue.sh` (mirrors every other
   lib's `R5`/`R1`/`C32a`-style syntax-check case).
6. Existing suites are unmodified and still green:
   `bash .pipeline/test_gates.sh` (95 passed today), `bash
   .pipeline/test_backlog.sh` (41 passed today), `bash
   .pipeline/test_run_log.sh` (38 passed today) — run all three before and
   after to prove zero collision with PR #36/#39's live diffs.
7. `rescue.sh`'s header states, in the same voice `run_log.sh`'s already
   does: not wired into `run.sh`/`test_gates.sh` this cycle, why (PR #36 and
   PR #39 both touch the lib-sourcing loop right now), and what wiring it in
   later would look like (preflight's `BLOCKED)` case calls
   `most_recent_other_run_log` over `logs/run-*.log` excluding its own `$LOG`,
   classifies the result, and branches on `preflight_blocked_disposition`
   instead of unconditionally aborting — falling back to today's exact abort
   message whenever the answer is `BLOCK` or the rescue attempt itself
   fails).

**Explicitly out of scope for this increment** (mirrors `run_log.sh`'s own
"Explicitly out of scope" section): calling `snapshot_dirty_main` from the
new RESCUE path (that call site lives in `run.sh`, deferred with everything
else); any edit to `run.sh`, `test_gates.sh`, or `PIPELINE.md`; diagnosing
*why* a cycle gets killed (session limit vs. the 600s ceiling vs. something
else) — same "structure, not cause" posture the classifier already commits
to.

## Open questions

- PR #36 and PR #39 are both 2+ weeks stale with `mergeable: UNKNOWN`. Whether
  they're actually still intended to land, or should be closed/rebased by a
  human, isn't something this cycle can determine — it only means "don't add
  a third contender to the same loop this week."
- Whether `classify_run_log`'s ABORTED state should be split further (e.g.
  distinguishing the silent sub-shape from the "with a message" sub-shape) for
  the RESCUE decision specifically — this proposal treats all of rc 2 as
  RESCUE-eligible, on the reasoning that *either* sub-shape means the run
  never reached `=== done`, so neither the per-cycle nor postflight
  `snapshot_dirty_main` call is guaranteed to have run. Worth the wiring
  cycle's second look once it exists, but doesn't change today's pure
  function.
- Once wired, should `rescue.sh`'s RESCUE path log *which* prior run
  triggered it (so a human auditing `cycle/*-unshipped-*` branches later can
  trace the branch back to the incident)? Not decided here — the pure
  function only returns RESCUE/BLOCK; what the caller logs is the wiring
  cycle's call.
