# agentlab

A working portfolio of AI-agent engineering: coding agents, reusable skills, and
MCP integrations — built incrementally, one real increment at a time.

Each entry under [`examples/`](examples/) is self-contained and runnable, backed
by a dated research note under [`research/`](research/) that explains the idea
and cites its sources. The point is depth you can click into, not volume.

**Start with [`CASE_STUDY.md`](CASE_STUDY.md)**: how the nightly pipeline that
builds this repo is allowed to merge its own code, and what broke along the way.

## Layout

| Path | What's in it |
|------|--------------|
| `research/` | Dated research notes — one topic each, with sources and a concrete build proposal |
| `examples/` | The actual work: runnable example agents, skills, and MCP integrations |
| `projects/` | Longer builds that grow one milestone per night (e.g. `nightloop`, the pipeline as an installable tool) |
| `knowledge/` | The pipeline's long-term memory: durable patterns and gotchas, linked by `[[wikilinks]]` |
| `BACKLOG.md` | Queue of topics and ideas the pipeline pulls from |
| `PIPELINE.md` | How the daily build pipeline works |
| `.pipeline/` | The scheduled orchestration (research → build → PR) |

## How it's built

A nightly pipeline of four focused agents — a **researcher**, a **builder**, a
**reviewer**, and a **maintainer** — coordinates through this repo: research
lands in `research/`, working code lands in `examples/` or `projects/`, an
independent reviewer runs the tests and checks the diff against the research
note's acceptance criteria, and the maintainer opens a pull request only when
review passes. A weekly health check and a pipeline observer report on the
whole portfolio and on the pipeline's own logs. The pull request merges automatically when GitHub reports no
conflicts, so the gate is the reviewer's PASS, not a human click. See
[`PIPELINE.md`](PIPELINE.md).

## Topics

- **Coding agents** — designing agent loops, tool use, subagents, evaluation
- **Skills** — authoring reusable, model-invoked skills
- **MCP** — building and integrating Model Context Protocol servers
