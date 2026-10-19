# TardyAI milestone 3: turn-by-turn directions (`RouteStep`)

Date: 2026-10-19. Mode: external:tardyai (kushsarora/TardyAI). Checkout `/Users/steeb/agentlab-work/tardyai` at 6c0ecfe (router-tests PR branch, so the `vm` harness exists).

## Question

How do we turn each `RouteLeg` polyline into followable steps ("Turn right at 3110", "3335 is on your left") inside the unmodified-platform `NavRouter.js`, show them in the Navigate UI, and test it without touching a hot file?

## Findings

Method: read `src/NavRouter.js` (`assembleLegs`), `RouteLeg.c3typ`, the UI under `ui/react/src`, and ran the router on seed data through the existing harness. No web search this cycle: the question is local code and geometry, and the hn-search MCP is down. No external API facts are needed.

1. **Where legs are built.** `assembleLegs(path, adjacency, nodesById, destNode)` walks the A* node path once, picks the cheapest `chosen` edge per hop, and adds `chosen.meters` / `chosen.seconds` to the leg. Vertical hops (`chosen.viaConnector`) end the leg and are not counted in `leg.meters`. Steps should be built in the same loop, from the same `chosen` edges, so step meters sum to `leg.meters` exactly (the acceptance bound is +-1). Do not recompute from pixel distance: authored edge meters may differ from pixels/ppm.

2. **Seed demo route** (West Entrance L1 to 3335, default):
   - Leg 1 (Level 1, 60.4 m): entrance (250,1300) -> hall-s1 (520,862) -> hall-wmid (520,712) -> stair-w (520,772).
   - Leg 2 (Level 3, 123.9 m): stair-w (520,772) -> hall-wmid (520,712) -> elev-wb (615,712) -> hall-c1 (760,712) -> c2 -> c3 -> hall-e (1330,712) -> elev-e (1410,712) -> r3335 (1478,180).
   - Step-free: 122.8 m along y=862 then north to elev-e, then elev-e -> r3335 on L3.

   Consequences for the design:
   - The stair node is a **spur**: leg 1 overshoots hall-wmid and doubles back 60 px (about 5 m). The turn rule must handle a ~180 degree reversal ("Turn around"). Do not fix the seed data or the router's path choice here.
   - Hallway runs on y=712 are many collinear nodes, so the merge rule is what makes this readable. Nodes `elev-wb`, `elev-e` are connector nodes sitting inline in the hallway; they are landmarks but not turns.
   - The final hop (elev-e to the room node) is about 44 m and perpendicular to the hallway. The room is a leaf node, so "which side" must come from the turn into the last hop: heading east then heading north = room on the left.

3. **Geometry (get this right once, test it twice).** Image y grows downward. For headings h1=(dx1,dy1), h2=(dx2,dy2), `cross = dx1*dy2 - dy1*dx2`. On screen, cross > 0 is a **right** (clockwise) turn, cross < 0 is left. Check: east (1,0) then south on screen (0,1): cross = +1, and that is visually a right turn. Math axes would flip it. Angle = `atan2(|cross|, dot)`. Under 30 degrees: collinear, merge. Over 150 degrees: reversal. Compare each hop to the previous **hop**, not to the step start, so a gentle curve is not merged into one long step beyond 30 degrees of cumulative drift... accept this simplification and note it in the code.

4. **Platform constraints.** `NavRouter.js` is plain top-level `var`/`function` with no exports; the new helpers go in the same file (no `require`). Keep ES5-style (`var`, no arrow functions, no template literals), matching the file. C3 types: add `RouteStep.c3typ` and an optional `steps: [RouteStep]` on `RouteLeg.c3typ`. `projects/tardyai/checks/c3_parity.py` checks that c3typ methods are implemented; a new data type adds no method, so it should pass. The builder must run it to confirm. This needs a Studio re-provision, so it goes on the PR checklist.

5. **UI state and hot files.** Hot this cycle: `App.tsx`, `FloorPlanMap.tsx`, `navigation.ts`, `Interfaces.tsx`, `package-lock.json`. Not hot: `src/pages/Navigate/RouteSteps.tsx` (the per-leg list, today showing only `leg.instruction`), `Navigate.tsx`, `src/lib/tardyApi.ts`. Consequences:
   - The TS type for `RouteStep` cannot go in `Interfaces.tsx`. Put it in a new `src/pages/Navigate/routeStepTypes.ts` and declare `type RouteLegWithSteps = RouteLeg & { steps?: RouteStep[] }` there.
   - `tardyApi.normalizeRoute` already spreads each leg (`...leg`), so `steps` passes through with no edit. The new component should still use `leg.steps ?? []` and fall back to `leg.instruction` when empty (old server, or not yet re-provisioned).
   - `RouteSteps.tsx` is the natural place for the UI: render the numbered steps under each leg's instruction, with per-step distance. No wiring in the hot files is needed.

6. **Jest is untested ground.** There are no jest tests in the repo today (`"test": "jest --passWithNoTests"`). `jest.config.ts` has `ts-jest`, `jsdom`, `collectCoverageFrom: src/**/*.tsx` and **no `@/` alias in `moduleNameMapper`**. `RouteSteps.tsx` imports `NavRoute` from `@/Interfaces`; if it is a type-only import ts-jest elides it, otherwise it fails to resolve. The builder should use `import type` and, if anything else fails, add a `'^@/(.*)$': '<rootDir>/src/$1'` mapping to `jest.config.ts` (not protected, not hot). `lucide-react` icons must also load under jest; I did not run this. If the render test cannot be made to work cheaply, that is the first thing to report, not to skip silently.

## Build proposal

Covers milestone 3 only (backend and UI of the same capability). Milestone 4 (room entry) is a different theme and stays separate.

**Intent.** Give every `RouteLeg` a `steps` array that a late student can follow without reading the map, computed server-side from the leg polyline, and show it in the Navigate result. Out of scope: changing path choice or seed data, `instruction` text, anything in a hot or protected file, mobile layout (milestone 5), room-name matching (milestone 4).

**Files (all in the checkout).**
- `pkgbc231600/src/RouteStep.c3typ` (new): `kind: !string`, `text: !string`, `meters: !double`, `seconds: !double`, `pointIndex: !int`. Doc comment names the five kinds.
- `pkgbc231600/src/RouteLeg.c3typ`: add `steps: [RouteStep]` (nullable, additive). Keep `instruction`.
- `pkgbc231600/src/NavRouter.js`: add pure helpers `turnDirection(h1, h2)`, `buildSteps(leg, hops, nodesById, destNode, arrivedVia)`. Call from `assembleLegs`; record per-hop `{from, to, meters, seconds}` while attributing costs, attach `leg.steps`, and do not leak the hop list into the result.
- `pkgbc231600/test/node/steps.test.js` (new): see below. Reuse `harness.js`; do not edit it unless needed.
- `pkgbc231600/ui/react/src/pages/Navigate/routeStepTypes.ts` (new): `RouteStep`, `RouteLegWithSteps`.
- `pkgbc231600/ui/react/src/pages/Navigate/RouteSteps.tsx`: render `leg.steps` as a nested numbered list per leg with per-step distance (`12 m`), falling back to `instruction`.
- `pkgbc231600/ui/react/src/pages/Navigate/RouteSteps.test.tsx` (new), and `jest.config.ts` only if the alias is needed.

**Interfaces.**
```js
// NavRouter.js (no exports; top-level functions, ES5)
function turnDirection(h1, h2)  // {dx,dy} x2 -> 'straight' | 'left' | 'right' | 'around'
function buildSteps(leg, hops, nodesById, destNode, arrivedViaNode)  // -> RouteStep[]
// RouteStep = { kind: 'start'|'straight'|'turn'|'connector'|'arrive', text, meters, seconds, pointIndex }
```
`pointIndex` indexes `leg.points` at the node where the step begins.

**Behavior.**
- Hops under 30 degrees from the previous hop merge. Skip zero-length hops.
- Over 30 degrees: a `turn` step, "Turn left" / "Turn right" (reversal over 150 degrees: "Turn around"), plus " at <label>" when the turn node has a `roomNumber` or is a connector (`connectorLabel`), plus the distance walked after it ("then walk 12 m").
- Leg 0 starts with `start` ("Walk 25 m toward ..." or similar); every later leg starts with `connector` ("Take the West Stairs up to Level 3", direction from floor levels, name from `connectorLabel`; `meters` 0). After a `connector` step, the first walk is `straight`. Never two consecutive `straight`.
- The final leg ends with `arrive`: "3335 is on your left" / "on your right" / "straight ahead", from the turn into the last hop (`meters` 0).
- Step `meters` and `seconds` come from the `chosen` edges. Sum of step meters in a leg equals `leg.meters` +-1. Failure routes have no legs, so nothing changes there. No throws.

**Acceptance (checkable).**
1. `node --test`: demo route (West Entrance L1 to 3335, seed data) has >= 1 `turn` step and no two consecutive `straight` steps.
2. Per leg, sum of step `meters` is within 1 of `leg.meters`.
3. First step of every leg is `start` (leg 0) or `connector` (later legs); the last step of the last leg is `arrive` and its text contains "3335".
4. An L-shaped synthetic fixture (three nodes, corridor east then south) walked both ways, plus its mirror image, gives right/left correctly (this pins the y-down sign). A reversal fixture gives "Turn around".
5. Step-free demo route: some connector step text contains "Elevator C". Default route: contains "West Stairs".
6. Existing router tests still pass; `instruction` strings are unchanged.
7. Jest render test: given a `NavRoute` with steps, the component shows each step text and its distance, and with `steps` absent still shows `instruction` (old-server fallback).
8. `bash projects/tardyai/verify.sh /Users/steeb/agentlab-work/tardyai` prints `VERIFY: PASS`. A mutation check (flip the cross-product sign) must make test 4 fail; report it.

**PR checklist addition.** "Re-provision in Studio (new type `RouteStep`, new field `RouteLeg.steps`)", then the standard Desktop/Mobile list.

**Blast radius.** `RouteLeg.steps` is nullable, and the UI falls back to `instruction`, so an un-re-provisioned tenant keeps working. If `buildSteps` threw, `findRoute` would break the core flow, so keep it total (no throws on a one-point leg or a leg of one hop) and test a one-point leg.

## Open questions

- Whether jest runs `RouteSteps.test.tsx` as-is (missing `@/` alias, `lucide-react` under jsdom). Unverified; the builder finds out first.
- Studio's Node version and whether it accepts new `.c3typ` fields without a manual re-provision. The latter is on the checklist rather than checked.
- Spur stairs cause a "Turn around" on the demo route. If the owner dislikes this, a later milestone could fix the seed or the path cost; deliberately out of scope here.
- Cumulative curve drift (item 3 of Findings) is accepted, not solved.
