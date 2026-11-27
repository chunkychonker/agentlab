# Benchmarking the reviewer: `nightloop bench`

`nightloop bench` measures the plugin's reviewer agent template
(`plugin/agents/reviewer.md`). It runs the production review phase on a set
of fixtures and reports two numbers side by side:

- **catch rate**: on *seeded* fixtures (a diff with one planted bug), how
  often the reviewer FAILs it *and names the bug*;
- **false-positive rate**: on *clean* fixtures (a correct diff), how often
  it FAILs anyway.

Each number can be gamed alone. A reviewer that fails everything has a 100%
catch rate, so the two are always printed together, and the command refuses
a fixture set that lacks either kind. Milestone 11; research note:
[`research/2026-11-24-nightloop-reviewer-benchmark.md`](../../../research/2026-11-24-nightloop-reviewer-benchmark.md).

**No real numbers have been published yet.** Every test drives the
benchmark with a scripted fake reviewer. The first catch rate and
false-positive rate need a human to run the command below, which is billed,
and to read the output.

## Cost

Each trial is one real review call to the reviewer's model (Opus in
`examples/plugin.nightloop.toml`). A run makes **fixtures × trials** calls.
With the shipped fixtures and the default `--trials 3`, that is
**7 × 3 = 21 Opus review calls**. `--dry-run` prints the exact count first.
Each call may take minutes. `--timeout-s` caps each one (default 3600 s,
the same as `nightloop run`).

## Run it

From `projects/nightloop/`:

```sh
python3 -m nightloop bench --dry-run   # the matrix, the call count, one trial's argv; runs nothing
python3 -m nightloop bench             # billed: runs every trial, prints the report
python3 -m nightloop bench --check-fresh
```

By default it reads the fixtures in `bench/fixtures/`, the template in
`plugin/agents/reviewer.md` and the review phase in
`examples/plugin.nightloop.toml`, and writes `bench/last-run.json`.
`--fixtures`, `--template`, `--config`, `--results`, `--trials` and
`--timeout-s` override them.

`--model NAME` runs the review phase on NAME instead of the model `--config`
names, without a second config file (for example `--model sonnet`, or a full
name such as `claude-opus-5-5`). The name follows the config's own rule:
non-empty, no spaces. `--dry-run` prints the model as `review model NAME`, and
the results file records it as `review_model`, so you can tell two runs apart.
Each run writes `--results`, so give the second model its own file
(`--results bench/sonnet-run.json`) if you want to keep both. The freshness
check reads only the template hash, so it ignores the model. The reviewer
template's own `model:` frontmatter is still passed through `--agent`.
agentlab's `PIPELINE.md` says the CLI's `--model` beats an agent file's
`model:` line, but nobody re-checked that for this flag. Before you trust a
comparison, check the model the CLI reports for one trial.

The report's first two lines are the two rates, each with a 95% interval
(made-up counts, to show the format):

```
catch rate 7/12 = 58% (95% CI 32-81%)
false-positive rate 1/9 = 11% (95% CI 2-44%)
```

After those lines come the ungraded count, the template's SHA-256, the
review model, and one table row per fixture with a count for every outcome.

| exit | meaning |
|------|---------|
| 0 | every trial was graded; the report printed and `bench/last-run.json` was written |
| 7 | at least one trial was UNGRADED; the report printed (with why, per trial) but the results file was **not** written |
| 2 | usage: bad arguments, config, fixture or results file; a missing kind; no executable `plugin/bin/run_silent` |
| 1 | environment: `git` or `claude` could not start, or a trial repo could not be built |

`--check-fresh` compares the template's SHA-256 with the one recorded in
the results file. It exits **0** (`FRESH`) when they are equal, **8**
(`STALE`) when they differ, and **9** (`NEVER_RUN`) when there is no
results file. A results file is only written after a fully graded run, so
FRESH means "a complete run measured this exact template". Wiring
`--check-fresh` into CI is left to the owner.

## How a trial is graded

The verdict is read by the same gate the nightly loop uses (`evaluate` →
`parse_verdict`): exactly one `VERDICT: PASS` or `VERDICT: FAIL` line at
column 0.

| fixture | verdict | outcome |
|---------|---------|---------|
| seeded | FAIL, and every pattern in `expect.toml` matches | CAUGHT |
| seeded | FAIL, some pattern does not match | MISDIAGNOSED |
| seeded | PASS | MISSED |
| clean | PASS | CLEAN_PASSED |
| clean | FAIL | FALSE_POSITIVE |
| either | missing or malformed verdict, CLI error, timeout | UNGRADED |

catch rate = CAUGHT / (CAUGHT + MISDIAGNOSED + MISSED).
false-positive rate = FALSE_POSITIVE / (FALSE_POSITIVE + CLEAN_PASSED).
UNGRADED is in neither, and any UNGRADED trial makes the exit code 7.

**Patterns search the whole verdict file**, case-insensitively, not only
the `Findings:` section. A verdict that mentions `startswith` only in its
`Ran:` line still counts as a match. The patterns are deliberately loose
alternations (`(prefix|startswith|sibling|...)`), so they test whether the
reviewer named the right bug, not its exact wording.

## The interval, and what it does not mean

The interval is a Wilson score interval over trials. At this sample size it
is wide: 5 caught out of 6 is 44–97%. Treat it as a reminder of how little
the run shows, not as a confidence statement about reviews in general.
Repeated trials of one fixture are **not independent samples of all
diffs**. They measure how consistent the model is on that one diff. Seven
fixtures are seven diffs. A difference between two templates that falls
inside both intervals is not a finding.

## Fixtures

Each fixture is a directory `bench/fixtures/<slug>/` holding exactly three
entries:

- `expect.toml`: `kind = "seeded"` with `patterns = [regex, ...]` (at
  least one, none blank), or `kind = "clean"` with no `patterns` key;
- `note.md`: the research note the reviewer reviews against, with a build
  proposal and acceptance criteria;
- `increment/`: the files of the diff, placed at the trial repo's root.

The loader rejects anything else, naming the fixture and the field. That
covers unknown keys, a regex that does not compile, symlinks, paths that
leave the increment, and paths into the trial repo's own `.git`,
`.claude`, `logs` or `research`.

Shipped: two seeded fixtures ported by copy from agentlab's `eval/fixtures/`
(`self-reference-bare-mention`, `subdirectory-bare-mention-overmatch`), two
new seeded ones (`settings-malformed-swallowed`: a broad `except` turns a
malformed settings file into silent defaults; `path-containment-prefix`: a
string-prefix containment check lets `../data-old/` escape), and three clean
ones (`path-containment-correct`, the prefix fixture's correct twin with the
same note; `slugify-ascii`; `run-window-hours`).

**A seeded fixture's bug must not fail its own tests.** If it did, the
reviewer would catch it by running `run_silent`. The benchmark would then
measure the test runner, not the review. `tests/test_bench_fixtures.py`
runs every fixture's tests and requires them all to pass, seeded ones
included. It then runs a probe per seeded fixture that shows the planted
bug really happens. A new seeded fixture needs a probe there too.

## The trial repo

Every trial runs in a new git repo under the system temp dir, deleted
afterwards. The live checkout is never touched. Each repo has:

- a base commit holding `.gitignore`, the fixture's note at
  `research/proposal.md`, and the template under test at
  `.claude/agents/reviewer.md` (byte-for-byte);
- the increment's files, untracked, as a builder would leave them;
- no remote, so a reviewer that tried to push could not.

Nothing in the repo names the fixture. The directory prefix
(`nightloop-bench-`), the note's file name and the commit message are the
same for every fixture, because a slug like `path-containment-prefix` would
tell the reviewer what to look for. The prompt and argv are exactly what a
night's review phase sends (`phase_prompt("review", ...)` and `build_argv`).
The verdict path in the prompt is the production one, `logs/last-review.md`,
relative to the repo.

The benchmark puts `plugin/bin/` first on `PATH` for the review calls, so
the reviewer finds `run_silent` the way the plugin's agents do.

## Open questions

- **Agent resolution is unverified.** It is not known whether `claude -p
  --agent reviewer`, run with its cwd in the trial repo, resolves the
  project agent at `.claude/agents/reviewer.md` rather than an installed
  `nightloop:reviewer` plugin agent. It is also not known whether the agent
  finds `run_silent` through the inherited PATH. If an installed plugin's
  reviewer wins, the benchmark measures that copy instead of the template
  under test. The opt-in live test answers part of this; run it first:
  `NIGHTLOOP_LIVE=1 python3 -m unittest tests.test_bench_live` (2 Opus
  calls). It checks that both trials are graded, but not *which* template
  answered. If the plugin is installed, uninstall it or compare against an
  edited template.
- `--trials 3` and the 3600 s timeout are guesses; the owner should set
  them.
- Whether a published catch rate needs a minimum is an owner decision. The
  benchmark only reports.
- Verdict texts are not kept: each trial repo is deleted, and the results
  file holds counts only. To read why a trial was MISDIAGNOSED, re-run that
  fixture by hand.
