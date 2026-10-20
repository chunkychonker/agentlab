# TardyAI milestone 6 (rescoped): clear the last wall crossings after the owner's routing rewrite

Date: 2026-10-20. Mode: external:tardyai (kushsarora/TardyAI). Checkout `/Users/steeb/agentlab-work/tardyai` at origin/main 2373c9b. No web research: a data/gate milestone, so the sources are the checkout and the plan PNGs. (The hn-search MCP was down this session; it was not needed.) This note SUPERSEDES the build proposal in `research/2026-10-19-tardyai-doorway-links.md`, whose measurements are stale.

## Question

Main moved after yesterday's note. What does `VERIFY: routes stay in corridors` actually fail on now, and what is the smallest change that makes it pass without hiding real wall crossings?

## Findings

1. **Main changed under us (Kush, 2026-10-19 16:27-17:34, merges 90a0704 / 2373c9b).** `tools/build_graph.py` now sets every room node to its door's coordinates (`coords[local_id] = coords[door_id]`) with a zero-length `door` edge (3 s allowance) and keeps the printed position in `labelX/labelY`. `MAX_ROOM_EDGE_METERS` is gone from the generator. So the 61 room-link wall crossings from yesterday cannot occur any more: a room link has no length. The new tests (`test/node/geometry.test.js`, `fixtures/route-obstacles.json`) assert this. Yesterday's plan (`RoomRow.doors`, stub edges, moving room x/y inward, `find_doorways.py`) is obsolete. Do not build it.
2. **Kush also added Learning Commons circulation** (connectors `elev-commons`, `stair-commons`; points `commons-*`, `commons-office-*`, `lab-*` in `tools/grainger_corridors.py`, floors 1-3). Routes now pass through the open Learning Commons and the Huber lab.
3. **Gate state now.** `python3 -I projects/tardyai/checks/walkability.py pkgbc231600` prints 35 violations: 31 "corridor edge ... N% crosses rooms or walls" and 4 wall-crossing edges (one also double-reported as a fill violation). `verify.sh` reports `VERIFY: FAIL (1): routes stay in corridors`; every other step passes (27 py tests ok, 3 skipped).
4. **The 31 fill violations are a gate false positive, not a data bug.** I cropped `floor_p1/2/3.png` around the commons (x 640-1130, y 230-640). Each floor is tinted with one solid colour: (159,126,255) purple on floor 1, (255,159,207) pink on floors 2 and 3. It is the open Learning Commons / Huber lab / atrium floor. Classrooms are hatched (green crosshatch, e.g. 2210M/N) and outlined in black walls; elevators are cyan; restrooms red/blue. `walkability.classify` calls any pixel with channel spread >= 40 `fill`, so the solid tint counts as blocked and every edge across the Commons is "100% rooms". Yesterday's decisions-log entry already anticipated this ("needs ... a gate rule for filled rooms; that is a follow-up").
5. **Tested the gate fix without touching the repo.** I copied `checks/` to `/tmp/chk`, made `classify` return `open` for fill pixels within 40 per channel of those two tints, and re-ran against main. 31 violations drop to 7 lines covering 5 edges, all genuine (walls are dark pixels and are still counted separately, so a tint pass-through never hides a wall):
   - `n-f2-commons-south -- n-f2-elev-commons`: 100% blocked. The elevator node (870,520) sits at the shaft; the cyan shaft pixels are still `fill`. Probably the edge ends inside or across the cyan box beyond the 2 m endpoint margin. Builder to look at the crop and move the node or split the edge.
   - `n-f2-commons-office-w -- n-f2-d2210S` (1 wall, 21%) and `n-f2-d2210S -- n-f2-d2210R` (1 wall, 21%): the 2210S/2210R door nodes (door hints (920,580) / (1020,580)) are projected onto a segment (`commons-south -- commons-south-e`, y 580) that runs through the south wall of 2210M/N. Fix the corridor in `tools/grainger_corridors.py` or the door hints.
   - `n-f2-commons-west-n -- n-f2-d2313`: 3 walls. 2313 (1205,335) has no hint, so its door lands on a segment that crosses the 2210E-L office block walls. Needs `door=` pointing at the north-east corridor.
   - `n-f3-d3303 -- n-f3-n660`: 2 walls. 3303 (1315,613) door projected onto the chain ending at `n660` (660,628), a long hop across the east wing partitions. Needs a `door=` hint or a corridor point near x 1315.
6. **Protected/hot.** Not touched: `WORKSPACE_SUMMARY.md`, `pkgbc231600.c3pkg.json`. No hot files (none listed this cycle). `README.md` was rewritten by Kush; leave counts to the PR body.
7. **Pillow python** `/usr/local/bin/python3` (3.13) has Pillow 12.0.0 (confirmed this cycle). Stdlib-only for `build_graph.py` stays.
8. **Other open item.** The generator docstring no longer claims the gate enforces anything. The gate lives in agentlab (`projects/tardyai/checks/`), the PR lives in Kush's repo; the gate change cannot ride the PR.

## Build proposal

Covers milestone 6 only, rescoped. Two commits' worth of work in two places. One theme: make the corridor gate tell the truth about the new graph.

**Intent.** (a) Teach `checks/walkability.py` that the floor's solid tint is open floor (walls are still counted), and (b) fix the genuine wall crossings left on main by regenerating from `tools/`. Out of scope: moving room nodes (rooms are now zero-length), `RoomRow.doors`, `find_doorways.py`, router/UI/Room Finder code, README, `unmapped-rooms.json`.

**Behavioral spec.**
- Part A, in agentlab (not in the PR): `classify(r,g,b)` returns `"open"` for a pixel whose every channel is within `TINT_TOLERANCE = 40` of a tint in `OPEN_FLOOR_TINTS = ((255,159,207), (159,126,255))`. Wall (all channels < 110) is tested first, so walls are unchanged. All other fills (green hatch, cyan, red, blue) stay `fill`. Add unit cases to `checks/test_checks.py`: tint pixel is open; antialiased tint (255,170,212) is open; classroom green (179,255,155) is fill; (0,191,255) is fill; black is wall; a synthetic edge across tint passes the 90% rule but still reports a wall crossing when a dark run of >= 3 px lies on it. Update the module docstring (fills: "room fills block, the floor-wide commons tint does not") and note the tint values come from the plan renderer (`tools/render_plans.py` in the target), not guesses.
- Part B, in the checkout: fix the five edges in item 5 via `tools/grainger_corridors.py` (and `tools/build_graph.py` only if the elevator case needs it), regenerate with `python3 tools/build_graph.py` then `/usr/local/bin/python3 tools/build_waypoints.py`. Never hand-edit the JSON. Keep every existing node id, `connectorKey` and room number; `unmapped-rooms.json` unchanged.
- Invariants: generator output idempotent; room nodes still sit at their door (`geometry.test.js` passes unchanged); `router.test.js`, `rooms.test.js`, `steps.test.js` pass. If a `steps.test.js` expectation changes, list it in the PR body.
- Failure modes: if a crossing cannot be fixed without leaving the corridor, say so in the PR body and add a decisions-log entry; never loosen `MIN_WALL_RUN_PX` or the wall margins to get green. If the tint fix hides something real, the wall rule is the backstop; add one regression row to `route-obstacles.json` only if a visual check finds a shortcut through a tinted-but-enclosed room.

**Interfaces.**
```python
# checks/walkability.py (agentlab)
OPEN_FLOOR_TINTS: tuple[tuple[int, int, int], ...]
TINT_TOLERANCE: int = 40
def classify(r: int, g: int, b: int) -> Pixel: ...   # unchanged signature
```
Target side: only per-room `door=` hints and corridor point/segment edits in `FLOOR_PLANS`.

**Acceptance criteria.**
1. `bash projects/tardyai/verify.sh /Users/steeb/agentlab-work/tardyai` prints `VERIFY: PASS`, including "routes stay in corridors" and "seed data integrity".
2. Running the OLD gate logic (git stash of Part A) on the NEW data still fails only on tint-related edges, not on the five wall edges. In practice: `walkability.py` with Part A reports zero wall crossings.
3. `python3 -I projects/tardyai/checks/test_checks.py` (however that file is invoked today) passes with the new classify cases.
4. Overlay crops (route drawn over the plan) for 2210S, 2313 and 3303 attached to the PR, last segment entering the corridor beside the door, not crossing a wall. The PR checklist adds "Re-provision in Studio to reload seed data". The PR body names the 5 edges fixed, the gate change (that it lives outside this repo), and that the room links are now zero-length by design.
5. PLAN milestone 6 text is stale ("room links cross a wall", "attach crops of 1280/1140/1270"); the builder marks it done with a pointer to this note.

**Risk.** Low-medium. The tint rule widens "open" for pink/purple floor on every edge, so correctness now leans on wall detection. Mitigation: wall counting is unchanged and independent, `geometry.test.js` obstacle rectangles remain a second oracle, and the builder eyeballs the three crops.

## Open questions

- Whether the elevator-shaft edge (`commons-south -- elev-commons`) is a data bug or a gate issue with cyan shaft pixels; I saw only the violation line and a crop of floor 3, not floor 2's shaft close-up.
- Whether Kush wants the gate's tint list maintained in his renderer; today it is two literals derived by sampling the plans.
- The PR cannot contain the gate change, so the Part B PR alone fails the gate on a fresh agentlab checkout until Part A lands; ordering is Part A (agentlab commit) then PR.
