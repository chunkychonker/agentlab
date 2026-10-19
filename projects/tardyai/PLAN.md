# TardyAI (external: kushsarora/TardyAI)

## Goal

Make TardyAI — indoor wayfinding for late students in Grainger Hall, a C3 AI
app built in C3 AI Studio — demo-ready for the hackathon: it builds cleanly the
way Studio builds it, its routing core is tested, and it works in the Studio
**Mobile** preview, since a student running late is on their phone.

## Scope

In scope: the core flow — a student enters a room and gets a correct route
with directions they can follow (turn-by-turn steps, room-entry matching) —
plus build reproducibility, the offline platform-parity gate, tests for the
A* router and UI libs, mobile layout, bugs found along the way.
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
3. [not-started] Turn-by-turn directions (core). Today each leg gets one
   sentence ("Walk to West Stairs"). A late student needs directions they can
   follow without reading the map. Add `steps` to every `RouteLeg`, computed
   server-side in `NavRouter.js` from the leg's polyline. This is additive:
   keep `instruction` so nothing that reads it breaks. Add a new
   `RouteStep.c3typ` with `text`, `meters`, `seconds`, `pointIndex`, and
   `kind` (`start | straight | turn | connector | arrive`). Rules:
   - Collinear hops merge into one "Walk N m along the hallway" step, never
     one step per graph node.
   - A bearing change over ~30° is "Turn left" or "Turn right". Image y grows
     downward, so the cross-product sign is flipped compared with math
     axes. Test this with an L-shaped fixture walked both ways.
   - Name a landmark at a turn when a room or connector node sits there
     ("Turn right at 3110").
   - Connector steps say direction and destination: "Take the West Stairs up
     to Level 3".
   - The last step says which side the room is on: "3335 is on your left".
   UI: the Navigate result shows the numbered steps for each floor, with
   per-step distance. Acceptance (`node --test` on seed data plus a jest
   render test):
   - The demo route has at least 1 turn and no two consecutive `straight`
     steps.
   - Step meters sum to the leg's meters ±1.
   - The first step of each leg is `start` or `connector`, and the last leg
     ends with `arrive`.
   - The mirrored L fixture flips left and right.
   - The step-free route says Elevator C.
   The PR checklist must add "re-provision in Studio (new type
   RouteStep)". If `App.tsx` or `FloorPlanMap.tsx` is still hot, put the
   steps list in a new component that needs no wiring in those files, or
   ship backend plus tests and leave the UI half `blocked: hot`. Never edit a
   hot file.
4. [not-started] Room entry finds the right room. The student types a
   destination, and today `routeToRoom` silently routes to `searchRooms(…,
   1)[0]`, even on a weak substring match. Fix this:
   - Normalize what people actually type: "rm 3335", "Room 3335", "#3335",
     "3335a"/"3335A", stray whitespace, and case.
   - An exact room-number or name match routes directly.
   - Several weak matches return a failed route listing the candidates
     ("Did you mean 3335 or 3350?"), and the UI shows them as tappable
     choices.
   - An unknown room returns the closest suggestions instead of a dead end.
   Add an all-rooms test: every room node is routable from every entrance,
   default and step-free. It runs inside `verify.sh`'s node step, so a seed
   gap fails before Studio sees it. Fix whatever gaps it exposes, in data or
   router. Acceptance: one table-driven test of typed inputs → expected room
   or candidate list, plus the all-rooms test.
5. [not-started] Mobile Navigate with directions. At phone width (≤ 640px)
   the sidebar collapses and the form stacks above the map. The step list
   becomes a bottom sheet over the map. The map auto-switches to the floor of
   the current leg. Every control is at least a 44px touch target, and the
   desktop layout is unchanged. Acceptance: jest + testing-library tests of
   the responsive layout and the floor auto-switch. The Studio Mobile preview
   check goes on the PR.
6. [not-started] Arrival-time correctness and demo hardening:
   - jest tests for `src/lib/geoTransform.ts` (GPS ↔ pixel round trip
     against `Floor.calibration`) and the pure parts of `src/lib/tardyApi.ts`.
   - "Arrive N min late / N min to spare" across midnight, an unset time and
     a past time: tests first, then fix what they expose.
   - Explicit loading, empty and error states for no route, an unknown room
     and a failed backend call, so the live demo never shows a blank screen.

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
- 2026-10-19: PRs are feature-sized, not single fixes (user request). The old
  milestones 3–5 are regrouped into 3–6 above, and core routing now comes
  first: turn-by-turn directions, then room entry. The user asked that the
  core flow (enter a room → get a route → follow good directions) be what gets
  fixed. Mobile layout and polish follow because they matter only once the
  directions are worth showing. A cycle may bundle consecutive milestones that
  share one theme (see `EXTERNAL_MODE.md`, "PR size").
- 2026-10-19: #1 merged. Milestone 3 is stacked on PR #2's branch (`target`
  base = `agentlab/2026-10-19-router-unit-tests`), because the directions tests
  reuse #2's `vm` harness. Set `base=main` right after #2 merges.

## Current state

2026-10-19 (after build, cycle 2): milestone 2 (router unit tests) in-progress, built and awaiting review. Milestone 1 is done as kushsarora/TardyAI#1; this work stacks on its branch. In the checkout (untracked, uncommitted): `pkgbc231600/test/node/harness.js` (`seed()`, `loadRouter(data?)`, which evaluates the unmodified `src/NavRouter.js` in a fresh `vm` context with `MapNode`/`NavEdge`/`Filter` stubs that throw on unknown filter paths) and `pkgbc231600/test/node/router.test.js` (10 tests, all passing on node v24.5.0). They cover: West Entrance to 3335 in 150-210 s, 2 legs and 2 floors via West Stairs; step-free goes via Elevator C with no stairs and a different ETA; unknown start, unknown destination and unmatched `routeToRoom` fail without throwing; an isolated synthetic room fails with "No walkable route"; with elevators out of service, step-free fails with "No step-free route" while the default route still works; and the same-node and blank-arg paths fail. Mutation check: with `ELEVATOR_WAIT_SECONDS = 0` in a scratch copy, 2 tests fail, so the suite is not vacuous. In agentlab, `verify.sh` `node_tests` now passes `"$dir"/*.test.js`, not the directory (which fails on Node 24), and fails if the directory has no tests. `bash projects/tardyai/verify.sh <checkout>` gives `VERIFY: PASS` with the router step running 10 tests. Fixture note: elevator nodes sit inline in hallway chains, so the "stairs only" fixture removes the elevators' `connectorKey`, not their edges. Next: review, then PR (no UI change, so the PR checklist only needs "Studio build still passes after pulling").
