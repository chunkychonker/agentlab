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

**Resolved 2026-08-13.** `run.sh`'s `reconcile_stranded_claims` now scans
`cycle/*-unshipped-*` (local *and* origin — a failed push leaves the branch local
only), reads the claim out of the branch's `BACKLOG.md` diff against its merge
base, and re-applies it to main as `- [stranded <branch>] `. That marker is not
`- [ ] `, so the item leaves both the researcher's pick and the replenishment
count, and it names the branch a human has to salvage. Decisions live in
`backlog.sh` (`backlog_claimed_line`, `backlog_claim_key`,
`backlog_apply_stranded`) and are tested offline as C15–C23; the git plumbing
stays in `run.sh`.

Two properties worth keeping if this is ever rewritten:

- **It reconciles, it does not salvage.** It never opens a PR for a stranded
  branch and never deletes one. Whether that work should ship is a judgment
  about intent, which is the line this pipeline does not cross.
- **Ordering is the fix, not the marker.** The pre-loop call must run *before*
  `stock_backlog`, because reconciling lowers the unclaimed count; counting
  first would let the night draw an item that is already built. The in-loop call
  after each `snapshot_dirty_main` is what stops cycle *k+1* rebuilding what
  cycle *k* just stranded — the actual 2026-08-12 failure.

An item matched by text, not by line number: the item text is byte-identical on
main and on the branch, and it contains backticks, parentheses and brackets, so
every comparison is literal. A regex built from the item text misfires. The same
trap bit the implementation itself — `${line#- [building] }` treats `[building]`
as a *character class* matching one char from `{b,u,i,l,d,n,g}`, silently strips
nothing, and hands back the whole line as the key. C17 exists because of it.

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
