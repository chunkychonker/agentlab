# nightloop milestone 11: reviewer benchmark (2026-11-24)

Mode: project:nightloop. Milestone 11 is picked over 7c on purpose: 7c's own
entry says building it is the owner's call and there is no recorded decision
(see the 2026-11-24 decisions entry in `PLAN.md`). Milestones 1-10 and 7b are done.

## Question

How do we measure whether nightloop's reviewer agent catches seeded bugs
**and** leaves clean diffs alone, with the false-positive rate printed next to
the catch rate, using the pieces nightloop already has (`parse_verdict`,
`run_phase`, `Runner`, `FakeRunner`) so everything but the billed run tests offline?

## Findings

**What already exists in this repo (read 2026-11-24).**
- `eval/run_reviewer_eval.sh` (agentlab's bash harness, PR #17) has two
  fixtures, `self-reference-bare-mention` and `subdirectory-bare-mention-overmatch`.
  Each has `expect.txt` (`VERDICT: FAIL` plus one `PATTERN:` regex) and an
  `increment/` dir. It copies the increment into `examples/_eval_<slug>/` of
  the live checkout, calls the real reviewer, and classifies CAUGHT /
  MISDIAGNOSED / MISSED. Its gaps for a published benchmark:
  - **No clean fixtures.** A reviewer that fails everything scores a perfect
    catch rate. This is exactly the 2026-11-16 owner decision for m11.
  - **One trial per fixture.** The reviewer is a model, so one run is an anecdote.
  - **It dirties the live checkout** (and refuses to run on a dirty tree).
  - **No rates**, only a pass/fail exit code.
  - A MISSING or MALFORMED verdict is lumped in with MISSED.
  - It tests agentlab's reviewer, not the plugin's `reviewer.md`.
- `projects/nightloop/plugin/agents/reviewer.md` is the template under test
  (`model: opus`, tools `Bash, Read, Grep, Glob, Write`). It reads the newest
  note under `research/`, the whole working-tree diff (`git status`,
  `git diff`, untracked files), runs tests via `run_silent`, and writes the
  verdict file. Both the note and the diff must exist in the repo it runs in.
- m2's `parse_verdict(text) -> Verdict` gives PASS / FAIL / MISSING / MALFORMED
  (exactly one column-0 `VERDICT:` line). The benchmark must reuse it, so
  "what counts as a verdict" is the same as in the real gate.
- m3's `run_phase(req, runner, *, now, gate, repo, verdict_path, metrics_sink)`
  runs one phase and reads the verdict through the gate. `FakeRunner` with a
  step function can write a verdict file, which stands in for the model. The
  prompt template `REVIEW_PROMPT` in `cycle/prompts.py` is what the real review
  phase sends; the benchmark should send the same string.
- The phase runner passes no env, so `run_silent` (in `plugin/bin/`) is only
  on PATH if the process env has it. m8's known limit says the plugin is not
  passed via `--plugin-dir`. For the bench, put the agent template in the
  temp repo's `.claude/agents/` (a normal project agent, resolved by bare
  name) and prepend `plugin/bin` to PATH at the CLI edge. I did not test that
  the agent resolves this way under `claude -p` (Open questions).

**How others measure code-review agents (web, 2026-11-24; I could not open
the papers, only search summaries, so treat every number as unverified).**
- A search turned up several 2026 benchmarks that report precision and
  recall against known defects, and add a noise measure: CR-Bench
  ([arXiv 2603.11078](https://arxiv.org/pdf/2603.11078)) reports a
  usefulness rate and a signal-to-noise ratio and says an agent pushed to find
  more bugs gets noisier; SWR-Bench
  ([arXiv 2509.01494](https://arxiv.org/pdf/2509.01494)) reports average false
  positives per PR. A DEV Community post (secondary source, not checked
  against the original) describes a vendor benchmark that injected defects into real
  PRs and found high precision but low recall in many tools. Two of the
  other hits carry arXiv ids (2608/2610 prefixes) and model names I cannot
  place; I am not citing them for anything.
- The takeaway that matters for the design is the one the owner already
  wrote in the plan: report both axes, because either alone is gameable.
  nightloop's reviewer outputs one binary verdict per diff, so the two rates
  are simply: **catch rate** = FAIL-with-the-right-reason on seeded diffs;
  **false-positive rate** = FAIL on clean diffs.
- With a handful of fixtures and a few trials each, a bare percentage
  overstates certainty. The Wilson score interval is the standard fix for
  small n: Agresti & Coull (1998) and Brown, Cai & DasGupta (2001) both
  recommend it over the normal approximation, which undercovers at small n and
  at proportions near 0 or 1 (search summary of
  [Agresti 1998](https://math.unm.edu/~james/Agresti1998.pdf) and the
  [Wikipedia article](https://en.wikipedia.org/wiki/Binomial_proportion_confidence_interval);
  Brown et al.'s specifics come from a search snippet, not the paper). It is a
  10-line pure function in stdlib. A 0/12 FP rate then prints as `0% (95% CI
  0-24%)`, which is honest: it does not prove the reviewer is quiet.
  Trials of the same fixture are not independent samples of "all diffs"; the
  interval is over trials, so state that in the report.

**Design consequences.**
1. A **catch** needs the right reason, not only FAIL. Keep the existing
   `PATTERN:` idea (regexes that must appear in the verdict file), because a
   reviewer that FAILs a seeded diff for an unrelated nit has not caught the
   seeded bug. That outcome gets its own name, MISDIAGNOSED, and counts as not
   caught.
2. A trial that yields no gradable verdict (CLI error, timeout, MISSING,
   MALFORMED) is **UNGRADED**: shown in its own column, excluded from both rates, and
   makes the run exit non-zero. It must never be read as a PASS or as a FAIL.
3. "The runner is run whenever the reviewer template changes" needs a
   mechanical hook, not a promise. Record the SHA-256 of `reviewer.md` in the
   result file and give the CLI a `--check-fresh` mode that compares it with the
   current template (a pure function on two strings). Wiring that check into
   CI is the owner's call and is not part of this milestone: a billed run cannot
   happen in the nightly build, and a test that fails until a human records a run
   would turn main red on the first template edit.
4. Fixtures live in a new `projects/nightloop/bench/fixtures/` rather than the
   top-level `eval/`: the live bash harness there is agentlab's and the
   decisions log forbids editing `.pipeline/` and its neighbours from
   nightloop work. Port the two seeded fixtures by copy (leave `eval/` untouched).
5. Each trial runs in a **temp git repo**, never the live checkout: base
   commit holds the fixture's research note (with a Build proposal); the
   increment files are left uncommitted. The temp repo has no remote, so even
   a misbehaving reviewer cannot push.

## Build proposal

**Increment:** `projects/nightloop/nightloop/bench/` plus
`projects/nightloop/bench/fixtures/`, and a `nightloop bench` subcommand.
Extends the existing project; no new top-level example dir.

### Layer 1: intent

Give nightloop a way to state, with numbers, how often its reviewer template
catches a seeded bug for the right reason and how often it wrongly fails a
clean diff. **Out of scope:** the billed run in the build (a human step,
stated in the docs); wiring `--check-fresh` into CI; changing `reviewer.md`;
LLM-judge grading of findings (regex patterns only); non-Claude reviewers;
more than the fixtures listed below; auto-tuning the reviewer.

### Layer 2: behavioral spec

Inputs: a fixtures dir, the reviewer template path, `--trials N` (default 3),
a config (for the review phase's model/tools; use the plugin config
`examples/plugin.nightloop.toml`), `--timeout-s`.

Fixture on disk, `bench/fixtures/<slug>/`:
- `expect.toml`: `kind = "seeded" | "clean"`; for seeded, `patterns = [regex, ...]`
  (at least one); clean fixtures must have none. Unknown keys are an error.
- `note.md`: the research note placed under `research/` in the temp repo.
- `increment/`: files copied into the temp repo, uncommitted.

Outputs: a `BenchReport` (per fixture: counts per outcome; totals; Wilson
intervals; template SHA-256), a JSON result file, and a text report whose
first lines are the two rates side by side, e.g.
`catch rate 5/6 = 83% (95% CI 44-97%)   false-positive rate 0/6 = 0% (95% CI 0-39%)`.

Trial outcomes, an enum decided by one pure function
`classify(kind, verdict, findings_text, patterns)`:

| fixture kind | verdict | patterns | outcome |
|---|---|---|---|
| seeded | FAIL | all match (case-insensitive, searched in the verdict text) | CAUGHT |
| seeded | FAIL | not all match | MISDIAGNOSED |
| seeded | PASS | n/a | MISSED |
| clean | PASS | n/a | CLEAN_PASSED |
| clean | FAIL | n/a | FALSE_POSITIVE |
| any | MISSING / MALFORMED, or phase status not GATED_PASS/GATE_FAILED | n/a | UNGRADED |

Rates: catch = CAUGHT / (CAUGHT+MISDIAGNOSED+MISSED); FP =
FALSE_POSITIVE / (FALSE_POSITIVE+CLEAN_PASSED). UNGRADED is in neither.
A rate with a zero denominator is `n/a`, never 0% or 100%. A run with zero
seeded or zero clean fixtures is refused before anything runs, so one axis
can never be reported alone.

Invariants:
- I1 No trial touches the live checkout (a tree digest of the repo is unchanged).
- I2 The prompt sent equals `phase_prompt("review", ...)` for the temp repo,
  and the argv equals what `build_argv` gives the real review phase, so the
  benchmark measures the production invocation.
- I3 `parse_verdict` is the only verdict parser.
- I4 Any UNGRADED trial makes the exit code non-zero; the report still prints.
- I5 Wilson interval is within [0,1], contains the point estimate, and is
  narrower as n grows for a fixed proportion.
- I6 Report and JSON are deterministic for the same trial outcomes (no clock, no ordering by dict hash).
- I7 `--check-fresh` is `FRESH` only when the recorded template hash equals
  the current file's; no record file is `NEVER_RUN`, not `FRESH`.
- I8 `--dry-run` prints the matrix (fixtures x trials, argv of one trial) and runs nothing.

Failure modes (all loud): malformed `expect.toml` (names field and fixture),
a clean fixture with patterns, a seeded one without, an invalid regex, a
fixture whose files escape its dir via `..` or a symlink, a missing `note.md`,
`git` or `claude` that cannot start (`OSError`, exit 1, as in m7), a
clean fixture whose own tests fail (see criterion 8).

### Layer 3: interfaces (no bodies)

```python
# bench/fixture.py  (pure types) / bench/load.py (edge: reads dirs)
class Kind(Enum): SEEDED; CLEAN
@dataclass(frozen=True) class Fixture: slug: str; kind: Kind; patterns: tuple[str, ...]; note: str; files: Mapping[PurePosixPath, bytes]
def load_fixtures(root: Path) -> list[Fixture]   # raises FixtureError(slug, field, reason)

# bench/classify.py (pure)
class Outcome(Enum): CAUGHT; MISDIAGNOSED; MISSED; CLEAN_PASSED; FALSE_POSITIVE; UNGRADED
def classify(kind: Kind, verdict: Verdict | None, findings: str, patterns: tuple[str, ...]) -> Outcome

# bench/stats.py (pure)
def wilson(successes: int, n: int, z: float = 1.96) -> tuple[float, float]   # ValueError if n<=0 or successes not in 0..n

# bench/report.py (pure)
@dataclass(frozen=True) class BenchReport: template_sha256: str; trials: int; per_fixture: Mapping[str, Counter[Outcome]]
def summarise(results: Sequence[TrialResult], template_sha256: str) -> BenchReport
def render_report(r: BenchReport) -> str ; def to_json(r: BenchReport) -> str
def exit_code(r: BenchReport) -> int   # non-zero if any UNGRADED

# bench/fresh.py (pure)
class Freshness(Enum): FRESH; STALE; NEVER_RUN
def check_fresh(current_template: str, recorded_sha256: str | None) -> Freshness

# bench/workspace.py (edge: git, tmp dirs)
class TrialWorkspace(Protocol): def materialize(self, f: Fixture, template: str) -> Path  # temp repo path
# bench/run.py (edge): run_bench(fixtures, cfg, runner: Runner, ws: TrialWorkspace, trials, ...) -> list[TrialResult]
# cli/bench_args.py, cli/bench_cmd.py: `nightloop bench [--dry-run | --check-fresh] ...`
```

Purity: `classify`, `stats`, `report`, `fresh`, `fixture` import no I/O
module (add `tests/test_bench_purity.py` as an allowlist, like the other
packages). `main` imports the bench modules only for `bench` (the m9/m10
rule), and the PATH prepend lives in `bench_cmd.py` only.

### Acceptance criteria (checkable)

1. `classify` returns the table's outcome for every row, plus: a pattern
   matching only in a non-findings place is still a match (patterns search the
   whole verdict text; state this in the README), and `verdict=None` is UNGRADED.
2. `wilson`: known values (0/0 raises; 0/10 lower bound is 0.0 and upper about 0.278;
   10/10 upper is 1.0; 5/10 about 0.237-0.763); bounds in [0,1] over a sweep of all (k, n<=30); width shrinks with n.
3. `render_report` prints both rates on the first two lines with intervals,
   `n/a` for an empty axis, and an UNGRADED column; byte-identical on a
   second call (I6).
4. `load_fixtures` rejects each failure mode listed above with a `FixtureError` naming the slug and field.
5. A full bench run through `FakeRunner` and the real `run_phase` and
   `evaluate` gate: scripted steps write verdict files; 3 seeded x 2 trials +
   2 clean x 2 trials gives the hand-computed counts, including one MISSED,
   one MISDIAGNOSED, one FALSE_POSITIVE, and one UNGRADED (a step that writes a
   malformed verdict and one that exits non-zero), and `exit_code` is non-zero.
6. I1/I2: with the fake runner, the recorded `PhaseRequest.prompt` equals
   `phase_prompt("review", repo=<temp repo>, verdict_path=<that repo>/logs/last-review.md)`,
   `cwd` is the temp repo, and a digest of the real repo tree is unchanged.
   The temp repo has the reviewer template at `.claude/agents/reviewer.md`
   (byte-equal to the template under test), the note committed, the increment
   untracked, and no remote.
7. `--check-fresh`: FRESH / STALE / NEVER_RUN for a hash equal / different /
   absent; and through `main`, exit codes distinguish them.
8. Fixtures shipped: the 2 ported seeded ones, at least 2 new seeded ones
   (builder's choice of bug class; each must be a real bug whose tests
   still pass, so the reviewer has to reason rather than just run them),
   and at least 3 clean ones (small, correct, with tests that pass). An
   offline test materializes every fixture and runs its tests: every clean
   fixture's tests must exit 0. It also checks that seeded fixtures' tests
   exit 0 too (a seeded bug a failing test reveals would measure `run_silent`, not review).
9. `--dry-run` writes nothing (tree digest) and builds no `Runner` (I8).
10. Mutation checks (each must fail at least one test): count UNGRADED as
    MISSED; count UNGRADED as CLEAN_PASSED; FP denominator includes UNGRADED; drop
    the pattern requirement (any FAIL on seeded = CAUGHT); print the catch rate only;
    a bare `==` on the verdict text instead of `parse_verdict`; materialize into the live repo.
11. The existing 636 tests still pass; the new tests need no network, no model.
12. Opt-in live test (`NIGHTLOOP_LIVE=1`, billed): one trial of one seeded
    and one clean fixture through `SubprocessRunner`. Not run in the build.
    README states cost: `fixtures x trials` Opus review calls (default 7 x 3 = 21).

Docs: add `docs/reviewer-benchmark.md` with how to run it, the interval
caveat (trials are not independent diffs), why a fixture's bug must not fail
its own tests, and that the first published numbers need a human run.

## Open questions

- Does `claude -p --agent reviewer` resolve an agent from the temp repo's
  `.claude/agents/` and find `run_silent` via an inherited PATH? Unverified;
  the opt-in live test is where it gets answered. If not, fall back to the
  scoped plugin form, which m1's agent-name rule rejects (m8 limit), so that
  would need an owner decision.
- Default `--trials 3` and the default `--timeout-s` are guesses; the owner should set them. Opus reviewer cost is not
  estimated here (I did not look up current Opus pricing this cycle).
- Whether a published catch rate should demand a minimum, and whether
  `--check-fresh` belongs in CI, are owner decisions. The proposal only
  reports.
- The benchmark papers above were seen only as search summaries; none was opened.
