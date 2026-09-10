# The lifecycle of a backlog claim

`BACKLOG.md` is the pipeline's work queue and its only mutual-exclusion
mechanism. A researcher "claims" an item by editing `[ ]` → `[researching]`.
This note records what actually happens to that claim as `.pipeline/run.sh`
moves through a night, and the three points where the claim is silently lost.

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

## Failure 3 — a shipped claim is never marked `[done #N]`

The state machine above draws `[building] --…human/auto merges--> [done #N]`,
but until 2026-09-10 **no pipeline step performed that rewrite**. The builder
is the only phase that edits the claim (`[researching]` → `[building]`,
`agentlab-builder.md`).
The maintainer opens the PR and writes only `logs/last-pr.txt`. Auto-merge runs
`gh pr merge --merge --delete-branch` and logs — it never touches `BACKLOG.md`.
`reconcile_stranded_claims` scans only `cycle/*-unshipped-*` snapshot branches
(failed cycles) and only rewrites `[ ]` → `[stranded …]`. So a **successful**
cycle merged its own `- [building] <text>` line to `main` verbatim and it stayed
`[building]` until a human edited it or the pipeline-observer re-filed it as a
health finding.

Observed: `[done #17]` carries *"backlog entry was stale"* (fixed 2026-08-09 by
hand); PR #33 (2026-08-16), PR #37 and PR #38 (both 2026-09-02) and PR #41
(2026-09-09) all merged and left their items `[building]`. Five occurrences,
every one corrected by a hand-written `chore(backlog): mark ... done (#N)`
commit on main afterwards — the repo has been running this reconciler manually
all along.

**Generalization (same as Failure 1):** a claim advanced by a *successful*
cycle is exactly as unenforced as one released by a *failed* one — the state
machine has arrows nothing walks.

**Resolved 2026-09-10.** `run_cycle` now calls `reconcile_shipped_claim
<pr_num>` on the line after `PR #N auto-merged`, before the caller's
`snapshot_dirty_main`, so the mark lands on main ahead of the next cycle's
researcher. Same split as the Failure 1 fix: the decision is
`backlog_mark_done <path> <key> <pr_num>` in `backlog.sh` — literal key match,
idempotent, distinct return codes (0 rewritten / 1 bad path or non-numeric PR
number / 2 item not found / 3 already handled), no temp file left on any path
— tested offline as C24–C32, while the git/gh plumbing stays in `run.sh` and
always returns 0. See `research/2026-09-10-backlog-mark-done-reconcile.md`.

**Recovering the shipped item's text: diff the merge commit, not the merge
base.** The instinct is to reuse the Failure 1 recipe verbatim —
`git diff $(git merge-base main $oid) $oid -- BACKLOG.md` with
`$oid = gh pr view <n> --json headRefOid`. It silently yields an **empty diff**,
and the research note proposed it before anyone ran it. The reason is that the
two cases are not symmetric: a stranded branch is *not* an ancestor of main, so
its merge base is a real fork point — but a merged PR's head commit **is** an
ancestor of main, so `git merge-base main $oid` returns `$oid` itself and the
diff is empty. Verified against PRs #37, #38 and #41 on 2026-09-10, all three of
which return their own head SHA as the merge base.

What works is asking for the change *the merge introduced to main*: the merge
commit against its **first parent**, which is main as it stood immediately
before the merge.

```
mc=$(gh pr view <n> --json mergeCommit -q .mergeCommit.oid)
git diff "$mc^1" "$mc" -- BACKLOG.md      # contains '+- [building] <key>'
```

That also removes two fragilities the research note flagged as open questions:
it does not depend on the head commit surviving `--delete-branch`, and it does
not depend on the merge being a `--merge` at all — a squash merge has one
parent and the same first-parent diff still describes what the PR added.

**What it still does not cover.** A PR that auto-merge declined (conflict, or
`mergeable=UNKNOWN`) and a human merged later never reaches this call site, so
its item stays `[building]` until the observer re-files it. A periodic
`gh pr list --state merged` sweep is the deferred follow-up. And exactly one PR
could never be fixed by its own code: the one that introduced it, because
`run_cycle` was already parsed into the running shell before the new call site
existed on disk — that last one took the same hand-written mark-done commit as
its four predecessors.

## The counting contract

The literal prefix `- [ ] ` at column 0 is the interface between `BACKLOG.md`
and the pipeline (`grep -c '^- \[ \]'`). An item written any other way — extra
indent, different marker — is invisible to both the researcher and the
replenishment gate. The regex now has exactly one executable copy, at
`.pipeline/backlog.sh:39` — it was duplicated across `run.sh` when this note
was first written, and consolidating it there is what made the counting logic
testable offline (`bash .pipeline/test_backlog.sh`).

What remains elsewhere is prose about the contract, not a second
implementation of it: `PIPELINE.md` (lines 90 and 105) describes the `- [ ] `
prefix, and `agentlab-researcher.md:26` says "topmost unclaimed `[ ]` item"
in the looser, unanchored form. Those still have to agree with the regex in
meaning, but they are not copies that can drift character-by-character.

Note `grep -c` with zero matches prints `0` **and exits 1**, so the `|| true`
at `backlog.sh:39` is load-bearing, not defensive noise — the reasoning is
recorded at `backlog.sh:30-31`, next to the code it protects rather than only
here. (`run.sh`'s one remaining `|| true`, on the `git branch -D` at line 204,
is a different thing entirely: it tolerates a failed cleanup of an empty
recovery branch.)

## Related

- [[bash-3.2-testable-scripts]] — how to get this orchestration logic under test
  on the box that runs it
- [[doc-transcript-drift]] — same shape of bug: an invariant spanning files that
  are never edited together, so diff-scoped review cannot see it
