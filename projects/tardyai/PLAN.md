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
3. [done kushsarora/TardyAI#3] Turn-by-turn directions (core). Today each leg gets one
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
4. [not-started] Routes follow the corridors (user report: "the map is
   making routes that walk through rooms"). The nav graph in
   `pkgbc231600/data/MapNode` and `data/NavEdge` was drawn by eye. Corridor
   edges cut across classrooms and restrooms. The entrance edge crosses the
   building diagonally. Rooms hang off 25-50 m diagonal spurs instead of a door
   on their corridor. The verify step "routes stay in corridors"
   (`checks/walkability.py`) samples the plan pixels under every edge, and it
   fails on main today (31 corridor edges, 15 room spurs). Make it pass:
   - Rebuild the graph on all three floors along the corridors as drawn on
     `ui/react/public/plans/floor_p{1,2,3}.png`. Look at each plan image
     before placing nodes. Put hallway nodes on corridor centrelines and at
     every junction and bend, so each corridor edge stays at least 90% on
     open floor.
   - Every room gets a `door` node on its corridor and a short room edge
     (15 m or less) from the door to the room node. No room connects to
     anything except its door.
   - Map every room number printed on the three plans, not just today's 24.
     1270 on floor 1 is the user's failing case. Each mapped room gets a
     `MapNode` (kind `room`, `roomNumber`) and the matching `Waypoint` label
     Room Finder uses. List the printed numbers per floor in a test fixture
     and assert each one is a room node that `findRoute` reaches from the
     entrance. Rooms you cannot read clearly go in the PR body, not guessed.
   - Keep stair and elevator nodes and their `connectorKey`s. Keep existing
     node ids wherever the node survives, so Waypoints and tests stay valid.
     Compute `seconds` from pixel length with the convention already in the
     data (about 0.77 s per metre at 12 px/m).
   - Acceptance: `VERIFY: PASS`, including "routes stay in corridors" and
     "seed data integrity". The 1310 → 1270 route on floor 1 walks only
     corridors (attach a screenshot or an overlay image to the PR). The
     #3 step tests still pass, with fixture expectations updated where the
     geometry changed, and a note in the PR body for each changed expectation.
   - The PR checklist says: "Re-provision in Studio to reload seed data".
5. [not-started] Room Finder routes through NavRouter (user report: "I'm on
   floor one in 1310, how do i get to 1270" fails with "Directions only — I
   could not place this route on the plans"). Today the vision LLM picks every
   waypoint label on the path, and `_resolveRoute` rejects the whole route if
   any label is unknown. That is fragile even with a perfect graph. Change
   the division of labour:
   - The LLM only extracts the start (a room, entrance or landmark, plus a
     floor if given) and the destination from the message, as validated JSON.
     It no longer chooses the path.
   - `RoomFinder.findRoute` resolves both ends to `MapNode`s (exact room
     number first, ignoring case, spaces and a "rm"/"room"/"#" prefix) and calls
     `c3.NavRouter.findRoute(start, dest, stepFree)`. Steps and legs come
     from the same router as Navigate, so the two pages always agree.
   - Return the existing `VisionRoute` shape, so the UI keeps working
     (expand/contract: add fields, don't remove any). Put the router's points
     on the annotated plans. Check the coordinate scale: the annotated images
     are 1500x1159 and the floor plans are 1980x1530.
   - An unknown room never produces "Directions only". The user gets "I can't
     find room X on these plans. Did you mean …?" with up to three nearby
     room numbers. If the start is missing, ask for it.
   - Acceptance: Python tests with the LLM client stubbed. "1310 → 1270",
     "from the west entrance to 3335" and a cross-floor request each return
     a placed route whose node sequence equals `NavRouter.findRoute` for the
     same ends. An unknown room returns suggestions, not an error. A message
     with no start asks for one. Verify passes.
6. [not-started] AI directions panel beside the map (user request). The
   Navigate page gets a "Directions" panel next to the map (to its right on
   desktop, under it on phones). It shows the route as a few plain, friendly
   sentences an AI writes, e.g. "Go in through the West Entrance and take the
   stairs on your right up to Level 3. Turn left, and 3335 is the third door
   on your left — about 3 minutes." The numbered steps from #3 stay visible
   under the panel as the exact reference.
   - Backend: a new `@py(env='server')` type `RouteNarrator` with
     `narrate(route: NavRoute): RouteNarration`. It uses the same
     `c3.GenaiCore.Llm.Completion.Client.forConfigKey("default-completions")`
     client as `RoomFinder.py`.
   - Grounded, not free-form: the model gets only the route's structured
     `steps`, the floor names and the destination. It does not get the map
     image.
   - The reply is validated before it is shown. Every connector and floor it
     names, and the destination room, must appear in the steps, in step order.
     It must not name any room or connector the steps lack.
   - On a failed check, a model error or a timeout, it returns
     `source: "steps"` with the deterministic step sentences joined, so the
     panel is never empty and never wrong.
   - Results are cached per route signature (start, destination, step-free),
     so re-renders and repeat demos don't re-call the model.
   - UI: the panel has loading, AI and fallback states, a small "AI" label
     when `source` is the model, and it re-narrates when the route changes.
     Put any new UI types under `src/lib/` (`Interfaces.tsx` is the Studio
     agent's hot file).
   - Acceptance:
     - `c3_parity` covers `narrate`.
     - Python unittest with a fake client: prompt contents, a valid reply
       passes, a reply that invents a room or misorders floors falls back,
       and a raising client falls back.
     - jest: the panel's three states, and a new route triggers a new
       narration.
     - PR checklist: re-provision `RouteNarrator`, confirm the
       `default-completions` config exists in Studio, and read the narration
       for West Entrance → 3335 in both modes.
7. [not-started] Room entry finds the right room, and fix the two step-wording issues #3's review found. The student types a
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
   or candidate list, plus the all-rooms test. Wording: (a) a final leg with
   no known heading (the step-free single hop from Elevator C to 3335) must
   not guess "straight ahead". Say "You have arrived at 3335" or work out
   the side from the doorway edge. (b) Cut the frequent "Turn around" steps
   (25 of 295 across West-Entrance routes, caused by spur stair/connector
   nodes) by not emitting a turn when the path just steps into and out of a
   spur. Test both on seed data.
8. [not-started] The route starts where the student actually is. The "I'm
   here" pin (tap or GPS) goes through `nearestNode`, which picks the closest
   node of any kind by straight-line pixel distance. It can snap to a room or
   a stair node across a wall, or to a node behind the student, so the route
   starts with a needless backtrack. Fix it without breaking anything that
   calls `nearestNode`:
   - Add `NavRouter.snapToGraph(floorId, x, y)`. It projects the pin onto the
     nearest walkable edge segment (hallway or connector edges on that floor)
     and returns the edge, the projected point and the fraction along the
     edge.
   - Add `findRouteFromPoint(floorId, x, y, destNodeId, stepFree)`. It starts
     A* from both ends of that edge, with costs prorated by the fraction, and
     the first leg's polyline begins at the projected point.
   - Room nodes are snap targets only when the pin is closer to them than
     to any edge.
   - Keep `nearestNode` and `findRoute` unchanged. Navigate switches to the
     new path.
   - GPS: when the accuracy radius covers more than one corridor, the UI
     asks the student to confirm the pin instead of routing silently.
   Acceptance (`node --test` on seed data plus a jest test):
   - A pin at the midpoint of a hallway edge routes with an ETA between the
     ETAs from its two endpoints.
   - A pin just outside a room's wall snaps to the hallway, not the room.
   - A pin's route never revisits the snapped edge.
   - `c3_parity` passes for both new methods.
   - The PR checklist says to re-provision `NavRouter` in Studio.
9. [not-started] Mobile Navigate with directions. At phone width (≤ 640px)
   the sidebar collapses and the form stacks above the map. The step list
   becomes a bottom sheet over the map. The map auto-switches to the floor of
   the current leg. Every control is at least a 44px touch target, and the
   desktop layout is unchanged. Acceptance: jest + testing-library tests of
   the responsive layout and the floor auto-switch. The Studio Mobile preview
   check goes on the PR.
10. [not-started] Arrival-time correctness and demo hardening:
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
- 2026-10-19: Stop stacking PRs. TardyAI doesn't auto-delete merged branches,
  so GitHub never retargeted the stacked PRs: #2 merged into the leftover
  `lock-file-sync` branch, and its tests never reached `main`. #3 (stacked on
  #2) is being retargeted to `main` to land both. From now on `base=main`, and
  a milestone that needs an open PR's code waits for that merge. Hot files now
  ignore commits by `self_email` (agentlab's identity), so our own merged
  work doesn't lock us out of `NavRouter.js`.
- 2026-10-19: Added milestone 4, the AI directions panel (user request). It
  stays grounded in #3's computed steps and falls back to them, because a
  model that invents a turn is worse than no AI. Old 4–6 become 5, 7 and 8,
  and the start-point fix is 6.
- 2026-10-19: Core flow before features (user report: Room Finder fails on
  1310 → 1270, and Navigate draws routes through rooms). Added milestones 4
  (graph follows corridors) and 5 (Room Finder parses with the LLM and routes
  with NavRouter) ahead of everything else. The AI panel moves to 6, and the
  rest move down two. Added the verify step "routes stay in corridors"
  (`checks/walkability.py`). It fails on main by design until milestone 4
  lands. Corridor edges must be at least 90% open plan pixels, ignoring 2 m at
  each end. Room edges are capped at 15 m. Measured on main: good corridor
  edges block at most 6%, bad ones 40-100%.

## Current state

2026-10-19 (build, cycle 3): milestone 3 (turn-by-turn directions) is built in the checkout and passes `VERIFY: PASS` (node 24/24, jest 2/2). It is still `in-progress` until the PR ships. Every acceptance criterion in the research note is met. Changes in `/Users/steeb/agentlab-work/tardyai`:
- Backend: new `src/RouteStep.c3typ`, plus an optional `steps: [RouteStep]` on `RouteLeg.c3typ`. In `NavRouter.js`, the new `turnDirection` and `buildSteps` are called from `assembleLegs`, which now records per-leg hops without adding them to the result. `instruction` strings are unchanged (a test checks this).
- Tests: `test/node/steps.test.js` covers the seed demo and step-free routes, every routable seed room, a one-point leg, the L fixture walked both ways plus its mirror, a reversal and a collinear merge. Flipping the cross-product sign fails 7 of these tests.
- UI: `RouteSteps.tsx` shows a numbered step list with distances under each leg, and falls back to `instruction` when `steps` is absent. `tardyApi.normalizeRoute` turns `steps` into an array the same way it does for `points`. `RouteSteps.test.tsx` is the repo's first jest test. `jest.config.ts` did not need the `@/` alias.
Departures from the proposal:
- The step types live in `src/lib/routeStepTypes.ts`, not `pages/Navigate/`, because `lib/tardyApi.ts` imports them and lib should not depend on pages.
- When the last hop bends off the hallway into the room, there is no separate turn step. The `arrive` step carries that hop's meters and names the landmark: "3335 (Class Room 3335) is on your left at Elevator C (east)", shown with 45 m. The proposal had `arrive` at 0 m. The bend's meters would otherwise fold into "walk 119 m", which overstates the 74 m hallway walk.
Known quirks, left alone on purpose:
- The demo route says "Turn around" at the West Stairs spur.
- The L1 entrance turn is 31.6 degrees, just over the threshold.
- Curve drift is accepted.
The PR checklist must add "Re-provision in Studio (new type RouteStep, new field RouteLeg.steps)".
