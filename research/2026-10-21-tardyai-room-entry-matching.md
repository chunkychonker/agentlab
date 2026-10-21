# TardyAI milestone 7: room entry finds the right room

Date: 2026-10-21. Mode: external:tardyai (kushsarora/TardyAI). Checkout `/Users/steeb/agentlab-work/tardyai` at origin/main 2373c9b (PR #6 still open; it touches the generator and `data/`, not `NavRouter.js`).

## Question

What does a typed destination do today in `NavRouter.searchRooms` / `routeToRoom`, what breaks for "rm 3335", "#3335", "3335a", "2nd floor restroom" and unknown rooms, and are #3's two step-wording issues still real after Kush's routing rewrite?

## Findings

Method: read `src/NavRouter.js`, `NavRoute.c3typ`, `RoomSearch.tsx`, `tardyApi.ts`, `vite.config.mts`, `test/node/*`, and probed the router on seed data through the existing `vm` harness. No web search: the question is local code, no external API facts are needed. The hn-search MCP was down this session (missing venv python), which did not matter here.

1. **Matching today** (`NavRouter.js` ~L720-775). `searchRooms` lowercases and trims the query, then scores each node: exact room number 0, room-number prefix 1, name prefix 2, name contains 3, alias contains 4, room-number contains 5. `routeToRoom` takes `searchRooms(..., 1)[0]` and routes to it, whatever the score. No normalisation exists. Probed on seed data: `rm 3335`, `#3335`, `3335a` and `2nd floor restroom` return `[]` (dead end); `33` silently routes to 3301; `restroom` matches three nodes all named "Restrooms" (one per floor), and `routeToRoom` would pick whichever sorts first.
2. **Seed facts.** 261 `room` nodes, 3 `restroom` nodes (all named "Restrooms", aliases `bathroom,washroom,toilet`, no roomNumber), 1 `entrance` (West Entrance), 12 elevators, 6 stairs. Suffixed numbers exist (`1210A`, `3115B`, `3150E`...), so a trailing letter is meaningful: `3335A` does not exist but `3335` does; `3350` exists.
3. **Who calls what.** The Navigate UI never calls `routeToRoom`: `RoomSearch.tsx` calls `searchRooms` (debounced), the student taps a result, Navigate calls `findRoute(startId, destId)`. `routeToRoom` is only reachable server-side (it is in the c3typ and `WORKSPACE_SUMMARY.md`, not in the dev server's `actions` map in `vite.config.mts`, which lists `findRoute, nearestNode, searchRooms, graphForBuilding`). So the "tappable choices" live in `RoomSearch`'s empty/ambiguous state, and `routeToRoom` gets the structured `candidates` for any other caller. Both must use one shared matcher.
4. **#3's two wording issues are largely gone on main.** Probe over West Entrance to every room and restroom, default and step-free (528 routes, 4691 steps): 0 "Turn around" steps (was 25 of 295) and 0 "is straight ahead" arrive steps (the step-free Elevator C to 3335 hop is no longer a heading-less single hop). Kush's access-point routing rewrite (5423994) removed the spur stair nodes. All 528 routes are found (0 failures). So (a) and (b) shrink to a **regression test** pinning those facts, plus one text fix that is still live: `buildSteps` (~L455) says "You have reached the mapped access point for X" when `destNode.labelX` is set, and the last-resort branch still says "is straight ahead" (L460) when the final hop has no turn. Keep the L460 branch reachable only when the heading is known (it is: `hops.length > 0` means a prior heading exists unless every hop is zero length, in which case it should say "You have arrived at X"). Do not rewrite the `labelX` branch's meaning; only check whether "mapped access point" is student-friendly and, if the builder changes it, add it to the PR body.
5. **All-rooms test already half exists.** `rooms.test.js` asserts every printed room is a room node and reachable from the entrance, but only default mode, only through its fixture, and it does not cover restrooms or step-free. The probe above shows the full sweep passes today (0 failures), so the new test is a guard, not a bug-finder. Say so honestly in the PR; do not invent fixes. If the sweep fails after PR #6 merges (data changes), that is the point of the test.
6. **Protected/hot files.** None hot (ext-context). `RoomFinder.py`, `RoomFinder.c3typ`, `VisionRoute.c3typ`, `RouteWaypoint.c3typ`: PR #5 has merged (it is in `git log`), so the "stay out" constraint has lapsed, but Room Finder resolves rooms in Python with its own rules, and unifying it is a different change. Leave it alone and say so. `WORKSPACE_SUMMARY.md` and `pkgbc231600.c3pkg.json` are protected.

## Build proposal

Covers milestone 7 only (matching, candidates UI, all-rooms guard, wording regression tests). Milestone 8 (start snapping) is a different theme.

**Intent.** A destination typed the way people type resolves to the right room, or to a short list the student can tap, never to a silent wrong room or a dead end. Out of scope: path choice, seed data, `RoomFinder.*`, LLM anything, Studio-only checks, fuzzy matching beyond the rules below.

**Behavioral spec** (all in `pkgbc231600/src/NavRouter.js`, ES5 style like the file: `var`, no arrows, no template literals; no `require`):

- Pure helper `normalizeRoomQuery(raw) -> {text, floorLevel}` (no I/O). Lowercase, trim, collapse whitespace; strip a leading `rm`, `rm.`, `room`, `no.`, `#` (with or without a following space); uppercase a trailing letter on a room-number-shaped token (`3335a` -> `3335A`); extract a floor hint from `1st|2nd|3rd|first|second|third floor`, `floor N`, `level N`, `lN` and remove it from the text. Non-string or empty -> `{text: '', floorLevel: null}`.
- Pure helper `rankRooms(nodes, query) -> [{node, score}]`, the existing score ladder moved out of `searchRooms` unchanged, plus: a name token match is word-wise and singular-tolerant (`restroom` matches `Restrooms`), and the floor hint filters by `floor.level`. `searchRooms` calls these (same signature, same ordering for queries that already worked).
- Pure helper `resolveRoomQuery(nodes, query) -> {status, matches}` with `status` one of `exact | candidates | none`:
  - `exact`: exactly one node scores 0 (room number equal after normalising), or exactly one node matches after the floor filter (so `2nd floor restroom` is the Level 2 "Restrooms"), or exactly one node matches at all with score <= 3.
  - `candidates`: two or more matches with no unique exact (`33`, `restroom` with no floor, `3335A` when only `3335` exists is **not** this: see next line). Up to 5, best first.
  - A query that normalises to a room number that does not exist but whose digits-only form does (`3335A` -> `3335`) returns `candidates: [3335]`, never a silent route to it.
  - `none`: no match. Then `matches` holds up to 3 closest rooms by shared digit prefix length then numeric distance (same floor digit first), so an unknown room gets suggestions, not a dead end. If the query has no digits and no name hit, `matches` is empty.
- `routeToRoom(buildingId, startNodeId, roomQuery, stepFree)`: `exact` -> `findRoute` as today. `candidates` / `none` -> a failed route (the existing `failedRoute` shape: `found: false`, message) whose message is `Did you mean 3335 or 3350?` for candidates and `We could not find "X" in this building. Did you mean ...?` (keep the existing substring `could not find "zzzz"`, `router.test.js` asserts it) for none, and a new optional `candidates: [MapNode]` on the route. Add `candidates` to `NavRoute.c3typ` as an optional field (additive; old clients ignore it). Never throws on any string input.
- New `suggestRooms(buildingId, query, limit) -> [MapNode]`: the `candidates`/`none` list from `resolveRoomQuery`, for the UI's empty state. Declared in `NavRouter.c3typ`, listed in `vite.config.mts`' `actions`, wrapped in `tardyApi.ts`. `c3_parity` must pass for it.
- UI: `RoomSearch.tsx` replaces "No rooms match ..." with "Did you mean" tappable buttons (calls `suggestRooms` when `searchRooms` returns `[]`; each is the same row component as a normal result, so tapping calls `onSelect`). Touch targets >= 44px is milestone 10's job, so just reuse the existing row styles. Loading and error states stay as they are.
- Step wording regression (no router change unless a test fails): the final step of the West Entrance routes to 3335 in default and step-free modes does not match `/straight ahead/`; no step across the West-Entrance sweep matches `/Turn around/`; a zero-hop final leg says `You have arrived at <dest>`.

**Interfaces** (stubs, no bodies):
```
function normalizeRoomQuery(raw)          // -> {text: string, floorLevel: number|null}
function rankRooms(nodes, query)          // -> [{node, score}]
function resolveRoomQuery(nodes, query)   // -> {status: 'exact'|'candidates'|'none', matches: MapNode[]}
function searchRooms(buildingId, query, limit)    // unchanged signature
function suggestRooms(buildingId, query, limit)   // new, js method on NavRouter
function routeToRoom(buildingId, startNodeId, roomQuery, stepFree)  // + candidates on failure
// NavRoute.c3typ: candidates: [MapNode]   (optional)
```

**Failure modes.** Non-string query: `searchRooms` returns `[]`, `routeToRoom` returns a failed route, neither throws. Unknown building: same as today (empty). Ambiguity is a failed route, never a guess.

**Acceptance criteria** (checkable):
1. `test/node/room-entry.test.js`, one table-driven test of typed input -> `{status, roomNumbers or names}` on seed data, covering at least: `3335`, `rm 3335`, `Room 3335`, `#3335`, `  3335  `, `ROOM  3335` -> exact 3335; `3335a` -> candidates `[3335]`; `3210h` -> exact `3210H`; `33` -> candidates incl. 3301, 3303; `restroom` -> candidates, three "Restrooms" on levels 1-3; `2nd floor restroom` -> exact, the Level 2 restroom (`n-f2-wc2`); `bathroom` -> candidates via alias; `9999` and `zzzz` -> none (`9999` suggests nearby rooms; `zzzz` suggests none); empty/whitespace -> none, no throw.
2. `routeToRoom` returns `found:false` with `candidates` and a message containing `Did you mean` for `33`; `found:true` with `destNodeId === findRoute`'s for `rm 3335`; the existing `could not find "zzzz"` test still passes unchanged.
3. `test/node/all-rooms.test.js`: for every entrance node and both `stepFree` values, `findRoute` to every `room` and `restroom` node is `found: true`, the last step is `arrive`, no step text matches `/Turn around|straight ahead/`, and step meters sum to leg meters +-1. Runtime under ~10 s (the probe's 528 routes ran in about a second). It runs in `verify.sh`'s node step automatically (it globs `test/node/*.test.js`).
4. Jest: `RoomSearch.test.tsx` gains cases: `searchRooms` -> `[]` and `suggestRooms` -> two nodes renders two buttons and tapping one calls `onSelect` with that node; no suggestions renders the old "No rooms match" message. (Mock `@/lib/tardyApi` the way the file already does.)
5. `VERIFY: PASS` (includes `c3_parity` for `suggestRooms`, and the walkability gate, which this change does not touch).
6. PR body: lists every router change, says the sweep found no data gaps (or lists them), says `RoomFinder.py` was deliberately not changed, and the checklist adds "Re-provision `NavRouter` and `NavRoute` in Studio (new method `suggestRooms`, new field `candidates`)" and "In the desktop preview type `rm 3335`, `restroom`, `2nd floor restroom`, `9999`: first routes, second lists three, third routes to Level 2, fourth offers suggestions".

Size: expect roughly 250-450 changed lines (router helpers, c3typ, vite action, api wrapper, RoomSearch, two node tests, jest cases).

## Open questions

- Whether the live Studio search action returns `[]` or throws on a floor-hint query: untestable offline, covered by the PR checklist item.
- Whether "mapped access point" in the `labelX` arrive step is intended student-facing copy: not changed here without Kush's say-so.
- `RoomFinder.py` has its own room resolution (from #5); unifying it with this matcher is a possible later milestone, not scoped here.
- Sweep numbers (528 routes, 0 "Turn around") are for main at 2373c9b; PR #6 changes `data/` and may move them.
