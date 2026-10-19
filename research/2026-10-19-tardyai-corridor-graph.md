# TardyAI milestone 4: routes follow the corridors

Date: 2026-10-19. Mode: external:tardyai (kushsarora/TardyAI). Checkout `/Users/steeb/agentlab-work/tardyai` at 3cbe22e (main, includes #1-#3).

## Question

How do we rebuild the Grainger nav graph so every edge stays on open corridor floor, every printed room number is a routable room with a door on a corridor, and `verify.sh` (incl. "routes stay in corridors") passes, in one PR?

## Findings

Method: read the seed data, `tools/build_graph.py`, `tools/build_waypoints.py`, the router tests, `checks/walkability.py`, and looked at all three plan PNGs; measured open-floor runs with numpy/PIL. No web research: this is local geometry and data, no external API facts. (hn-search not needed.)

1. **The graph is generated, not hand-edited JSON.** `data/MapNode`, `data/NavEdge` (and `Floor`, `Building`) come from `tools/build_graph.py`. It has a `SPINE` shared by all three floors (14 nodes, same pixel coordinates on every floor), a per-floor `PER_FLOOR` table (room, wing hallway nodes, each with one `connects_to`), and computes `meters`/`seconds` from pixels (12 px/m, 1.3 m/s, +3 s on `door` edges). Do the work there and regenerate; never hand-edit the JSON. `tools/` is not protected or hot.
   - `SPINE` is wrong for the floors it is shared across: the three plans differ.
   - `main()` writes `edgeType: "door"` for any non-hallway/entrance node link. Door nodes (new, `kind: "door"`) should get `hallway` edges to corridor nodes and a short `door` edge to the room.

2. **Why the checker fails today, measured on the plans.** Scanning open-floor runs down columns (open = not dark ink, not colour fill, matching `walkability.classify`), the main east-west corridor on all three floors is the band **y 768-803 (centreline about y=785)** from x about 400 to 1400. West of x=400 on L1/L2 it is **y 692-757** (centre about 725) plus the same 768-804 band. The seed spine runs at **y=712** and **y=862** instead:
   - y=712 between x=640 and x=1190 is inside the restroom blocks and the lab (L1: red restrooms x 665-1005, y 680-765), hence 48-84% blocked edges.
   - y=862 (hall-s1..s4) is inside classrooms 1250/1270/1280 and the stair column, hence the 93% and 100% edges.
   - x=500 is open from y 640 to 880 on L1/L2 (the stair/elevator column), so a north-south link exists there, but `n-f1-stair-w` at (520,772) is fine and should keep its id.
   Treat these numbers as a starting hypothesis to confirm on an overlay, not as final coordinates.

3. **Walkability rules the data must meet** (`checks/walkability.py`): corridor edges (neither end `room`/`restroom`) at least 90% open pixels, ignoring 2 m at each end (so a node inside an elevator box is fine); room edges at most 15 m (180 px) and exempt from pixel sampling; every node inside the 1980x1530 image. A `door` node is NOT a room kind, so its edges to the corridor are judged as corridor edges: put it on the centreline. Node pairs are judged once.

4. **Things that break if the builder is not careful (found by reading the code):**
   - `build_waypoints.py` labels every non-room, non-connector, non-restroom, non-entrance node via `hall_labels[node["id"]]`, and `hall_labels` only contains `kind == "hallway"`. A `door` node raises `KeyError`. Either skip `door` nodes there (they are routing geometry, not something the vision model needs) or give them no waypoint. Also do not create hundreds of hallway waypoints: they are printed on the annotated PNGs the Room Finder LLM reads. Door nodes should not be waypoints; keep hallway waypoints to junctions and bends.
   - The tool needs Pillow. `python3` on PATH here (`/opt/homebrew/bin/python3`) has no PIL or numpy; `/usr/local/bin/python3` has Pillow 12 and numpy 2.3. `verify.sh` itself uses stdlib only (`python3 -I`), so only the authoring and overlay steps need PIL.
   - `test/node/router.test.js` pins ids: `n-f1-stair-w`, `n-f1-elev-e`, `n-f3-r3335`, the single "West Entrance" node, and a 150-210 s ETA for West Entrance to 3335 in 2 legs. `steps.test.js` has fixtures tied to seed geometry. Corridor routing will lengthen paths; rough estimate from the plans is 200-220 m, about 2.7-3.4 min plus the stair hop, so the band may still hold. If a pinned expectation changes, update it and say why in the PR body (the milestone requires this).
   - `README.md` states "75 nodes, 156 edges". Update that sentence.
   - The annotated images under `public/plans/annotated/` and `data/Waypoint`, `data/AnnotatedPlan` (`waypointCount`) are generated from the graph by `build_waypoints.py`. Regenerate them after the graph, or Room Finder's labels point at moved or deleted nodes. Milestone 5 retires the LLM's path choosing, so this only needs to stay consistent, not be perfect.

5. **Room inventory is large.** Reading the plans, printed numbers (excluding single letters on L3, which are carrel/locker labels in the south wing) are roughly 60 on L1, 110 on L2 and 190 on L3 (L3 has suites like 3150A-Q, 3115A/B, 3210A-H, 3260A-J, 3264A/B, and long runs 3102-3124, 3415-3427, 3519-3547). Exact counts must be transcribed by the builder from the images into the test fixture. No OCR is available (no tesseract; plan text is vector outlines, as `build_graph.py` already notes), so reading is by eye. Practical method: crop and upscale tiles of each plan with a pixel grid overlay (PIL), read centroids per tile, write them into a compact table. Labels that are unreadable go in the PR body, not guessed.

6. **Automate the door, hand-author the rest.** The tedious part is per-room door placement. Let `build_graph.py` compute it: author corridor polylines per floor (nodes at junctions, bends and elevator/stair spurs) and, for each room, only its centre `(x, y)`. The generator projects the room centre onto the nearest corridor segment (pure geometry, no pixels), inserts a `door` node there (splitting that corridor edge), and asserts the room edge is at most 15 m. Then the checker, not the author, is the oracle. A room whose nearest corridor is across a wall will still pass the check, so add a per-room optional `door=(x, y)` override and look at the overlay for the demo rooms and any room that sits in an enclosed block (1070/1080 on L1, the carrel rooms on L3).

7. **Likely real corridor topology (hypothesis from the images).** L1: west outer strip at x about 110 running north-south (y 780 to 1240) with column markers; a southern lobby corridor y about 1235-1260 from the west entrance east to the orange doorway near x=500; the main east-west band as in finding 2; the x 440-470 and x 475-550 stair/elevator columns; the east wing north-south corridor near x 1400; the plenary 1310 reached from the lobby north of the east elevators (x 1300-1380, y 560-690). The seed puts the entrance at (250,1300), which is outside the drawn building footprint on L1 (the footprint edge is near y=1250); confirm and move it onto the entrance doorway. L2/L3 differ in the south and east wings (L3 has the long south-wing letter corridor x 700-840 and the east wing x 1550-1620).

## Build proposal

Covers milestone 4 only (graph follows corridors, every printed room is a node and a Waypoint).

**Intent.** Replace the eyeballed nav graph with one that follows the corridors on all three floors, gives every printed room a door on a corridor and a short room edge, and makes every room reachable, so routes stop walking through rooms. Out of scope: Room Finder logic (milestone 5), `NavRouter.js` changes, new connectors or buildings, the Studio agent's `WORKSPACE_SUMMARY.md` and `pkgbc231600.c3pkg.json` (protected), hot files (`.gitignore`, `App.tsx`, `FloorPlanMap.tsx`, `navigation.ts`, `Interfaces.tsx`: none are needed), step wording fixes (milestone 7).

**Files (in the checkout).**
- `tools/build_graph.py`: per-floor corridor polylines (replacing the shared `SPINE`), a room table, door projection, edge generation. Keep connector ids (`n-fN-stair-w`, `elev-wa`, `elev-wb`, `elev-e`) and their `connectorKey`s; keep node ids where the node survives (e.g. `r3335`, `r1310`, `entrance-w`, `hall-wmid`). A room's id stays `n-fN-r<number>`; new door ids `n-fN-d<number>`.
- `tools/build_waypoints.py`: skip `door` nodes (no `KeyError`), keep room waypoints for every room, keep hallway waypoints limited (do not label every bend).
- Regenerated: `data/MapNode/MapNode.json`, `data/NavEdge/NavEdge.json`, `data/Waypoint/Waypoint.json`, `data/AnnotatedPlan/AnnotatedPlan.json`, `data/BuildingFacts/facts-grainger.json`, `ui/react/public/plans/annotated/{1,2,3}.png`.
- `pkgbc231600/test/node/fixtures/printed-rooms.json` (new): `{ "1": ["1070", ...], "2": [...], "3": [...] }` transcribed from the plans.
- `pkgbc231600/test/node/rooms.test.js` (new): see acceptance.
- `test/node/router.test.js`, `steps.test.js`: only expectation updates forced by the new geometry, each noted in the PR body.
- `README.md`: node and edge counts sentence.

**Interfaces.** Generator side (Python, stdlib plus `random`-free, deterministic):
```python
Corridor = list[tuple[str, float, float]]          # (local_id, x, y), consecutive items are linked
RoomRow = tuple[str, float, float, str | None, str | None]  # (number, x, y, name, door_override as "x,y" or None)
def project_to_corridor(px: float, py: float, segments) -> tuple[float, float, int]  # door x, y, segment index
def build_floor(level: int, corridors: dict[str, Corridor], rooms: list[RoomRow]) -> tuple[list[dict], list[dict]]  # nodes, edges
```
Failure mode: `build_floor` raises `ValueError` naming the room when its door-to-room edge exceeds 15 m, instead of emitting an edge the checker will reject.

**Behavior (acceptance, all checkable).**
1. `bash projects/tardyai/verify.sh /Users/steeb/agentlab-work/tardyai` prints `VERIFY: PASS`, including "routes stay in corridors" and "seed data integrity". Today the first fails with 31 corridor edges and 15 room spurs.
2. Every room edge is at most 15 m, and every `room` node has exactly one edge pair, to its own `door` node. (`rooms.test.js`, from the seed JSON.)
3. Every number in `printed-rooms.json` is a `room` node with `roomNumber` and has a matching `Waypoint` row with the same label, and `findRoute(entrance-w, <room>, false)` returns `found: true` for all of them (table-driven, via the existing `vm` harness on seed data).
4. Same set routable step-free from the entrance (or listed in an explicit, justified exception list in the test; rooms only reachable by stairs must be real, not a data gap).
5. Elevator and stair nodes and their `connectorKey`s are unchanged: the demo and step-free tests in `router.test.js` still find Elevator C and the West Stairs. ETA bounds in those tests may be adjusted only with a note in the PR body.
6. 1310 to 1270 on floor 1: `findRoute(n-f1-r1310, n-f1-r1270, false)` is found and its polyline passes the same open-pixel test as the verify step (reuse the walkability sampler logic or assert `legs[*].points` are only corridor/door nodes). Attach an overlay PNG of that route (and of the West Entrance to 3335 demo route) to the PR.
7. `seconds` for every edge equals `meters / 1.3` (+3 for `door` edges) within rounding, with `meters` from pixel length at 12 px/m.
8. `node --test` on `steps.test.js` still passes, with fixture changes listed in the PR body one by one. No hot or protected file is touched; the diff is data, tools, tests, README.

**Process note for the builder.** Work floor by floor and keep the overlay loop tight: render the graph over the plan with `/usr/local/bin/python3` (Pillow), look at it with the Read tool, run `walkability.py` after each floor. Build L1 first (it contains the user's failing 1310 to 1270 case), then L2, then L3. If time runs out on the L3 room long tail, report the milestone `in-progress` with the exact rooms left; do not ship it as done, and do not guess unreadable numbers.

**PR checklist additions.** "Re-provision in Studio to reload seed data" (MapNode, NavEdge, Waypoint, AnnotatedPlan, BuildingFacts), then in Desktop and Mobile preview route 1310 to 1270 and West Entrance to 3335 and confirm the drawn line stays in hallways.

## Open questions

- Whether Studio's `upsertSeedData` handles about 1500 nodes and 6000 directed edges at once, and whether `NavRouter.findRoute` loading the whole graph per call (`limit: -1`) is still fast; not testable offline. Node count after this milestone is roughly 3x today's.
- Exact entrance doorway coordinates and where the plenary 1310 is entered; both need a zoomed look at the plan.
- Whether the annotated PNGs stay legible to the vision model with several hundred room labels; milestone 5 reduces the dependence, but until then the builder should check the L3 image (about 190 labels at 1500x1159).
- Plan dates: the PDFs are printed 2021-09-15; room numbers may differ from the building today.

## Resume addendum (cycle 2026-10-19_151057)

Milestone 4 was resumed, not re-researched: the earlier cycle's work did not ship (no open PR on kushsarora/TardyAI, `origin/main` still 3cbe22e, checkout pristine). The proposal above stands unchanged. Hot files this cycle (`.gitignore`, `WORKSPACE_SUMMARY.md`, `App.tsx`, `FloorPlanMap.tsx`, `navigation.ts`, `Interfaces.tsx`) are still not needed; `tools/`, `data/`, `test/node/`, `README.md` and `public/plans/annotated/` are free. The hn-search MCP server failed to connect this cycle; no HN lookup was needed (local geometry work).

Scoping advice to the builder: if the L3 room long tail cannot be finished, ship L1 and L2 complete only if `walkability.py` passes on all three floors, since the gate is all-or-nothing. A partial L3 must still be a valid corridor graph with the rooms it does cover, and the PR body must list the printed numbers not mapped.
