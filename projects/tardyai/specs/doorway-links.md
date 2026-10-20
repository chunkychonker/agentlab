# Spec: room routes enter through doorways (TardyAI milestone 6)

Repo: kushsarora/TardyAI, package `pkgbc231600`. Builds on main with #1-#5 merged.

## 1. Intent

Routes on Navigate and Room Finder still end by walking through a wall. The
hallway network follows the corridors since #4, but the last link of every
route goes from a `door` node to a `room` node in a straight line. Today that
line crosses a drawn wall for 61 rooms, and one hallway link
(`n-f3-n1250 -- n-f3-d3210`) crosses one too. Make every link enter through
the real wall opening.

Out of scope: new rooms, the 11 `not_mapped` suite rooms, router, UI and
Room Finder code, and changing the wall rule below.

## 2. Cause

`tools/build_graph.py` places each door node by projecting the room's label
point straight onto the nearest corridor segment. So the door always sits on
the corridor centreline directly "below" the label. Real doors are gaps in the
wall, often angled corner doors (1280, 1140, the 1170-1295 classrooms), so the
straight line from door to label passes through the wall beside the gap. The
existing `RoomRow.door` hint only picks which segment to project onto. It
cannot place the door.

Crossings per link run from 1 to 5. One crossing means the door is on the wrong
side of a single wall. Three or more mean the room node is deep inside a suite
and the line cuts several partitions. The hard cases are 1210, 1290A, 1425,
2020D, 2020H, 3161, 3163, 3165, 3337 and 3344.

## 3. Behavioural spec

**Inputs.** `FLOOR_PLANS` in `tools/grainger_corridors.py`, extended per room.
**Outputs.** Regenerated `data/MapNode`, `data/NavEdge`, `data/Waypoint`,
`data/AnnotatedPlan` JSON and the annotated PNGs (via `tools/build_waypoints.py`).

- New optional `RoomRow` field `doors: tuple[Point, ...]`, default empty.
  - Empty keeps today's behaviour, so rooms that already pass keep their ids
    and positions.
  - Each point is a door-node position in the corridor, directly in front of
    a gap in the room's wall.
  - The first door keeps id `d<number>`. A room with two openings gets
    `d<number>-2`, and so on.
  - The room keeps one `room` node, linked to each of its doors. The router
    needs no change.
- For each explicit door, the generator finds the nearest corridor segment.
  - If the door is within `DOOR_MIN_GAP_PX` of the segment, split the segment
    there, as today.
  - Otherwise, insert a foot node on the segment and add a `hallway` stub edge
    from the door to the foot, at most `MAX_DOOR_STUB_METERS` (8 m) long.
  - The stub counts as a corridor edge, so it must also pass the wall rule
    (section 5).
- `RoomRow.x, y` may move a few pixels inward, so door → room is a straight
  segment through the gap. Room edges stay at 15 m or less. A room links only
  to its own doors.
- Fix `n-f3-n1250 -- n-f3-d3210`: give 3210 an explicit door, and split or
  redraw that corridor chain if needed, so it crosses no wall.
- Failure modes, each a `ValueError` naming the room and raised before any file
  is written:
  - a door farther than the stub cap from every corridor
  - two doors of one room closer than `DOOR_MIN_GAP_PX`
  - a stub or room edge over its cap
  - an explicit door for an unknown or `not_mapped` room
- Invariants:
  - Every node id that exists today survives: `n-f*-r<num>`, connector ids
    and `d<num>`. `Waypoint` ids stay the same.
  - `connectorKey`s are unchanged.
  - `seconds` comes from the existing `_edge_rows` (about 0.77 s per metre at
    12 px/m).
  - The generator is deterministic: running it twice gives identical files.
- Don't hand-edit the JSON. The source of truth is `grainger_corridors.py`, so
  regenerating never undoes a fix.

## 4. Interfaces

```python
# tools/grainger_corridors.py
class RoomRow(NamedTuple):
    number: str
    x: float
    y: float
    name: str | None = None
    door: Point | None = None        # existing: segment-choice hint
    aliases: str | None = None
    doors: tuple[Point, ...] = ()    # NEW: explicit doorway positions

# tools/build_graph.py
MAX_DOOR_STUB_METERS: float = 8.0
DOOR_MIN_GAP_PX: float = ...         # pick and name once
def place_doors(room: RoomRow, segments, coords) -> list[tuple[str, Point, int]]: ...
```

Optional dev aid, `tools/find_doorways.py` (stdlib only, never run by tests):
for each failing room, scan open pixels within about 8 m of the corridor and
print candidate `doors=` points whose door → room line crosses zero walls,
shortest first. It only suggests. A person or agent checks a crop of the plan
and pastes the values into `grainger_corridors.py`.

## 5. The wall rule (what "passes" means)

Plans: `ui/react/public/plans/floor_p{1,2,3}.png`, 1980x1530, 12 px/m. Node
coordinates are in the same pixel space.

Pixel classes:
- `wall` if `max(r,g,b) < 110`
- `fill` (a coloured room) if `max - min >= 40`
- otherwise `open`

Sample one point per pixel of length along each edge, judging each node pair
once. An edge passes when:

1. **No wall crossings:** no run of 3 or more consecutive `wall` samples. Skip
   the first and last 2 m at a door or corridor node, and 4 m at a room node,
   because the room's label box is outlined in ink.
2. **Corridor edges** (neither end a `room` or `restroom`): at most 10% of
   samples are non-`open`, skipping 2 m at each end.
3. **Room edges:** 15 m or shorter.

Dark lines thinner than 3 px (door swings, hatching) are not walls.

The agentlab verify gate runs exactly this check as "routes stay in
corridors". On current main it fails with 62 wall-crossing lines and nothing
else.

## 6. Method

1. Run the check to get the 62 failing links.
2. Crop each failing room from its floor plan and look at where the door gap is.
3. Group rooms by corridor run. Most single-crossing rooms share one pattern
   (door projected onto the wrong side of the wall), so fix them per run, not
   one at a time.
4. Add `doors=` to each room, adjust `x, y`, then regenerate with
   `build_graph.py` and `build_waypoints.py`.
5. Rerun the check until it reports zero crossings.
6. If a room truly cannot pass without changing the rule, list it in the PR
   body. Prefer fixing it.

## 7. Acceptance criteria

1. The wall rule (section 5) reports zero violations, including
   `n-f3-n1250 -- n-f3-d3210`. The existing ui build, jest, and node and
   python tests all pass.
2. New `test/node/doorways.test.js` (`node --test`, stdlib), asserting on seed
   data:
   - every `room` and `restroom` node has at least one door edge
   - every door edge is 15 m or shorter
   - `n-f1-r1280`, `n-f1-r1140`, `n-f1-r1270` and `n-f3-r3335` exist
   - the 1310 → 1270, 1310 → 1280 and West Entrance → 3335 routes each end
     with a door-edge leg whose last point is the room node
3. New `test/py/test_build_graph_doors.py` (stdlib unittest):
   - a synthetic floor plan with one explicit door gives the expected nodes
     and edges
   - a door beyond the stub cap raises `ValueError` naming the room
   - building twice gives equal output
4. `rooms.test.js` (every printed room reachable) and `router.test.js` pass
   unchanged. `steps.test.js` expectations may change where geometry moved,
   for example the side 3335 is on. List each changed expectation in the PR
   body, one line each.
5. The PR attaches overlay crops of 1280, 1140 and 1270, with the route drawn
   over the plan, showing the last segment passing through the opening.
6. The PR checklist includes "Re-provision in Studio to reload seed data".

## 8. Risks and open questions

- **Effort:** about 60 rooms placed by eye. Mitigations: grouping by pattern,
  `find_doorways.py` suggestions, and the check as an automatic oracle.
- **Failure domain:** seed data only. A bad door breaks routes to that room,
  which `rooms.test.js` catches.
- **Margin:** the 4 m room-end margin could hide a crossing where a label sits
  far from the wall. The crops are the backstop.
- **Door ticks:** whether the extra door ticks clutter the map in Studio's
  Desktop and Mobile previews can't be checked offline.
