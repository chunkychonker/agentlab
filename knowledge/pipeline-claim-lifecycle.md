# The lifecycle of a backlog claim

`BACKLOG.md` is the pipeline's work queue and its only mutual-exclusion
mechanism. A researcher "claims" an item by editing `[ ]` → `[researching]`.
This note records what actually happens to that claim as `.pipeline/run.sh`
moves through a night, and the two points where the claim is silently lost.

## The intended state machine

```
[ ]  --researcher-->  [researching]  --builder-->  [building]
     --maintainer opens PR, human/auto merges-->   [done #N]
```

`PIPELINE.md` asserts the exclusion property this way:

> Sequential cycles see the previous cycle's claim because it lands on `main`
> as part of that cycle's merged PR.

That sentence is true **only when the cycle ships**. It is the load-bearing
assumption behind running N cycles a night, and it has no enforcement.

## Failure 1 — a failed cycle silently releases its claim

Every claim lives in the working tree until a PR merges. When a cycle fails,
`run.sh` calls `snapshot_dirty_main` (carrying the claim off to a
`cycle/<date>-unshipped-*` branch) and then `reset_to_clean_main`, which
restores a `BACKLOG.md` where the item reads `[ ]` again. The next cycle picks
the same topic.

Observed 2026-08-12: cycle 1 researched *and* built server-side compaction; the
maintain phase died on `API Error: 529 Overloaded`; the finished work went to a
snapshot branch with **no PR**; cycle 2 saw the item unclaimed.

**Why the standard defence misses it.** The researcher's procedure says to check
`gh pr list --state open` before committing to a topic, because a built-but-
unmerged PR looks unclaimed. A snapshot branch is *not a PR*, so that check
returns empty. The reliable check is `git branch -a` plus a look at what the
branch touches — a snapshot branch is named `cycle/<date>-unshipped-<time>-<n>`
and its commit message begins `wip:`.

**Generalization:** a claim stored in the same medium as the work is released
by any recovery path that discards the work. Exclusion state and work product
want different durability.

## Failure 2 — replenishment measures a backlog it did not fill

Replenishment is gated on `unclaimed < CYCLES` and, before the fix proposed on
2026-08-12, ran only *after* the cycle loop. The interaction is subtler than
"it runs too late":

When the backlog drains mid-loop, the researcher's own empty-backlog fallback is
to file new items by hand. Those hand-filed items are then counted by the
post-loop gate, which skips. On 2026-08-11 that is exactly what happened — a
researcher filed two items at cycle 2, and the log records
`replenish skipped (2 unclaimed >= 2/night)`.

Consequence: **the replenishment phase has never executed** in any run log. A
feature whose gate is satisfied by the symptom it exists to prevent accumulates
no evidence that it works.

**Generalization:** if a fallback path repairs the condition that a monitor
checks, the monitor never fires and you learn nothing. Check for the *cause*, or
run the monitor before the fallback can act.

## The counting contract

The literal prefix `- [ ] ` at column 0 is the interface between `BACKLOG.md`
and the pipeline (`grep -c '^- \[ \]'`). An item written any other way — extra
indent, different marker — is invisible to both the researcher and the
replenishment gate. This regex is currently restated in `run.sh` (twice), in
`PIPELINE.md`, and in the researcher's agent definition; four copies of one
magic value.

Note `grep -c` with zero matches prints `0` **and exits 1**, so the `|| true`
in `run.sh` is load-bearing, not defensive noise.

## Related

- [[bash-3.2-testable-scripts]] — how to get this orchestration logic under test
  on the box that runs it
- [[doc-transcript-drift]] — same shape of bug: an invariant spanning files that
  are never edited together, so diff-scoped review cannot see it
