# Stale health findings starve the backlog

Observed 2026-10-25. `backlog_count_unclaimed` (`.pipeline/backlog.sh`) counts every
line beginning `- [ ] `, including auto-filed `fix (health ...)` items. Once feature
items were all done, 47 historical health observations (old run outcomes, "no run log for
<date>", aggregated session-limit counts) kept the count above the replenish threshold,
so `backlog_ensure_stocked` never fired and the researcher landed on unactionable
items. Filing is deduped ([[health-finding-parsers]], `.pipeline/pauses`) but nothing
closes an item once obsolete.

Gotchas: `backlog_has_unresolved` treats all markers except `[done ` as still queued
(suppresses re-filing), so close stale items as `[done by hand DATE: ...]`, which lets
real recurrences re-file and does not trip the `[done #N]` PR-mismatch check. A build
that only touches `.pipeline/` fails `increment_built` ([[pipeline-claim-lifecycle]]),
so tooling goes under `examples/`. See `research/2026-10-25-backlog-stale-health-triage.md`.
