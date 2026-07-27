# The build pipeline

A once-a-day, three-phase pipeline. Each phase is a headless Claude Code run
(`claude -p`) that delegates to one specialist subagent. The phases don't talk
to each other directly — they coordinate through this repo's files, which is how
the state actually flows:

```
  BACKLOG.md ──▶ [1] researcher ──▶ research/DATE-slug.md
                                          │
                     research note ──▶ [2] builder ──▶ examples/<name>/  (+ BACKLOG update)
                                                              │
                                    working increment ──▶ [3] maintainer ──▶ branch + commit + PR
```

1. **Researcher** (`agentlab-researcher`) — pulls the top backlog item, does
   real web research, writes a dated note to `research/` with sources (and their
   dates) and a concrete, small build proposal.
2. **Builder** (`agentlab-builder`) — reads the newest research note and builds
   ONE real, runnable increment under `examples/`, with its own README and a
   quick self-test. Updates the backlog.
3. **Maintainer** (`agentlab-maintainer`) — judges whether there's a coherent
   unit of real work. If yes: branch, commit **authored as Steve Ling
   `<steveylingy@gmail.com>`**, push, open a PR. If not, it does nothing and logs
   why. It never fabricates, backdates, or pads commits.

Nothing reaches `main` without a human merging the PR — that review is the
quality gate on code that carries your name.

## Running it

- Manually (recommended first, to shake out PATH/auth): `bash .pipeline/run.sh`
- On a schedule: the launchd job `com.steeb.agentlab.daily` runs `run.sh` daily.
  See the repo setup notes for how to load/unload it.

Logs land in `logs/` (git-ignored).
