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

## Related

- [[health-finding-parsers]] — the sibling case: a parser/producer contract
  silently drifting, found live via a different bad assumption in the same
  agent's own template
- [[pipeline-claim-lifecycle]] — the other kind of state a failed or killed
  cycle can silently lose (a `BACKLOG.md` claim, not a log's closing line)
- [[bash-3.2-testable-scripts]] — the environment constraint (bash 3.2 only)
  this experiment and any classifier built from it must respect
