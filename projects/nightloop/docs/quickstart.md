# Quickstart: from install to your first night

This tutorial takes you from a fresh install to a printed plan for your
repo, then to one real night and its report. Steps 1-5 are free and change
nothing. Steps 6 and 7 are billed and push to your GitHub repo, and they
have never been run end to end (see [Status](../README.md#status)). They
are marked **human step**.

What you need: Python 3.11 or newer, a git checkout of the repo you want
nightloop to work on, and this repo's `projects/nightloop/` directory. For
the real night you also need the `claude` CLI logged in, and `gh` logged in
with push access to that repo.

## 1. Install

```sh
cd projects/nightloop
python3 -m pip install -e .
```

That puts a `nightloop` script on PATH for that interpreter. Every command
below also works without installing, as `python3 -m nightloop ...` run from
`projects/nightloop/`. That is the form this page uses, so you can copy
each command as it is.

## 2. Get your repo ready

nightloop runs in the top level of a git checkout and expects this layout.
It is the layout of the repo nightloop was ported from, and two of the
gates are hard-wired to it (see [the freshness gate](architecture.md#gate-phase-artifact-freshness)):

- a `main` branch that tracks `origin/main` on GitHub, with a clean
  working tree;
- a `BACKLOG.md` whose work items are lines starting `- [ ] `;
- a `research/` directory, where the research phase writes one dated note
  per cycle;
- an `examples/` or `projects/` directory, where the build phase writes the
  increment;
- `logs/` in `.gitignore`. The review verdict (`logs/last-review.md`) and
  the metrics file (`logs/metrics.jsonl`) live there. If git tracks them,
  every cycle's tree is dirty and recovery saves it to a branch.

## 3. Write a config

Copy the plugin example into your repo as `nightloop.toml`, the name
`nightloop run` looks for by default:

```sh
cp examples/plugin.nightloop.toml /path/to/your/checkout/nightloop.toml
```

[`examples/plugin.nightloop.toml`](../examples/plugin.nightloop.toml) sets:

- `[repo] backlog`: the backlog file, relative to the repo.
- `[run] cycles`: how many cycles a night runs, one after another.
  `window_start_hour` and `window_end_hour` give the local hours a night
  may start in, end hour excluded (`1` and `6` mean 01:00 to 05:59).
- One `[phase.<name>]` table for each of `research`, `build` and `review`.
  Each one gives the agent to run, the model, the postcondition (the gate
  that checks the phase's artifact) and the tool allowlist passed to
  `--allowedTools`. A bare `Bash` needs `allow_unrestricted_bash = true`,
  so that a phase given every shell command says so.

A bad config fails at load time with a message naming the field, and the
command exits 2.

## 4. Print the plan

From `projects/nightloop/`, using the example config directly:

<!-- docs-check: dry-run-command -->
```sh
python3 -m nightloop run --dry-run --repo /path/to/your/checkout --config examples/plugin.nightloop.toml
```

At 02:30, the output is:

<!-- docs-check: dry-run-output -->
```text
dry run: would run: hour 02 is inside the run window [01:00, 06:00)
no model was called and no runner was built

cycle 1 / research
  model:   sonnet
  gate:    research_note_fresh
  timeout: 3600s
  cwd:     /path/to/your/checkout
  argv:    claude -p 'Run this cycle'"'"'s research end to end, following your instructions exactly. The repo is /path/to/your/checkout.' --model sonnet --output-format json --permission-mode dontAsk --agent researcher --allowedTools WebSearch WebFetch Read Write Edit Glob Grep Bash

cycle 1 / build
  model:   opus
  gate:    increment_built
  timeout: 3600s
  cwd:     /path/to/your/checkout
  argv:    claude -p 'Build this cycle'"'"'s increment from the newest research note, following your instructions exactly. Verify it runs. The repo is /path/to/your/checkout.' --model opus --output-format json --permission-mode dontAsk --agent builder --allowedTools Read Write Edit Glob Grep Bash

cycle 1 / review
  model:   opus
  gate:    review_verdict
  timeout: 3600s
  cwd:     /path/to/your/checkout
  argv:    claude -p 'Independently review this cycle'"'"'s working-tree diff, run its tests, and write a PASS/FAIL verdict to logs/last-review.md, following your instructions exactly. The repo is /path/to/your/checkout. Do not modify the increment.' --model opus --output-format json --permission-mode dontAsk --agent reviewer --allowedTools Bash Read Grep Glob Write

claims (backlog BACKLOG.md): each step checks main is current after a fetch, then commits and pushes only BACKLOG.md, and only if it changed
  before cycle 1: mark claims on recovery branches stranded
  after cycle 1: mark claims on recovery branches stranded
  after the night: mark tonight's shipped PRs done if already merged
```

`tests/test_docs.py` runs this exact command with the clock at 02:30 and
checks that its output equals this block, so the block cannot go out of
date.

## 5. Read the plan

- The first line is the run-window decision. Outside the window it says
  `would refuse`, and a real `run` would exit 3 without starting anything.
  `--ignore-window` overrides the window for one manual run.
- Each phase block is exactly what a real night runs: the model, the gate
  that must pass before the next phase starts, the timeout (`--timeout-s`,
  default 3600) and the full `claude` argv. The dry run and the real run
  build the argv with the same function.
- The claims block lists the backlog bookkeeping around the cycles. See
  [claim reconcile](architecture.md#gate-stranded-claim-reconcile).
  `--no-claims` turns it off for a repo without a backlog.

## 6. Make the agents resolve (human step)

The config names the agents `researcher`, `builder` and `reviewer`. Those
are the templates in [`plugin/agents/`](../plugin/agents/), and `claude`
must be able to find them when it runs in your repo. nightloop's phase
runner does not pass `--plugin-dir`, and the plugin has no marketplace
entry yet, so for now:

- Copy the three files into your repo's `.claude/agents/` and commit them.
  `claude --agent builder` then finds a project agent named `builder`.
- That copy does not bring the publish guard hook with it. The phases do
  not need to push, because the shipper publishes after the review passes.
  But the example config gives each phase bare `Bash`, and without the
  guard a phase could push anyway. To keep the guard, register
  `plugin/hooks/publish_guard.py` as a `PreToolUse` hook on `Bash` in your
  repo's Claude Code settings, the way
  [`plugin/hooks/hooks.json`](../plugin/hooks/hooks.json) does for the
  plugin. Nobody has checked yet that a `claude -p` phase picks it up.
  [`plugin/README.md`](../plugin/README.md#not-verified-yet-human-steps)
  says how to check it with one billed run.

## 7. Run one night (human step, billed)

Inside the window (or with `--ignore-window`):

```sh
python3 -m nightloop run --repo /path/to/your/checkout
```

This runs real Sonnet and Opus calls, pushes a branch and opens a PR. What
to expect:

- stdout ends with one line per cycle: shipped as PR #N, or which gate
  stopped it and what recovery did.
- stderr has the claim lines (`nightloop: claims before cycle 1: ...`).
- `logs/metrics.jsonl` in your repo gains one JSON line per phase.
- nightloop never merges. A shipped PR waits for you, and its review
  record is in the PR body.
- A failed cycle's work is on a `cycle/<date>-unshipped-<time>-<n>` branch,
  and `main` is back where it was.

Nobody has done this step yet. If you do, record what happened in
[`PLAN.md`](../PLAN.md#current-state), so the README's status line can
change.

## 8. Read the report

```sh
python3 -m nightloop report --repo /path/to/your/checkout
```

This covers the last 7 nights (`--nights N`) and prints two
tab-separated tables. The phase table gives each phase's runs, successes,
and median minutes, turns, output tokens and cost. The gate table gives,
for each phase's gate, how many times and on how many nights it ran and
blocked, with the note `never blocked in N night(s)` when it never fired.
A gate that never blocks over a long window is a candidate to remove.

## Next

- Run it every night: `python3 -m nightloop schedule install --repo
  /path/to/your/checkout` (macOS launchd; `--dry-run` first), or a GitHub
  Actions workflow ([how-to](github-actions.md)).
- Before you trust the reviewer, measure it ([how-to](reviewer-benchmark.md)).
- How each gate works, and the incident behind it: [architecture](architecture.md).
