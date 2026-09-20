# How a `logs/run-*.log` ends, and why a trap can't guarantee it says so

`.pipeline/run.sh` writes exactly one closing line on a fully successful run —
`=== done <TS> === (shipped N/M, full log: ...)` — at the very end of the
script. Everything else about how a night ended has to be read out of the raw
text. This note records the shapes that text actually takes, and an empirical
result that rules out the obvious "just add a trap" fix for the worst one.

## The three shapes

1. **OK** — reaches `=== done ...` with `shipped N/N`.
2. **PARTIAL** — reaches `=== done ...` with `shipped X/N`, `X < N` (a clean
   review `FAIL`, or a phase that returned non-zero and was handled).
3. **ABORTED** — never reaches `=== done`, and splits into two sub-shapes that
   matter differently:
   - **with a message** — the last thing in the log is a line `run.sh` itself
     printed on purpose (`"... Aborting."`, `"phase '<name>' exited non-zero —
     see <log>"`, a review-gate line). The script noticed something was wrong
     and said so before stopping.
   - **silent** — the log's last content is a `--- phase: NAME (model: MODEL)
     ---` announcement with *nothing after it*. Nobody printed a reason
     because nothing ran long enough to print one — the process that was
     supposed to run the phase (or the phase's foreground `$CLAUDE` child)
     simply stopped existing.

`.claude/agents/agentlab-pipeline-observer.md`, the agent that classifies
these today by reading the raw log, states as fact: "the script's abort lines
end with `Aborting.`" That's true of sub-shape one and false of sub-shape two.
A real incident (`run-2026-08-29_114701.log`, cycle 2's review phase) is
exactly sub-shape two — confirmed by reading the log directly, not by
inference. See [[health-finding-parsers]] for a prior, structurally identical
case of the observer's own prose disagreeing with what actually happens on
disk (the `(none)`-bullet bug).

## Why "add a `trap` to `run.sh`" does not fix the silent shape

Tested directly on this machine's actual pipeline shell
(`/bin/bash`, `GNU bash 3.2.57(1)-release` — the exact version
`.pipeline/run.sh` runs under, confirmed via `bash --version`):

```bash
trap 'echo "TERM caught" >> "$LOG"' TERM
sleep 8     # stands in for run_phase's foreground `$CLAUDE ...` call
```

Sending `SIGTERM` to the parent script one second into that eight-second
foreground child does **not** run the trap at the one-second mark. Timestamped
log output showed the trap fired only *after* `sleep` returned on its own,
~8 seconds later — bash defers a pending trap until whatever foreground
command it's blocked in `wait()` on finishes, it does not interrupt it. Against
a same-process-group `SIGKILL` (uncatchable by any shell, by definition, no
experiment needed) a trap cannot fire at all.

`run_phase` in `run.sh` calls `$CLAUDE --model "$model" "$prompt"` as exactly
such a blocking foreground child. Whatever external mechanism is actually
killing a night mid-phase — this note does not know which, see the open
question in `research/2026-09-17-pipeline-run-log-classifier.md` — a trap
added to `run.sh` would not have produced a closing line for the sub-shape-two
incident above, because the foreground child it was blocked on never returned
control to the shell for the trap to run.

**The implication:** guaranteeing a closing line be *written during* an event
that may kill the whole process tree at once is not reliably achievable from
inside the script. Classifying *after the fact*, from whatever is already on
disk when something reads the log later, is the tractable alternative — which
is what a deterministic classifier (rather than a signal handler) buys.

## Reading it mechanically

`.pipeline/run_log.sh` (`classify_run_log <path>`) is the after-the-fact reader
that follows from the above: one line of `<STATE>|<reason>` per log, return
code mirroring the state, no model in the loop. Two structural details are
worth recording because they are not obvious from the taxonomy alone.

**It keys on the log's LAST CONTENT LINE, not on "does `=== done` appear
anywhere".** The pipeline-observer phase reads *old* `logs/run-*.log` files and
echoes their closing lines into the log of the run it is itself part of — so a
`=== done ... (shipped 2/2, ...)` line sitting mid-file is routinely about a
different night. Anything looser than "last content line" would report a run
that died during the observer phase as a clean finish.

**Only the model-bearing phase header is the silent-death marker.** `run.sh`
prints `--- phase: NAME (model: MODEL) ---` from `run_phase`, the one place
that hands control to a foreground `$CLAUDE` child; its other
`--- phase: ... ---` lines (auto-merge, the two reconcile passes, the
findings-filing passes, the `... skipped` notices) print no model because they
run no model, and none of them can die the sub-shape-two way. A run that stops
during one of those is reported by quoting its last line verbatim instead.

The reason string is built only from those two markers plus the verbatim last
line — never by matching `run.sh`'s own failure wording. Matching the prose
would be coupling-by-meaning (CLAUDE.md §2) between two files that are never
edited together, and is also precisely the assumption that failed the
pipeline-observer here.

## Not yet wired in (as of 2026-09-20)

`classify_run_log` (above) exists and is fully tested (`bash
.pipeline/test_run_log.sh`), but three things downstream of it are still
true, confirmed by direct `grep`, not inference — worth checking whether
they're still true before assuming this note describes the live system:

- **`run.sh` never sources `run_log.sh`.** Its `for lib in ...` loop lists
  eight libraries (`backlog verdict health pipeline_health preflight
  postcondition schedule network_retry`); `run_log` is not one of them, and
  `test_gates.sh` mentions the file only in a comment, never in a sourced-lib
  loop or a test case.
- **`.claude/agents/agentlab-pipeline-observer.md` still states the disproven
  claim as fact.** Section "1. Run outcomes" says an ABORTED run's "abort
  message" — universally — is "the script's abort lines end with
  `Aborting.`" This note's own "The three shapes" section above already shows
  that's false for the silent sub-shape; the agent's instructions were never
  updated to say so.
- **Nothing anywhere parses a `run-*.log` filename's embedded date against a
  cutoff.** `run.sh` computes `PIPE_CUTOFF` (a date string) for the
  pipeline-observer's window, but only ever *hands that string to the
  subagent in its prompt* — the actual filtering of which `logs/run-*.log`
  files fall in or out of the window is done by the model reading filenames
  itself, every run. `PIPE_CUTOFF`/`LAST_DATE` date-extraction exists inline
  for `lab-pipeline-*.log`/`lab-health-*.log` (the two cadence gates), not for
  `run-*.log`.

See `research/2026-09-20-pipeline-run-log-classifier-wiring.md` for the
proposed fix (two new functions, `run_log_in_window` and `run_log_manifest`,
extending `run_log.sh` itself) and PR #44's own research note
(`research/2026-09-17-pipeline-run-log-classifier.md`) for why this wiring was
deferred in the first place (it was blocked on PR #36/phase-postconditions
landing and its `run_phase` signature churn settling — confirmed settled by
2026-09-20, via `git merge-base --is-ancestor` against `main`).

## Related

- [[health-finding-parsers]] — the sibling case: a parser/producer contract
  silently drifting, found live via a different bad assumption in the same
  agent's own template
- [[pipeline-claim-lifecycle]] — the other kind of state a failed or killed
  cycle can silently lose (a `BACKLOG.md` claim, not a log's closing line)
- [[bash-3.2-testable-scripts]] — the environment constraint (bash 3.2 only)
  this experiment and any classifier built from it must respect
- [[network-preflight-retry]] — a confirmed concrete instance of the
  "a line sitting mid-file is routinely about a different night" gotcha above:
  `grep -l "NETWORK UNREACHABLE" logs/run-*.log` matches
  `run-2026-09-02_134705.log` even though that night's own preflight passed —
  the pipeline-observer phase was quoting `run-2026-08-31`'s closing line into
  its own report
