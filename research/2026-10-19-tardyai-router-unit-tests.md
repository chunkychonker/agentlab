# TardyAI milestone 2: router unit tests (`node --test` + `vm`)

Date: 2026-10-19. Mode: external:tardyai (kushsarora/TardyAI, checkout `/Users/steeb/agentlab-work/tardyai` at 8c1b4e1, the lock-file PR branch).

## Question

Can the unmodified `NavRouter.js` be tested offline with only `node --test` and `vm`, and what exactly should the tests assert?

## Findings

Method: I read `pkgbc231600/src/NavRouter.js` (583 lines) and `data/{Floor,MapNode,NavEdge}`. I then prototyped the harness in `/tmp/proto/p.js`, outside both repos, and ran it on node v24.5.0. The hn-search MCP server failed to connect this cycle (missing venv python), so I did no HN lookup. Nothing here needed practitioner opinion; the evidence is local code plus a run.

1. **Loading works unmodified.** `NavRouter.js` is plain top-level `var`/`function` declarations with no `module.exports`. `vm.runInContext(src, ctx)` on a `vm.createContext({...})` makes every function a property of `ctx` (`ctx.findRoute`, `ctx.searchRooms`, `ctx.routeToRoom`, `ctx.nearestNode`, `ctx.graphForBuilding`). The file's only platform coupling is three globals: `MapNode`, `NavEdge` (both used as `.fetch({filter, include, limit})` returning `{objs}`) and `Filter.eq(path, value)`.

2. **Stub shape (the part that is easy to get wrong).** The router reads `node.floor.{id,level,name,pixelsPerMeter,building.id}`, but `data/MapNode` rows carry only `floor: {id}`. The stub must join each node to its `Floor` row, as C3's `include` would. `NavEdge` rows carry `fromNode: {id}` and `toNode: {id}`. The code uses exactly four filters:
   - `Filter.eq('floor.building.id', b)` on nodes
   - `Filter.eq('id', x)` on nodes
   - `Filter.eq('floor.id', f)` on nodes
   - `Filter.eq('fromNode.floor.building.id', b)` on edges (resolve via the joined from-node)

   Make `Filter.eq` return a predicate over a dotted path and have `fetch` apply it. Throw on any unrecognised path, so a future router change fails loudly rather than silently matching nothing.

3. **Prototype results on the real seed data** (node v24.5.0). West Entrance is `n-f1-entrance-w`, found by `name`. The destination is `roomNumber === '3335'`, which is `n-f3-r3335`.

   | Query | Result |
   |---|---|
   | default | found, `totalSeconds` 188.6 (3.1 min), 2 legs, `floorsTraversed` 2; "Walk to West Stairs", then "Take West Stairs to Level 3, then walk to 3335 (Class Room 3335)" |
   | step-free | found, 191.8 s, 2 legs; "Walk to Elevator C (east)", then "Take Elevator C (east) to Level 3, ..." |
   | unknown destination id | `found:false`, "That destination is not on the walkable map." |
   | unknown start id | `found:false`, "Could not find your starting point on the map." |
   | `routeToRoom(..., 'zzzz')` | `found:false`, `We could not find "zzzz" in this building.` |

   Assert with bounds, not exact floats: `150 <= totalSeconds <= 210` for "~3 min". Step-free is only 3.2 s slower, so assert the *instruction text and leg points* (no `n-f1-stair-w` in any leg, `n-f1-elev-e` present) rather than a large ETA gap. The README says "different route *and* different ETA", and `!==` is true today, but a one-second seed edit could flip it, so assert `!==` only.

4. **Unreachable case needs synthetic data.** The seed graph has no stranded room. Build it in the test by cloning the node and edge arrays and appending an isolated room node on floor 3 with no edges. Expect `found:false` with "No walkable route found to ...", and no throw. Also cover the step-free variant: remove the edges into the elevators and stairs so only stair access remains. Expect the message "No step-free route to ... involves stairs." Keep these as small in-test fixtures and never edit `data/`.

5. **Real failure channels.** In production the platform rejects blank `!string` args with `Err.InvalidCall` before the body runs (see `NavRouter.c3typ`). In the vm the JS body does run, so `findRoute('', x)` returns `found:false` ("A start and a destination are both required."). Test it, but label it as the body's defensive path, not the production behaviour. `findRoute(a, a)` returns `found:false` "You're already there."

6. **Gotcha in our own gate: `node --test <directory>` fails on Node 24.** `projects/tardyai/verify.sh` runs `node --test "$dir"` with `$dir=.../test/node`. I reproduced on v24.5.0: `node --test /tmp/proto/t` gives `pass 0 / fail 1` (the directory is treated as a module path and fails). `node --test "/tmp/proto/t/*.test.js"` and a bare `node --test` run from inside the dir both give `pass 1 / fail 0`. As written, the gate would fail the moment `test/node/` exists. The `[ ! -d ]` skip hides this today. Studio's node version is unknown (open question), so the glob form is the portable choice. Source for the behaviour: my own run, not docs. I did not fetch the Node changelog.

7. **A vacuous-pass hole.** If `test/node/` exists but contains no `*.test.js`, a glob that matches nothing makes node error. That is fine (loud), but the step should say so explicitly instead of relying on it.

## Build proposal

**Intent.** Pin the README's routing claims with a no-dependency test suite that runs the real `NavRouter.js`, and wire it into the verify gate correctly. Out of scope: any change to `NavRouter.js` (if a test exposes a bug, report it, don't fix it), the UI jest tests (milestones 3-4), and any C3-tenant check.

**Files (all in the checkout; none protected or hot).**
- `pkgbc231600/test/node/harness.js` (not a `*.test.js`, so it is not run as a test): exports `loadRouter(opts?) -> ctx` and `seed()`.
- `pkgbc231600/test/node/router.test.js`: the assertions.
- `pkgbc231600/test/node/README.md`: not needed; a 3-line header comment in `harness.js` suffices.
- In agentlab (ours, not the target): `projects/tardyai/verify.sh`, the `node_tests` function. Replace `node --test "$dir"` with `node --test "$dir"/*.test.js`. If `$dir` exists but no `*.test.js` matches, print that and return 1. Add a line to `projects/tardyai/checks/test_checks.py` only if it already tests verify.sh behaviour; otherwise skip.

**Interfaces.**
```js
// harness.js
function seed(): { floors, nodes, edges }          // reads ../../data/*/*.json, joins floor onto node
function loadRouter(data?: {nodes, edges}): object // new vm context with MapNode/NavEdge/Filter stubs, NavRouter.js evaluated; returns ctx
```
No `module.exports` in `NavRouter.js` (unchanged); harness reads it with `fs.readFileSync` and `vm.runInContext(src, ctx, {filename})`. Use `node:test` and `node:assert/strict` only. CommonJS, since the package's module type is unknown; check `ui/react/package.json` is not an ancestor of `test/node`. It is not, since `test/` is under `pkgbc231600/`.

**Invariants and failure modes.**
- Each test builds a fresh context (no shared mutable state).
- The stub throws on an unknown filter path.
- Seed data is read-only; synthetic fixtures are deep clones.

**Acceptance criteria (the self-test).** `node --test pkgbc231600/test/node/*.test.js` passes, and `bash projects/tardyai/verify.sh <checkout>` prints `VERIFY: PASS` with the router step actually running (the log shows >= 7 passing tests, not "skipped"). The tests assert:
1. West Entrance to 3335 is found, `legs.length === 2`, `floorsTraversed === 2`, and `150 <= totalSeconds <= 210`.
2. The default route's legs include `n-f1-stair-w`, with "West Stairs" in the first instruction.
3. Step-free is found, no leg point is a stairs-kind node, the first instruction names "Elevator C (east)", and `totalSeconds` is not equal to the default's.
4. Unknown destination id and unknown start id return `found:false` with a non-empty message, without throwing (`assert.doesNotThrow`).
5. `routeToRoom` with an unmatched query returns `found:false`; `routeToRoom` for `'3335'` returns the same total as (1).
6. A synthetic isolated room gives `found:false` ("No walkable route"); a synthetic stairs-only fixture with `stepFree=true` gives `found:false` ("No step-free route").
7. Same start and destination, and the blank-argument body path, give `found:false`.
8. Mutation check, done by the builder by hand and noted in the PR: change `ELEVATOR_WAIT_SECONDS` to 0 in a scratch copy and confirm a test fails. This shows the suite is not vacuous.

**PR checklist note.** The tests are offline, so the Studio Desktop/Mobile checks are not needed. The checklist line is "no UI change; confirm the Studio build still passes after pulling".

## Open questions

- Which Node version does Studio run? Unknown. The tests use only `node:test`, `node:assert/strict` and `vm` (stable since Node 18+), and the glob form works on 24.5.0; I did not test on 18/20.
- Does C3's real `Filter.eq` support paths the stub handles, or does `include` return nested objects with the same shape? I assumed yes from the code and `.c3typ` docs; it can't be verified without a tenant.
- Whether the "~3 min" seed numbers will drift when Kush edits `data/` (the Studio agent pushes to main). The bounds are deliberately loose; if they break, that is a signal worth having.
