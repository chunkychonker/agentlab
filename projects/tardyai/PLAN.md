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

1. [not-started] Lock file in sync: `npm ci` in `pkgbc231600/ui/react` fails
   (`package-lock.json` is missing `@mui/*`, `@testing-library/dom`, …).
   Regenerate it from `package.json` with the same major versions and make
   `verify.sh` pass end to end on a fresh checkout.
2. [not-started] Router unit tests: `pkgbc231600/test/node/*.test.js` run by
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

## Current state

Baseline 2026-10-19 at `main` = ed7c81c: `verify.sh` passes every step except
`npm ci` (lock file drift). The UI builds after a plain `npm install`; there
are zero tests (`jest --passWithNoTests`). The C3 Studio agent is actively
pushing to `main` (corridor-grid work in flight: `data/CorridorGrid/`,
`tools/build_corridors.py`, `data/Waypoint/Waypoint.json`), so expect hot
files. Next: milestone 1.
