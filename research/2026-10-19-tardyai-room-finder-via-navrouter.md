# TardyAI milestone 5: Room Finder routes through NavRouter

Date: 2026-10-19. Mode: external:tardyai (kushsarora/TardyAI). Checkout `/Users/steeb/agentlab-work/tardyai` at 8806cbf (origin/main, includes #1-#4). Covers milestone 5 only. Milestone 6 (room entry) shares the "resolve a typed room" helper but stays separate (see Open questions).

## Question

How do we make `RoomFinder.findRoute` stop trusting the vision LLM to choose the path, so "I'm on floor one in 1310, how do i get to 1270" returns a drawable route from the same A* router Navigate uses, and an unknown room gives suggestions instead of "Directions only"?

## Findings

Method: read `src/RoomFinder.py` (1113 lines), `src/VisionRoute.c3typ`, `src/NavRouter.js`/`.c3typ`, `src/NavRoute.c3typ`, `RouteLeg`/`RoutePoint`/`RouteStep`, the seed `Waypoint`/`AnnotatedPlan` data, `test/node/harness.js` and `projects/tardyai/verify.sh`. No web research: this is local code and C3 platform behaviour already in the repo. (hn-search MCP was down; not needed.)

1. **Where it fails today.** `findRoute` (RoomFinder.py ~L729-945) sends facts + three annotated PNGs + the sentence to the model, which returns `route: [labels]`. `_resolveRoute` (L435) rejects the whole route if one label is unknown or a floor change is illegal. After one retry it returns `ok` with `drawable=False` and the message "I could not place this route on the plans, so here are the directions in words only." That is the user's failure. The path quality depends on the model reading dots off an image, which no graph fix can cure.

2. **The router already has everything needed, in the right shape.** `NavRouter.findRoute(startNodeId, destNodeId, stepFree)` returns `NavRoute {found, message, legs[], totalSeconds, totalMeters, floorsTraversed, ...}`. Each `RouteLeg` has `floorId` ("grainger-f1"), `floorLevel`, `floorName`, `points: [RoutePoint{nodeId,x,y,kind}]`, `instruction`, and `steps: [RouteStep{kind,text,meters,seconds,pointIndex}]` (from #3). Python calls it as `c3.NavRouter.findRoute(a, b, stepFree)` (js type called from py; the existing file already uses `c3.<Type>.fetch`).

3. **Coordinate scale: no conversion needed if you draw from the router.** `MapNode`/`Waypoint` pixel coordinates are in floor-plan space (1980x1530): e.g. Waypoint `1270` is (679, 872), `E1` L1 is (400, 748), and `AnnotatedPlan.imageWidth/Height` is 1980/1530 while `annotatedWidth/Height` is 1500x1159. `_buildSegments` already stamps `imageWidth/imageHeight` (the 1980 values) and `planImageUrl` on each segment, and the client draws `RouteWaypoint.imageX/Y` on that plan. So use `RoutePoint.x/y` as `imageX/imageY` untouched. The 1500x1159 images are only for the LLM prompt; if the builder ever draws onto an annotated image it must scale by 1500/1980 (= 0.7576) on x and 1159/1530 (= 0.7575) on y. Recommend not doing so.

4. **Mapping NavRoute to the existing `VisionRoute` shape (additive, no field removed).**
   - `route`/`segments[].points` (`RouteWaypoint`): one per `RoutePoint`. `label` is required (`!string`): use the `Waypoint.label` for the node when one exists (room number, "S1", "ENT-W"), else the `nodeId`. Build a `sourceNode -> Waypoint` map (the `Waypoint.sourceNode` field exists in the seed data; `_loadWaypoints` currently omits it from `include`, so add `sourceNode`). `role`: first point `start`, last `end`, connector nodes `connector`, rest `via` (the existing vocabulary, see `_resolveRoute` L91-99). `isFloorChange` true on connector points that begin/end a leg.
   - `segments`: one per `RouteLeg`. `floorKey` needs mapping from `leg.floorId` ("grainger-f1") to the plan's `floorKey` ("1"); `_loadPlans` does not fetch `floor.id` today, so add it to the include and key `plansByFloorId`. `stepIndexes` = the indices of that leg's steps in the flat list.
   - `steps`/`stepFloorKeys`: flatten every `leg.steps[].text` (fall back to `leg.instruction` if a leg has no steps); `stepFloorKeys[i]` = that leg's `floorKey` (exact, not a guess, which is better than the LLM path's attribution).
   - New optional fields (expand, don't contract): `source` gets the new value `"router"` (the c3typ doc says `llm`/`cache`; update the doc comment, the UI only branches on `cache`), plus `totalSeconds`, `totalMeters` and `suggestions: [string]` on `VisionRoute`. Adding fields means re-provisioning `VisionRoute` in Studio, which goes on the PR checklist.
   - `drawable` is True whenever `found` is True. `status` `ok`. Tokens/model fields stay 0/model name from the extraction call.

5. **The LLM's new, narrow job.** One text-only call (no images, no cache prefix, tiny prompt) via the same `c3.GenaiCore.Llm.Completion.Client.forConfigKey("default-completions")` and `client.completion(messages=..., options={"returnJson": True, "max_tokens": ~300})` already used in `findRoute`. Required JSON: `{"start": {"type": "room"|"entrance"|"landmark"|"unknown", "text": str, "floor": str|null}, "destination": {"text": str}, "stepFree": bool|null}`. Validate at the boundary (the existing `_parseJsonContent` L311 handles fenced JSON/truncation); a malformed reply gets one retry then an `error` VisionRoute with a plain message, never "Directions only". Everything after extraction is deterministic Python: the model never sees or emits a label.
   - Allowing the pre-LLM fast path: if a regex finds two room numbers ("from 1310 to 1270"), still call the model (keeps behaviour simple), but a pure `extract_with_regex` fallback when the client raises keeps the demo alive: `r"(?:from|in|at|near)?\s*(\d{4})\b ... (?:to|get to)\s*(\d{4})"`. Optional stretch; the acceptance below does not require it.

6. **Resolving text to a `MapNode` (pure Python, no c3).** Normalize: lower-case, strip, drop a leading `rm|room|#|no.`, collapse spaces, drop a trailing letter suffix only as a second-try ("3335a" -> "3335"). Order: exact `roomNumber` match -> exact name/alias match (West Entrance, "west stairs", "elevator c") -> none. Entrances and landmarks resolve from `MapNode.name`/`searchAliases` (comma string) and `kind in {"entrance","stairs","elevator","restroom"}`. Start with a floor but no room ("I'm on floor 2") has nothing to route from: ask for a room. Source of candidates: `Waypoint` rows (already loaded, carry `roomNumber`, `name`, `searchAliases` list, `sourceNode`) so the resolver needs no extra fetch and needs no JS.
   - Suggestions for an unknown room: pure function `nearby_rooms(text, roomNumbers, limit=3)`. Rank by (a) same digit-string edit distance <= 1-2 (typo, e.g. "1207" -> "1270"), then (b) same floor digit and smallest numeric distance. Return at most 3, deterministic order, never the empty string. Message: `I can't find room X on these plans. Did you mean 1270, 1280 or 1250?`; `status "ok"`, `drawable False`, `message` set, `suggestions` set, `steps` empty. (Do not use `clarify`, whose `question` is a chat bubble and which the UI already renders; it is a reasonable reuse. Builder decides, but must not use `error`: the UI shows a red failure for `error`. Recommend `clarify` with `question` = the "Did you mean" text plus `suggestions` populated, because the RoomFinder page already renders `clarify` as a chat bubble (RoomFinder.tsx ~L241).)
   - Missing start -> `clarify` with `question` "Where are you starting from? Tell me a room number or an entrance."

7. **Python tests have no home or gate yet.** `verify.sh` runs `c3_parity`, seed integrity, walkability, `npm ci`/build/jest and `node --test`; there is no Python test step, and `RoomFinder.py` uses a global `c3` that is injected by the platform (no `import c3`), so a test must `exec(open("RoomFinder.py").read(), ns)` with `ns["c3"]` a stub. Two consequences:
   - The builder adds a `python3 -I -m unittest discover` step ("room finder tests (python)") to `projects/tardyai/verify.sh` (agentlab-owned, not a protected file) that runs `pkgbc231600/test/py/test_*.py`, and fails (not passes) if the dir exists with no tests, mirroring the node step's vacuous-pass guard. Use `python3 -I` plus an explicit path; with `-I` the cwd is not on `sys.path`, so the test locates `RoomFinder.py` by `Path(__file__).resolve().parents[2] / "src"`.
   - "node sequence equals `NavRouter.findRoute`" needs the real router. Python cannot run JS, so the test's fake `c3.NavRouter.findRoute` shells out to Node: a small `test/py/router_oracle.js` that loads `test/node/harness.js` (the existing `vm` harness) and prints `JSON.stringify(findRoute(a,b,sf))` for a pair. Use `subprocess.run(["node", oracle, a, b, sf], check=True)`; Node is already a hard requirement of verify. Then assert on the real router's output, not a hand-written stub (the point of the milestone is that Navigate and Room Finder agree). Check `harness.js` exports before writing the oracle; I read only its seed/fetch helpers, not its export list.

8. **Seed facts for the tests (verified in `data/`).** Node ids: `n-f1-r1270`, `n-f1-r1310`, `n-f3-r3335`, `n-f1-entrance-w` (label `ENT-W`, name "West Entrance"). Waypoint labels equal room numbers. 254 room waypoints, 3 stairs (S1 per floor), 9 elevators, 1 entrance. `RouteQuery` logging (`_logQuery`) wraps its own try/except; reuse it unchanged so pinned-answer and logging behaviour remain.

9. **Things that must not break (expand/contract).** Keep `_buildMessages`, `_resolveRoute`, `_pinnedAnswer`, the prompt and `diagnostics` in the file; the milestone changes `findRoute`'s body only. The vision path becomes dead for the happy path; either delete it later (separate PR) or keep it as a last-resort fallback when extraction fails. Recommend: keep, call it only if extraction returns unusable JSON twice and `allowFallback` is true (so pinned replay still works for an outage). Dropping it here would be a breaking change to the pinned-answer feature (`pinRoute`/`diagnostics` still reference plans).
   - Hot/protected files: none touch this. `RoomFinder.py`, `VisionRoute.c3typ`, `RoomFinder.c3typ`(doc only) and new test files are free. The UI file `RoomFinder.tsx` is not required for acceptance; `ui/react/src/Interfaces.tsx` is the Studio agent's hot file, so if a `suggestions` TS field is wanted put it in `src/lib/` and read `suggestions` defensively.

## Build proposal (milestone 5, one PR)

**Intent.** Make Room Finder route with `NavRouter` instead of model-chosen labels, so the answer to a typed request is deterministic, drawable, and identical to Navigate. Out of scope: changing `NavRouter.js`, the graph, the image prompt, room-entry fuzzy typing in Navigate (milestone 6), `snapToGraph` (7), UI redesign.

**Files (all in the checkout `pkgbc231600/`, none protected or hot):**
- `src/RoomFinder.py` edit. Add private pure helpers (no `c3` inside): `_normalizeRoomText(text) -> str`, `_resolveEndpoint(text, waypoints) -> Waypoint|None`, `_nearbyRooms(text, roomNumbers, limit=3) -> list[str]`, `_parseExtraction(parsed) -> Extraction|ErrorStr`, `_visionRouteFromNavRoute(navRoute, waypointsByNode, plansByFloorId) -> dict`. Edge code: `_extractEndpoints(client, message)` (the one new model call). `findRoute` calls them in order; falls through to the old vision path only per point 9.
- `src/VisionRoute.c3typ` edit: add `suggestions: [string]`, `totalSeconds: double`, `totalMeters: double`; update the `source` doc to include `router`. Additive only.
- `test/py/test_room_finder.py`, `test/py/router_oracle.js` new (+ small `test/py/fixtures/` if needed).
- `projects/tardyai/verify.sh` (agentlab repo): add the Python test step.
- `README.md` one line.

**Interfaces (stubs).**
```
@dataclass(frozen=True) class Endpoint: kind: str; text: str; floor: str | None
@dataclass(frozen=True) class Extraction: start: Endpoint | None; destination: Endpoint; step_free: bool
def _parseExtraction(parsed: dict) -> Extraction            # raises ValueError on bad shape
def _normalizeRoomText(text: str) -> str
def _resolveEndpoint(text: str, waypoints: list[dict]) -> dict | None
def _nearbyRooms(text: str, roomNumbers: list[str], limit: int = 3) -> list[str]
def _visionRouteFromNavRoute(nav: dict, wpByNode: dict, plansByFloorId: dict) -> dict
```
Failure modes: model unreachable or unusable JSON twice -> existing pinned-answer fallback, else `error` with the message; unknown start or destination -> `clarify` + `suggestions`; `found: False` from the router -> `ok`, `drawable False`, `message` = the router's message (never "Directions only"); stepFree defaults to false unless the model or the caller says so. Idempotent and read-only except `RouteQuery` logging (already best-effort).

**Acceptance (all checkable offline via the new Python step):**
1. With a fake completion client returning `{"start":{"type":"room","text":"1310","floor":"1"},"destination":{"text":"1270"}}`, `findRoute("grainger", "I'm on floor one in 1310, how do i get to 1270", True)` returns `status "ok"`, `drawable True`, `source "router"`, and the list of node ids behind `route` equals the nodes in `NavRouter.findRoute("n-f1-r1310","n-f1-r1270",False)` from the real router (via the Node oracle). Note: 1270 is on `main` since #4, so this works now (the PLAN's "arrives with milestone 4" caveat is obsolete); also assert the second pair 1310 -> 1170 as the PLAN requested.
2. Same for "from the west entrance to 3335" (`n-f1-entrance-w` -> `n-f3-r3335`, two segments, floorKeys "1" and "3") and for a cross-floor room-to-room request (e.g. 1310 -> 3335).
3. `segments[i].imageWidth == 1980`, point coordinates equal the router's `RoutePoint.x/y` exactly, `len(steps) == sum(len(leg.steps))`, `len(stepFloorKeys) == len(steps)`, every `stepIndexes` partition covers each step exactly once.
4. Unknown room ("get to 9999", "get to 1207"): `status "clarify"` (or `ok`/not drawable per the builder, but never `error`, never contains "Directions only"), `suggestions` has 1-3 existing room numbers, and "1207" suggests "1270". No router call is made for it (assert the fake counts calls).
5. Missing start (`start` null): `clarify` with a question asking where they are.
6. Typed forms "rm 1270", "Room 1270", "#1270", " 1270 ", "1270A" resolve to the same node (table-driven).
7. Client raising or returning garbage twice: no exception; falls to pinned answer when `allowFallback` else `status "error"`.
8. The old vision tests, if any, and `VERIFY: PASS` including `c3 methods implemented` (no new `.c3typ` method is declared, so parity is unaffected) and the new Python step.

**PR checklist additions:** re-provision `VisionRoute` (new fields) in Studio; confirm the `default-completions` config exists; try "I'm in 1310, how do I get to 1270" in Room Finder on desktop and mobile and see the line drawn on Level 1, and Navigate for the same pair shows the same steps.

## Open questions

- I did not read `harness.js` past its seed/fetch helpers, so whether it exports a callable `findRoute` factory for the oracle is unconfirmed; the builder should check and, if needed, add a tiny export rather than edit router tests.
- Whether the UI treats `clarify` as acceptable for "unknown room" is inferred from RoomFinder.tsx line hits (clarify and error branches exist); not rendered or run.
- Milestone 6 also wants normalization of typed rooms in `NavRouter.routeToRoom` (JS). The Python resolver here duplicates that logic in a second language; a later cycle could move it into one place. Bundling 6 here would double the PR and mix a JS router change with a Python change, so I did not.
- `Waypoint.searchAliases` is a list while `MapNode.searchAliases` is a comma string; the resolver should use the Waypoint list. Not tested against live C3 data types.
- Real LLM extraction quality (e.g. "floor one" -> "1") is untestable offline; the prompt must be eyeballed in Studio.
