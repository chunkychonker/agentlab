# agentlab

A working portfolio of AI-agent engineering: coding agents, reusable skills, and
MCP integrations — built incrementally, one real increment at a time.

Each entry under [`examples/`](examples/) is self-contained and runnable, backed
by a dated research note under [`research/`](research/) that explains the idea
and cites its sources. The point is depth you can click into, not volume.

## Layout

| Path | What's in it |
|------|--------------|
| `research/` | Dated research notes — one topic each, with sources and a concrete build proposal |
| `examples/` | The actual work: runnable example agents, skills, and MCP integrations |
| `BACKLOG.md` | Queue of topics and ideas the pipeline pulls from |
| `PIPELINE.md` | How the daily build pipeline works |
| `.pipeline/` | The scheduled orchestration (research → build → PR) |

## How it's built

A daily pipeline of four focused agents — a **researcher**, a **builder**, a
**reviewer**, and a **maintainer** — coordinates through this repo: research
lands in `research/`, working code lands in `examples/`, an independent reviewer
runs tests and gates the diff, and the maintainer opens a pull request only when
review passes. Every increment is real, reviewed, and merged by a human. See
[`PIPELINE.md`](PIPELINE.md).

## Topics

- **Coding agents** — designing agent loops, tool use, subagents, evaluation
- **Skills** — authoring reusable, model-invoked skills
- **MCP** — building and integrating Model Context Protocol servers
