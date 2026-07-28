# CLAUDE.md

Guidance for Claude Code (and the pipeline's subagents) working in this repo.

## What this repo is

`agentlab` is a portfolio of AI-agent engineering — coding agents, reusable
skills, and MCP integrations — built one real, runnable increment at a time by a
daily 4-phase pipeline (researcher → builder → reviewer → maintainer). See
`README.md` and `PIPELINE.md`. Every example must be self-contained, runnable,
and backed by a dated note in `research/`. Depth you can click into, not volume.

## Engineering Protocol

These rules govern all code work in this repo. The pipeline's subagents each
carry a pointer to this file; read it before building or reviewing.

### 0. Before writing code

Work down these layers — do not skip to implementation:

1. **Intent** — one sentence: the problem, and what is explicitly out of scope.
2. **Behavioral spec** — inputs, outputs, invariants, failure modes, and
   acceptance criteria as concrete checkable statements.
3. **Interfaces** — type signatures / function stubs / schemas. No bodies.
4. **Implementation.**
5. **Tests** — written against the layer-2 criteria, not layer-4 internals.

For anything larger than a single-function change, write layers 1–3 (a few
lines is fine) before implementing. For trivial changes, state them inline in a
sentence or two. The point is that they exist and are auditable. If the request
is ambiguous at layer 1 or 2, ask instead of guessing.

### 1. Separation of concerns

- Every module must be describable in one sentence containing no "and."
  If you cannot, split it. Business rules, persistence, transport, and
  presentation are always separate reasons to change.
- Prefer more small files with clear names over fewer large ones.

### 2. Coupling and surface area

- Export the minimum. Everything not part of the contract is private.
- Prefer narrow, explicit function signatures over wide config objects,
  mutable shared state, or inheritance hooks.
- Magic values are coupling-by-meaning. Use named constants defined once
  (e.g. the `MODEL` constant in `examples/typed-tool-registry/agent.py`).

### 3. Dependency direction

- Core logic never imports I/O. Model APIs, filesystem, network, and clocks
  live at the edges. Pure functions where possible; push side effects to the
  outermost layer (functional core, imperative shell).
- No I/O, env-var reads, or global state access inside business logic. Read the
  key at the entry point (`main`) and pass typed values inward.

### 4. Correctness posture

- Make illegal states unrepresentable. Prefer a type that cannot hold a bad
  value over a validator that must be remembered.
- Validate at the boundary, once. Interior code assumes valid input.
- Fail fast and loudly. Never swallow an exception to keep something running.
  Never return a default (or `""`) in place of an error unless the spec says so.
- Every function that can fail states its failure modes in its docstring.

### 5. Change discipline

- One intent per change. No drive-by refactors bundled with a feature, no
  reformatting files you were not asked to touch.
- Any change with an existing consumer follows expand/contract: add new, dual
  support, migrate, then remove old. Never a breaking change in one commit.
- Do not delete or rewrite code you do not understand. Ask.
- Pin dependencies enough to run. Never hardcode secrets or log them.

### 6. Testing

- Tests assert acceptance criteria from the spec, not implementation details.
- Every bug fix starts with a failing test that reproduces it.
- Never write a test that passes trivially or asserts only on a mock you
  configured in the same test.

### 7. Reporting back

End substantive changes with: what changed and why (two or three lines);
assumptions made where the spec was silent; what is not covered and known
failure modes; anything you noticed but deliberately left alone.
