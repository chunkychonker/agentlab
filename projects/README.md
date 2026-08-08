# Projects

Unlike `examples/` (an independent, single-cycle demo each), a
`projects/<slug>/` directory is a real piece of software built incrementally
across many daily cycles, with a plan that persists between them.

## Convention

- `projects/<slug>/PLAN.md` — the plan: goal, scope, an ordered milestone
  list, an append-only decisions log, and a current-state section that every
  cycle touching this project must update before it ends.
- `projects/<slug>/` (everything else) — the actual code. It grows cycle over
  cycle; the builder extends it, it is not a fresh directory each time.

## Mode switch

Exactly one project is active at a time, tracked in `.pipeline/mode`:

- `demo` (default) — the pipeline works `BACKLOG.md` as before: one
  independent increment per cycle under `examples/`.
- `project:<slug>` — the pipeline works `projects/<slug>/PLAN.md` instead,
  building toward it one milestone at a time.

Switching is a manual edit to `.pipeline/mode` — a deliberate human decision,
never inferred by a cycle on its own. To start a new project:

1. Copy `projects/TEMPLATE_PLAN.md` to `projects/<slug>/PLAN.md` and fill in
   the goal, scope, and an initial milestone list.
2. Set `.pipeline/mode` to `project:<slug>`.
3. Run the pipeline as usual (`bash .pipeline/run.sh`).

See `PIPELINE.md` and the individual agent definitions
(`.claude/agents/agentlab-*.md`) for what each phase does differently in
project mode.
