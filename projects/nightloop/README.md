# nightloop

nightloop gives a git repo a gated overnight loop: research, then build,
then review, then a pull request, one increment per night, run by headless
Claude Code (`claude -p`). Model judgment does the work. Plain code decides
whether that work moves on: each phase must leave a fresh artifact, the
review verdict is parsed fail-closed, a PR is only reported shipped when
GitHub says it merges cleanly, and a failed cycle is saved to its own branch
so its leftovers never reach the next one. It is for developers who want an
agent working a backlog in their repo while they sleep, without trusting it
blindly. It is a Python CLI that uses only the standard library, plus a
Claude Code plugin with the three phase agents.

nightloop is a port, to a reusable tool, of the bash pipeline that builds
this repo ([`PIPELINE.md`](../../PIPELINE.md)). The plan, the milestones and
every design decision are in [`PLAN.md`](PLAN.md).

## Status

**nightloop has not been run live.** No night has called a real model,
pushed a real branch or opened a real PR. Each piece is tested offline with
a fake runner, a fake `gh` and a local bare `origin` (803 tests before the
docs milestone). The live checks still to do are human steps, and each one
is listed where it belongs rather than copied here:

- [`PLAN.md`, Current state](PLAN.md#current-state): the "Known limits"
  list of each milestone (shipper, claims, CLI, plugin, schedulers,
  benchmark).
- [GitHub Actions: the three-step human check](docs/github-actions.md#the-three-step-human-check)
- [Reviewer benchmark: open questions](docs/reviewer-benchmark.md#open-questions)
  (no catch rate has been measured yet)
- [Plugin: not verified yet](plugin/README.md#not-verified-yet-human-steps)

## Install

Python 3.11 or newer. No third-party packages. Tested on 3.11 and 3.13. The
Homebrew 3.12.14 on the build machine cannot `import plistlib`, so
`nightloop schedule` fails there. Everything else works on it.

```sh
cd projects/nightloop
python3 -m pip install -e .   # puts `nightloop` on PATH for this interpreter
nightloop --help
```

Or run it from the source tree without installing:
`cd projects/nightloop && python3 -m nightloop --help`. (`python3 -I` cannot
import it from the source tree, because isolated mode drops the current
directory from the path.)

## First command

```sh
cd projects/nightloop
python3 -m nightloop run --dry-run --repo /path/to/your/checkout --config examples/plugin.nightloop.toml
```

This prints the night's plan, with each phase's model, gate, timeout and
exact `claude` argv. It calls no model and writes nothing. The
[quickstart](docs/quickstart.md) goes from here to a first real night.

## Commands

| Command | What it does |
|---|---|
| `nightloop run` | Runs one night (up to `cycles` cycles) if the clock is inside the config's run window. |
| `nightloop run --dry-run` | Prints the plan for that night. No model, no git, no writes. |
| `nightloop report` | Summarises `logs/metrics.jsonl`: per phase, and per gate how many nights it ran and blocked. |
| `nightloop schedule install` | macOS: writes and loads a launchd job that runs `nightloop run` nightly. `uninstall` and `status` too. |
| `nightloop schedule-gha` | Writes a GitHub Actions workflow that runs one night on a cron ([how-to](docs/github-actions.md)). |
| `nightloop bench` | Measures the reviewer template's catch rate and false-positive rate. Billed ([how-to](docs/reviewer-benchmark.md)). |

`nightloop <command> --help` lists every flag.

## Exit codes

Every subcommand uses these. A test checks that this table matches
`nightloop/cli/exit_codes.py`.

| Code | Name | Meaning |
|---|---|---|
| 0 | `OK` | The night ran (shipped or not; the metrics say how), or the plan, report or schedule step finished. |
| 1 | `ENVIRONMENT` | A program could not start (`claude`, `git`, `gh`), a launchd step failed, or `schedule status` found a broken job. |
| 2 | `USAGE` | Bad arguments, config or fixture, or `--repo` is not a checkout's top level. |
| 3 | `REFUSED` | Outside the run window, or the clock could not be read. |
| 4 | `USAGE_LIMITED` | The night stopped early on a Claude usage limit. |
| 5 | `NO_METRICS` | `report`: the metrics file is missing or holds no usable record. |
| 6 | `JOB_RUNNING` | `schedule install`/`uninstall`: the job is running, so nothing was changed. |
| 7 | `UNGRADED` | `bench`: a trial gave no gradable verdict, so no results were recorded. |
| 8 | `BENCH_STALE` | `bench --check-fresh`: the recorded run measured a different reviewer template. |
| 9 | `BENCH_NEVER_RUN` | `bench --check-fresh`: no results file is recorded. |

## Docs

- [Quickstart](docs/quickstart.md): a tutorial from install to the first
  night and its report.
- [Architecture](docs/architecture.md): what a night does, each gate, and
  the incident that motivated it. Also lists what nightloop does not do yet.
- [Running on GitHub Actions](docs/github-actions.md) and
  [benchmarking the reviewer](docs/reviewer-benchmark.md): how-to guides.
- [The plugin](plugin/README.md): the phase agents, the publish guard and
  `run_silent`.
- Example configs: [`examples/plugin.nightloop.toml`](examples/plugin.nightloop.toml)
  (the plugin's agents) and
  [`examples/agentlab.nightloop.toml`](examples/agentlab.nightloop.toml)
  (this repo's own pipeline, for reference).

## Tests

```sh
cd projects/nightloop
python3 -I -m unittest discover -v
```

The tests need no network, no model and no key. The opt-in live tests skip
unless `NIGHTLOOP_LIVE=1` is set. `tests/test_docs.py` keeps these docs
honest: every link resolves, every `nightloop` command and flag they name
exists, the exit-code table equals the code, every gate section names a real
module, test file and incident source, and the quickstart's dry-run output is
real output.
