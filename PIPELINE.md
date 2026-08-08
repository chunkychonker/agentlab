# The build pipeline

A once-a-day, four-phase pipeline. Each phase is a headless Claude Code run
(`claude -p`) that delegates to one specialist subagent. The phases don't talk
to each other directly — they coordinate through this repo's files, which is how
the state actually flows:

```
  BACKLOG.md ──▶ [1] researcher ──▶ research/DATE-slug.md
                                          │
                     research note ──▶ [2] builder ──▶ examples/<name>/  (+ BACKLOG update)
                                                              │
                            working-tree diff ──▶ [3] reviewer ──▶ logs/last-review.md (PASS/FAIL)
                                                                          │
                                          PASS verdict ──▶ [4] maintainer ──▶ branch + commit + PR
```

1. **Researcher** (`agentlab-researcher`) — pulls the top backlog item, does
   real web research, writes a dated note to `research/` with sources (and their
   dates) and a concrete, small build proposal.
2. **Builder** (`agentlab-builder`) — reads the newest research note and builds
   ONE real, runnable increment under `examples/`, with its own README and a
   quick self-test. Updates the backlog.
3. **Reviewer** (`agentlab-reviewer`) — an independent gate: reviews the diff it
   didn't write, runs the increment's tests and lint, scans for stubs, secrets,
   and bugs, and writes a `PASS`/`FAIL` verdict to `logs/last-review.md`. It does
   not fix code — a broken increment gets `FAIL`ed with specifics.
4. **Maintainer** (`agentlab-maintainer`) — reads the verdict. Only on `PASS`
   does it branch, commit **authored as Steve Ling `<steveylingy@gmail.com>`**,
   push, and open a PR. On `FAIL` (or missing verdict) it ships nothing and logs
   why. It never fabricates, backdates, or pads commits, and never overrules a FAIL.

Two gates protect code that carries your name: the reviewer before the PR, and
**you** merging the PR. Nothing reaches `main` without both.

## Mode: demo vs. project

`.pipeline/mode` picks the track for every cycle — `demo` (default) works
`BACKLOG.md` as above, one independent increment under `examples/` per cycle;
`project:<slug>` works `projects/<slug>/PLAN.md` instead, building one
milestone at a time into `projects/<slug>/`, which persists and grows across
cycles rather than starting fresh each time. Each subagent reads
`.pipeline/mode` itself as step 0, so this isn't threaded through the phase
prompts. See `projects/README.md` for how to start one.

## Tools available to the pipeline

Beyond the built-in file/search/web tools, the researcher also has the
`hn-search` MCP server (`examples/mcp-hn-search/`, registered in `.mcp.json`)
for practitioner discussion/reception, not just vendor docs.

## Running it

- Manually (recommended first, to shake out PATH/auth): `bash .pipeline/run.sh`
- On a schedule: the launchd job `com.steeb.agentlab.daily` runs `run.sh` daily.
  See the repo setup notes for how to load/unload it.

Logs land in `logs/` (git-ignored).
