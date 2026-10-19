# TardyAI (external: kushsarora/TardyAI)

## Goal

Make TardyAI — indoor wayfinding for late students in Grainger Hall, a C3 AI
app built in C3 AI Studio — demo-ready for the hackathon: it builds cleanly the
way Studio builds it, its routing core is tested, and it works in the Studio
**Mobile** preview, since a student running late is on their phone.

## Scope

In scope: build reproducibility, the offline platform-parity gate, tests for
the A* router and UI libs, mobile layout, bugs found along the way.
Out of scope: deploying to C3, editing `WORKSPACE_SUMMARY.md` (the Studio
agent's memory), renaming the package, new buildings/floors, anything needing
a live C3 tenant to verify — say so explicitly; a milestone outside this list
should not get picked up without first amending this section.

## Milestones

Ordered; the researcher picks the topmost `not-started` item each cycle (or
resumes the topmost `in-progress`/`blocked-but-now-unblocked` one — never
skip ahead of an unfinished predecessor without saying why in the decisions
log). Status values: `not-started` / `in-progress` / `blocked: <reason>` /
`done <repo>#<PR>`.

1. [done kushsarora/TardyAI#1] Lock file in sync: `npm ci` in `pkgbc231600/ui/react` fails
   (`package-lock.json` is missing `@mui/*`, `@testing-library/dom`, …).
   Regenerate it from `package.json` with the same major versions and make
   `verify.sh` pass end to end on a fresh checkout.
2. [done kushsarora/TardyAI#2] Router unit tests: `pkgbc231600/test/node/*.test.js` run by
   `node --test` (no new dependency). Load `src/NavRouter.js` in a `vm`
   context with stubbed `MapNode`/`NavEdge` globals fed from `data/`, then
   assert the README's demo claims: West Entrance (L1) → 3335 is ~3 min in 2
   legs; step-free swaps West Stairs for Elevator C; an unreachable or unknown
   room yields a failed route, not a throw.
3. [not-started] Mobile layout: in the Navigate page, at phone width (≤ 640px)
   the sidebar collapses, the form stacks above the map, and controls are at
   least 44px touch targets. Desktop layout unchanged. Verify with a jest +
   testing-library test of the responsive classes, and list the Studio Mobile
   preview check on the PR.
4. [not-started] UI lib tests: jest tests for `src/lib/geoTransform.ts`
   (GPS ↔ pixel round trip against `Floor.calibration`) and the pure parts of
   `src/lib/tardyApi.ts`.
5. [not-started] Class-time ETA edge cases: "Arrive N min late / N min to
   spare" across midnight, an unset time and a past time — tests first, then
   fix what they expose.

## Decisions log

Append-only — never edit or delete an entry. To change a prior decision, add
a new entry that supersedes it and says why. A cycle's work must not
contradict an entry here without adding one.

- 2026-10-19: Worked as an external repo by PR, never merged by the pipeline
  — the repo belongs to a teammate (Kush) and has no license, so its code must
  not be copied into the public agentlab repo.
- 2026-10-19: `npm ci`, not `npm install`, is the build gate — a clean deploy
  installs from the lock file, and `npm install` silently hides drift.
- 2026-10-19: No new runtime dependencies without a decisions-log entry: the
  Studio template pins its toolchain, and an unvetted dependency is a deploy
  risk nobody can test offline.
- 2026-10-19: Router tests use `node --test` + `vm`, not a C3 test harness —
  NavRouter.js's only platform coupling is `MapNode.fetch` / `NavEdge.fetch`,
  so stubbing those two globals tests the real file unmodified.

- 2026-10-19: Milestone 2 is stacked on PR #1's branch (`target` base =
  `agentlab/2026-10-19-lock-file-sync`), because `npm ci` fails on `main` until
  #1 merges, which would fail every cycle at the verify gate. GitHub retargets
  the stacked PR to `main` when #1 merges. Set `base=main` back right after
  that merge: if the branch is deleted, `ext_sync` aborts the night.

## Current state

2026-10-19 (after build, cycle 2): milestone 2 (router unit tests) in-progress, built and awaiting review. Milestone 1 is done as kushsarora/TardyAI#1; this work stacks on its branch. In the checkout (untracked, uncommitted): `pkgbc231600/test/node/harness.js` (`seed()`, `loadRouter(data?)`, which evaluates the unmodified `src/NavRouter.js` in a fresh `vm` context with `MapNode`/`NavEdge`/`Filter` stubs that throw on unknown filter paths) and `pkgbc231600/test/node/router.test.js` (10 tests, all passing on node v24.5.0). They cover: West Entrance to 3335 in 150-210 s, 2 legs and 2 floors via West Stairs; step-free goes via Elevator C with no stairs and a different ETA; unknown start, unknown destination and unmatched `routeToRoom` fail without throwing; an isolated synthetic room fails with "No walkable route"; with elevators out of service, step-free fails with "No step-free route" while the default route still works; and the same-node and blank-arg paths fail. Mutation check: with `ELEVATOR_WAIT_SECONDS = 0` in a scratch copy, 2 tests fail, so the suite is not vacuous. In agentlab, `verify.sh` `node_tests` now passes `"$dir"/*.test.js`, not the directory (which fails on Node 24), and fails if the directory has no tests. `bash projects/tardyai/verify.sh <checkout>` gives `VERIFY: PASS` with the router step running 10 tests. Fixture note: elevator nodes sit inline in hallway chains, so the "stairs only" fixture removes the elevators' `connectorKey`, not their edges. Next: review, then PR (no UI change, so the PR checklist only needs "Studio build still passes after pulling").
