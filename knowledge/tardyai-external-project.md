# TardyAI external project

An external-mode target (`kushsarora/TardyAI`, a C3 AI Studio app) worked by PR only. The plan is in `projects/tardyai/PLAN.md`. The first milestone is the lock file fix: see [[npm-ci-peer-dep-lock-drift]].

## Router tests (milestone 2)

`NavRouter.js` has no exports, so tests load it with `vm.runInContext` and stub `MapNode`, `NavEdge` and `Filter`. The stub must join `Floor` onto each node, because C3's `include` does that and the seed rows carry only `floor: {id}`. Gotcha: on Node 24, `node --test <directory>` fails (the directory is treated as a module), so pass a glob such as `dir/*.test.js`. See research/2026-10-19-tardyai-router-unit-tests.md and [[npm-ci-peer-dep-lock-drift]].
