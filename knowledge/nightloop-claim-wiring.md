# Wiring claim reconciliation into a night (nightloop m7b)

Durable facts from `research/2026-11-20-nightloop-claim-wiring.md`. Related:
[[pipeline-claim-lifecycle]], [[shipping-a-cycle]], [[cycle-orchestration-gates]].

- The "shipped" half of the claim lifecycle needs a *merged* PR (it reads the
  merge commit's backlog diff). A pipeline that only opens PRs, as nightloop
  does, cannot call it at the moment of shipping; it needs a record of PR
  numbers across nights, or it will only ever see `UNREADABLE`.
- Commit the backlog edit with a pathspec (`git commit -- BACKLOG.md`), never
  `add -A`, so unrelated dirt never rides along.
- A backlog commit whose push failed is not lost if recovery counts commits
  ahead of `origin/main`: it is snapshotted to a branch on the next cycle.
- Run the stranded step after each cycle's recovery and before the usage-limit
  stop, as bash does, so the next cycle cannot re-pick built work.
