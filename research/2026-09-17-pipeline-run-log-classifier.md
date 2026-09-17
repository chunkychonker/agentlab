# 2026-09-17 — deterministic termination classifier for `logs/run-*.log`

## Question

The topmost unclaimed `[ ]` item in `BACKLOG.md` (line 211) is a stale-looking
single-night health finding: `run-2026-08-29_114701` — "no `=== done`; log
stops mid `cycle 2/2: review` with no verdict and no `Aborting.` line." Is this
still worth building against, and if so, what's the smallest real increment
that fixes the underlying gap rather than just this one log?

## Findings

**The topic is not covered by any open PR.** Checked all three
(`gh pr list --state open`, 2026-09-17):
- #36 `feat/phase-postconditions` — gates each phase on whether it produced its
  artifact *after `run_phase` returns*. Its own commit message names the
  problem it fixes precisely: "the CLI still exited 0... exit code is not
  evidence an artifact was produced." That is a different failure shape from
  line 211's: in the 2026-08-29 log, `run_phase`'s foreground `$CLAUDE` call
  never returned at all — nothing runs after it dies, postcondition included.
  Read `origin/feat/phase-postconditions`'s diff directly to confirm neither
  `run_phase`'s new 4th argument nor `postcondition.sh` touch this case.
- #39 `worktree-schedule-0200` — a launchd/wall-clock scheduling guard,
  unrelated.
- #40 `cycle/2026-09-07-context-editing-cache-tradeoff` — ships the
  already-`[stranded]` context-editing/prompt-caching item (line 150), not
  this one.

**Read the actual incident log** (`logs/run-2026-08-29_114701.log`, local —
`logs/` is gitignored, confirmed via `cat .gitignore`, so this file exists only
on this machine and is not a committed fixture). Its last content is line 185:
```
--- phase: cycle 2/2: review (model: opus) ---
```
— then nothing. Not "exited non-zero", not "Aborting.", not a verdict. `git
blame`-adjacent context: cycle 1 that same night got a clean review `FAIL`
(logged in full, unrelated to this), and cycle 2's *research* phase earlier in
the same run printed `Background tasks still running after 600s; terminating.`
and then continued normally (that message is `claude`'s own background-task
watchdog finishing tool calls early — informational, not fatal, confirmed by
the fact that phase went on to write a real research note and a build
followed). Cycle 2's *review* phase is the one that died outright.

**Read `.claude/agents/agentlab-pipeline-observer.md`, the agent whose job this
is today.** It classifies every run log into OK / PARTIAL / ABORTED *by
reading the raw text itself*, each run, and its own contract states: "ABORTED
— never reached `=== done`. State the last thing it did **and the abort
message (the script's abort lines end with `Aborting.`)**." That assumption is
false for the exact incident this backlog item names — there is no `Aborting.`
line anywhere in that log. The observer's own documented procedure has no
answer for "the process vanished before printing anything at all," which is
presumably why this finding reads as a raw quote from the log rather than a
diagnosis. `knowledge/health-finding-parsers.md` already documents one prior
instance of the observer's own instructions silently disagreeing with what its
downstream parser accepts (the `(none)` bug, fixed 2026-09-12) — this is the
same shape one level up: an assumption in the agent's prose that a concrete log
disproves.

**This observer phase itself is expensive and limit-prone.** The auto-filed
health findings lower in `BACKLOG.md` record "phase 'pipeline observer' exited
non-zero — 3x ... (all session-limit)". It reads `logs/run-*.log` newer than
its last cutoff, in full, by eye, every run. A cheap, deterministic
pre-classification of *structure* (did it finish, did it ship everything, did
it die silently) before the LLM has to reason about *cause* is a genuine cost
reduction for exactly the phase that keeps blowing its budget — not just a
nicety.

**Empirically tested bash's trap/signal behavior before proposing anything
signal-based**, because the naive fix ("add a trap to `run.sh` so it always
prints a closing line") needed checking, not assuming. On this machine's real
`/bin/bash` (`GNU bash, version 3.2.57(1)-release` — same box, same version
`.pipeline/run.sh` runs under):
```bash
trap 'echo "TERM caught"' TERM
sleep 8   # foreground child
```
Sending `SIGTERM` to the *parent* bash script one second into an 8-second
foreground `sleep` does **not** run the trap until `sleep` itself returns —
confirmed by timestamped log lines (trap fired ~8s later, not ~1s later), which
matches documented bash behavior: signals are deferred while bash is blocked
`wait()`-ing on a foreground command, and the trap can never fire at all
against a `SIGKILL` (uncatchable by definition) delivered to the whole process
group, which is what "session limit" abruptly ending an entire agent session —
`run.sh` and its child `claude` process together — would look like from
outside. **A trap-based fix could not have produced a closing line for the
2026-08-29 incident either**, since `run_phase`'s `$CLAUDE ...` call is exactly
such a foreground blocking child. This rules out the trap approach and points
at the alternative below: classify *after the fact*, from what's already on
disk, instead of trying to guarantee a closing line be written *during* an
event that may be fundamentally unwriteable-through.

**No existing code does this classification mechanically.**
`.pipeline/pipeline_health.sh`'s `pipeline_findings()` only parses the
*agent-written* `logs/last-pipeline-health.md` markdown report — it never reads
a raw `logs/run-*.log`. `.pipeline/health.sh` is the portfolio-rot sibling,
unrelated. Confirmed by `grep -n` across both files: neither contains a `case`
or pattern matching `--- phase:` or `=== done`.

## Build proposal

New library `.pipeline/run_log.sh` + `.pipeline/test_run_log.sh`, in the same
pattern as `postcondition.sh`/`verdict.sh`/`preflight.sh`: pure, offline,
sourced-and-tested independently of `run.sh`. **Deliberately not wired into
`run.sh`, `test_gates.sh`, or any `.claude/agents/*.md` file in this
increment** — `test_gates.sh` and every `run_phase` call site in `run.sh` are
live edit targets of the still-open PR #36 (its own diff shows every call site
migrating to a new 4-argument signature); adding calls or test cases there
this cycle would collide with that PR rather than build alongside it. This
increment ships as new, standalone files only.

### What it is

`classify_run_log <path>` — given one `logs/run-*.log` file, print exactly one
line, `<STATE>|<reason>`, and return a code that mirrors it:

- `OK|shipped N/N` — return 0. The log's last non-blank line matches
  `=== done ... === (shipped N/N, full log: ...)` with `N == N`.
- `PARTIAL|shipped X/N` — return 1. Same line, `X < N`.
- `ABORTED|<reason>` — return 2. No `=== done` line found. `<reason>` is
  **always non-empty**, built purely structurally (never by matching
  `run.sh`'s specific wording — see "why no string matching" below):
  - if a `--- phase: NAME (model: MODEL) ---` line exists and is itself the
    log's last non-blank line: reason states phase `NAME` started and nothing
    followed (the 2026-08-29 shape, exactly);
  - if such a line exists but more content follows it: reason quotes the
    verbatim last non-blank line of the file, with `NAME` named too (covers
    "exited non-zero", a printed review-FAIL gate message, or anything else
    `run.sh` might print, without the classifier knowing the exact wording in
    advance);
  - if no such line exists at all: reason says no phase started and quotes the
    verbatim last non-blank line if the file is non-empty (covers a
    preflight-only death — bad `CYCLES` config, `NETWORK UNREACHABLE`), or
    states `(empty log)` if the file has zero bytes.
- Unreadable/missing/directory path: nothing on stdout, one line on stderr,
  return 3. Fails closed, same posture as `artifact_freshness` in
  `postcondition.sh` — an unreadable log is not evidence of anything.

**Why no string matching against `run.sh`'s specific failure text** (e.g.
`"Aborting."`, `"exited non-zero"`): coupling the classifier to another file's
exact wording is coupling-by-meaning (CLAUDE.md §2) — a harmless rewording of
one of `run.sh`'s log lines would silently break classification with no test
catching it, since the two files aren't edited together. Keying only on the
two structural markers `run.sh` already treats as load-bearing itself
(`=== done`, `--- phase: ... ---`) and otherwise reporting the raw last line
verbatim gets the same diagnostic value without that coupling.

A direct-execution CLI tail (`[ "${BASH_SOURCE[0]}" = "$0" ]` guard — verified
working under this exact `/bin/bash 3.2.57` in a throwaway test) lets
`bash .pipeline/run_log.sh logs/run-2026-08-29_114701.log` be run by hand,
useful on its own before any pipeline wiring exists.

### Interfaces (stubs, no bodies — builder's job is layer 4)

```bash
# .pipeline/run_log.sh
# classify_run_log <path>
# Prints "<STATE>|<reason>" to stdout, one line, no trailing content.
# STATE in {OK, PARTIAL, ABORTED}. reason is "-" only for a plain OK with
# nothing further to say (implementation's call whether OK ever needs more).
# Returns: 0 OK, 1 PARTIAL, 2 ABORTED, 3 input error (stdout empty on 3).
classify_run_log () { : ; }

# CLI: when executed directly (not sourced) with one argument, calls
# classify_run_log on it, prints its line, exits with its return code.
```

```bash
# .pipeline/test_run_log.sh — mirrors test_gates.sh's shape: set -uo pipefail,
# temp-dir heredoc fixtures, one PASS/FAIL line per case, final tally, exit
# 0/1. No network, no key, no real logs/ read.
```

### Where it goes / how it's tested

Both files new, at `.pipeline/run_log.sh` and `.pipeline/test_run_log.sh` — no
existing file edited. Confirmed no name collision: `ls .pipeline/` today has
no `run_log.sh` or `test_run_log.sh`.

Self-test cases (offline, synthetic heredoc fixtures — `logs/` is gitignored
so no real log can be a committed fixture; the 2026-08-29 incident is
reproduced synthetically, matching its exact structure):

1. Synthetic log ending `=== done ... (shipped 2/2, ...)` → `OK|shipped 2/2`, rc 0.
2. Same, `shipped 1/2` → `PARTIAL|shipped 1/2`, rc 1.
3. Synthetic reproduction of the real incident — last line is exactly
   `--- phase: cycle 2/2: review (model: opus) ---`, nothing after → `ABORTED`,
   rc 2, reason names `cycle 2/2: review` and says nothing followed.
4. Phase header followed by `phase 'cycle 2/2: research' exited non-zero — see
   ...` with no `=== done` → `ABORTED`, rc 2, reason contains that verbatim line.
5. No phase header at all, just a `NETWORK UNREACHABLE ... Aborting.` line →
   `ABORTED`, rc 2, reason says no phase started, quotes the line.
6. Nonexistent path → stdout empty, stderr non-empty, rc 3.
7. Empty (0-byte) file → `ABORTED|(empty log)`, rc 2.
8. Two phase headers (an earlier completed-looking one, then a second that's
   the true last line) → classifies on the *last* one, proving "last
   occurrence" rather than "first".
9. Sourcing the file alone prints nothing, exits 0 (sibling-lib convention;
   `test_gates.sh` already asserts this shape for its five libs).
10. Direct execution (`bash .pipeline/run_log.sh <path>`) on a fixture from
    case 1 prints the same line `classify_run_log` would and exits 0.

"It works" = `bash .pipeline/test_run_log.sh` exits 0, reports 10/10 (or more,
if the builder finds additional edge cases worth a case) passed, runs in well
under a second, and needs no `ANTHROPIC_API_KEY`, no `claude`, no network — all
fixtures are heredocs in a throwaway temp dir the script creates and removes.

### Explicitly out of scope

- Wiring `classify_run_log` into `run.sh`'s pipeline-observer phase call, or
  into `agentlab-pipeline-observer.md`'s instructions (its own report format
  already expects `OK`/`PARTIAL`/`ABORTED` bullets — a natural next increment
  once #36 lands and its `run_phase` signature churn settles, not this one).
- Diagnosing *why* a phase died (session limit vs. 600s ceiling vs. something
  else) — the classifier reports structure, not cause, matching the
  observer's own stated posture ("a confident wrong diagnosis is worse than a
  count").
- Any change to `run.sh`, `test_gates.sh`, `test_backlog.sh`,
  `pipeline_health.sh`, or `postcondition.sh`.
- A trap/signal-based fix inside `run.sh` — tested above and ruled out for
  this exact incident shape.

## Open questions

- Whether the real kill mechanism behind "session limit" nights sends a
  catchable signal to the whole process group, `SIGKILL`s it, or tears the
  environment down some other way is genuinely unknown from here — I could
  reproduce bash's *documented* trap-deferral behavior locally, but not the
  actual external kill this repo's own logs describe. The classifier
  sidesteps needing that answer by working after the fact from disk.
- Once this ships, whether `agentlab-pipeline-observer.md`'s own worked
  example/template should be updated to describe the "silent, no `Aborting.`
  line" ABORTED shape explicitly (mirroring the `(none)` fix
  `knowledge/health-finding-parsers.md` documents) is a fair follow-up but
  needs its own increment, after wiring exists to make it worth teaching.

## Knowledge base

New note: `knowledge/pipeline-run-log-shapes.md` — the taxonomy above (OK /
PARTIAL / ABORTED-with-message / ABORTED-silent) plus the empirical bash
trap/signal finding, wikilinked from `health-finding-parsers.md`,
`pipeline-claim-lifecycle.md`, and `bash-3.2-testable-scripts.md`; added to
`INDEX.md` under "Repo hygiene & self-verification".
