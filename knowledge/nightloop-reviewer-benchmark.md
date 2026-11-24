# Benchmarking a reviewer agent (nightloop m11)

Durable points from `research/2026-11-24-nightloop-reviewer-benchmark.md`:

- A reviewer with one binary verdict has two rates, and each is gameable alone: **catch rate** (FAIL on seeded diffs, for the right reason) and **false-positive rate** (FAIL on clean diffs). Print them side by side; a reviewer that fails everything scores a perfect catch rate.
- A catch needs the *reason*, not only FAIL: require regex patterns in the verdict text; a FAIL for an unrelated nit is MISDIAGNOSED, not caught.
- A trial with no gradable verdict (MISSING, MALFORMED, CLI error, timeout) is UNGRADED: in neither rate, and the run exits non-zero. Reuse the production verdict parser, see [[gates-port-python]].
- Seeded bugs must not fail their own tests, or the benchmark measures the test runner rather than the review.
- With few fixtures and trials, report a Wilson interval, not a bare percentage (recommended over the normal approximation at small n by Agresti-Coull and Brown-Cai-DasGupta); trials of one fixture are not independent diffs.
- Run each trial in a throwaway git repo with no remote, never the live checkout (the older bash harness `eval/run_reviewer_eval.sh` dirties it). Send the production prompt and argv, see [[claude-p-phase-contract]].
- "Run when the template changes" needs a mechanical check: record the template's SHA-256 with the results and compare.

Status: researched 2026-11-24, not built yet. Related: [[cycle-orchestration-gates]], [[nightloop-gate-stats]].
