# README transcript drift: making the lab's documented self-test output verifiable

**Date:** 2026-08-11
**Backlog items:** both entries under "Health-check findings (2026-08-10)"

## Question

The 2026-08-10 health check found `examples/typed-tool-registry/README.md` claiming
"All 4 self-tests passed" while the suite actually emits "All 6" — wrong since PR #2
landed, invisible for nine days. Is this a one-off typo, or a class of defect this
lab systematically cannot catch? And what is the smallest thing that turns the class
into a test?

## Findings

### 1. It is a class, not a one-off. 9 of 13 examples are exposed.

Every example README in this repo documents its offline self-test with the same
shape: a fenced block introduced by "Expected output:", containing `ok  <claim>`
lines and a trailing `All N self-tests passed.`

I measured it — 9 example READMEs carry a deterministic, offline self-test
transcript (`mcp-hello-world`, `mcp-resources-vs-tools`, `minimal-agent-loop`,
`orchestrator-subagents`, `skill-anatomy`, `skill-reference-files-bigquery`,
`skill-script-execution-word-counter`, `tool-error-policy`, `typed-tool-registry`).
`mcp-connect-claude-code` has two blocks, one of which is a *billed live* transcript
("verified during this build") and is therefore not reproducible offline.

That is 9 hand-maintained copies of program output, none of which is checked against
the program. The health check found one already wrong. The base rate is not zero.

### 2. Why nothing catches it

Per `PIPELINE.md`, the nightly reviewer sees only that night's working-tree diff. A
README whose transcript was correct on the day it landed and drifted later is
invisible to every subsequent reviewer, because no subsequent diff touches it. The
health check (every 3rd night) is the only thing that looks laterally — and it found
this by hand-comparing, not by a check that can be re-run.

This is textbook documentation rot: docs that were true once and decayed as the code
moved, with no mechanism binding them to reality
([Documentation Rot, Devonair, undated](https://devonair.ai/blog/pain-points/documentation-rot);
[Why CI/CD Still Doesn't Include Continuous Documentation, DeepDocs / DEV, 2025](https://dev.to/nilzkool/why-cicd-still-doesnt-include-continuous-documentation-m09)).
The consistently recommended fix in that literature is to execute doc examples in CI
rather than to review them harder.

### 3. Prior art: the design is settled, and it is exact-match on captured stdout

**Go's testable examples** are the canonical form and the closest fit. An `Example`
function's trailing `// Output:` comment is compared against captured stdout:

> "As it executes the example, the testing framework captures data written to
> standard output and then compares the output against the example's 'Output:'
> comment. The test passes if the test's output matches its output comment."
> — [go.dev/blog/examples](https://go.dev/blog/examples) (2015, but the mechanism is
> unchanged and still current; flagging the age — the *design*, not a moving API)

Two details worth stealing:
- **Exact match by default.** Go offers `// Unordered output:` as an explicit opt-in
  escape hatch, not a default fuzziness. Fuzzy-by-default comparators stop catching
  the thing they exist to catch.
- **Omitting the output comment means "compile but do not run."** i.e. a documented
  block with no verifiable output is a distinct, legitimate state — not a failure.
  Directly relevant to `mcp-connect-claude-code`'s billed live transcript.

Go's own docs note the lineage back to Python's `doctest`
([Testing in Go, LWN, 2020](https://lwn.net/Articles/821358/)).

**The Python ecosystem's options**, all checked as current:
- `pytest --doctest-glob="*.md"` collects markdown and runs `>>>` REPL sessions
  ([pytest docs, stable](https://docs.pytest.org/en/stable/how-to/doctest.html)).
  Wrong shape here: our blocks are *shell command transcripts*, not REPL sessions.
- [`mktestdocs`](https://github.com/koaning/mktestdocs) runs ` ```python ` blocks
  from markdown under pytest. Also wrong shape — it executes code blocks, it does
  not assert that a documented *output* block matches.
- [`phmdoctest`](https://tmarktaylor.github.io/phmdoctest/) is the closest match: it
  pairs a code block with the *following* output block and generates pytest cases.
  Design-validating, but it's a codegen tool aimed at Python-in-markdown.
- [`bashtestmd`](https://github.com/Sovereign-Labs/bashtestmd) has a
  `bashtestmd:compare-output` tag that does exactly our thing for shell blocks;
  [`markdown2bash`](https://github.com/michaelvl/markdown2bash) extracts blocks to
  scripts for the same purpose.

**Conclusion:** the pattern is well-established and none of these is worth taking on
as a dependency for 9 uniform blocks in one repo. The comparison we need is
`extract fenced block` + `run command` + `string ==`. Adding a dependency here costs
more (pin, install, teach) than the ~60 lines it replaces. Building it is also the
more honest portfolio artifact, since the interesting content is the *verdict
taxonomy* (below), which none of the above gets right for our case.

### 4. Practitioner reception: thin, and I will not overclaim it

I searched HN via the Algolia API (the `hn-search` MCP server is registered in
`.mcp.json` but could not start — see "Noticed, left alone"). The relevant threads
are small: [Ask HN: keeping code/docs/tests from rotting (2025-07-29, 4
comments)](https://news.ycombinator.com/item?id=44724572) is mostly "write cleaner
code / make juniors update docs" and contains no executable-docs advocacy at all.
[Unit tests as documentation (2024-10)](https://news.ycombinator.com/item?id=41871629)
has a Rust-doctest sub-thread whose summary sentiment is that doctests are good for
keeping examples honest but a poor place for thorough testing.

**Honest read: there is no strong practitioner consensus I can cite here.** The
justification for this increment rests on the measured local base rate (1 of 9
transcripts already wrong) and on the Go/doctest design precedent, not on community
enthusiasm.

### 5. Verified locally: exact string match works, and the real failure mode is different

I ran both examples and compared the documented block to real stdout byte-for-byte.

`minimal-agent-loop` — **matches exactly**, including the blank line before the
summary. Its self-test imports only stdlib (`requirements.txt` says so, and the only
imports are `types.SimpleNamespace` and `agent`), so it runs anywhere with no venv.
This is a real, dependency-free MATCH case for the checker's end-to-end test.

`typed-tool-registry` — I built a scratch venv and captured ground truth. Actual:

```
ok  each tool's schema (name/description/type/required) matches its type hints
ok  TOOL_REGISTRY has exactly 3 entries, keyed by name with true identity
ok  each tool's .call(...) returns the correct value for a known input
ok  each tool's .call(...) raises ValueError on wrong-typed or missing arguments
ok  run_agent joins the final text blocks on a clean finish
ok  run_agent raises RuntimeError instead of returning '' on max_iterations

All 6 self-tests passed.
```

The README documents only the first 4 lines and `All 4`. **This is the exact
replacement text** — the builder should not re-derive it, but should re-run to
confirm.

Note the source of the drift: `test_agent.py:197` prints `f"\nAll {len(tests)} self-tests passed."`
— the *code* is self-updating and correct; the README is a frozen transcript. Adding
a test therefore adds no maintenance burden to the suite.

**Critically:** in a bare environment `python3 test_agent.py` exits 1 with empty
stdout (`ModuleNotFoundError: No module named 'anthropic'`). A naive comparator
would report this as "drift", which is a lie — the doc may be perfectly correct and
merely unrunnable here. The health check handles deps by building a fresh scratch
venv per example (`logs/lab-health-2026-08-10_210003.log:36-38`). So:

> **The verdict taxonomy must have three states, not two: MATCH / DRIFT / UNRUNNABLE.**
> Collapsing UNRUNNABLE into DRIFT produces false accusations on every machine that
> hasn't installed that example's deps — which is most of them.

This is the design insight that makes the increment worth building rather than
`diff <(cmd) <(sed ...)`.

### 6. The second finding, reproduced (and the backlog misfiles it)

The backlog says `examples/tool-error-policy/policy.py`. **It is actually in
`agent.py:187`** — `policy.py` has no such function. The builder should fix the
backlog line while fixing the bug.

`agent.py:207` promises: "``ValueError`` from ``max_attempts < 1`` propagates from
``policy.classify``." It cannot. `agent.py:209` is `for attempt in range(1, max_attempts + 1)`,
so `max_attempts=0` gives `range(1, 1)` — empty — the body never runs, `classify` is
never called, and control falls to the `AssertionError` at line 227. Reproduced in a
scratch venv:

```
max_attempts=0:  AssertionError: policy.classify returned Retry on the final attempt (0)
max_attempts=-1: AssertionError: policy.classify returned Retry on the final attempt (-1)
```

The message is actively misleading: it blames `policy.classify`, which never ran.
`max_attempts` is a public keyword parameter of both `call_tool_with_retry` and
`run_agent` (defaulting to `MAX_ATTEMPTS = 3`), so a caller can reach this. Per
CLAUDE.md §4 ("validate at the boundary, once") the fix is a guard at the top of the
function, not a change to the loop.

## Build proposal

Two commits in one PR. Commit A is the must-ship; commit B is a ~15-minute addendum.
Ship A alone if the day runs out.

### Commit A — `examples/readme-transcript-check/`

Name checked and free: not in `ls examples/` on `main`, no open PRs (`gh pr list
--state open` is empty), no branches beyond `origin/cycle/2026-08-10-tool-error-policy`
(already merged).

**1. Intent.** A README that documents a self-test transcript should fail loudly when
the program stops producing that transcript. This increment ships a checker that
verifies one example's documented "Expected output" block against the real stdout of
its self-test command, and uses it to fix the one transcript already known to have
drifted. *Out of scope:* wiring it into the nightly pipeline or the health check;
sweeping all 9 examples in CI (that needs per-example venvs — a later increment);
fuzzy/regex matching; the billed live transcript in `mcp-connect-claude-code`.

**2. Behavioral spec.**

*Inputs:* README text (str), the fenced-block heading marker, a command
(`Sequence[str]`), and a working directory.

*Outputs:* exactly one of three verdicts.

| Verdict | When | Exit code (CLI) |
|---|---|---|
| `Match` | command exited 0 **and** stdout equals the documented block exactly | 0 |
| `Drift(expected, actual, diff)` | command exited 0 **and** stdout differs | 1 |
| `Unrunnable(exit_code, stderr_tail)` | command exited non-zero | 2 |

*Invariants:*
- Comparison is exact string equality on captured stdout, including blank lines and
  the trailing newline. No stripping, no normalization, no regex. (Go precedent;
  fuzzy defaults defeat the purpose.)
- A non-zero exit is **never** reported as drift, even if stdout also differs.
- The extractor is a pure function of the README text — no filesystem, no clock.
- Running the checker never modifies the README. There is deliberately no `--update`
  flag: auto-accepting output would let a genuine regression rewrite its own
  documentation. State this in the README as a design decision.

*Failure modes (raise, don't return a default — CLAUDE.md §4):*
- No "Expected output" marker in the README → `TranscriptNotFound`.
- Marker present but no fenced block follows it → `TranscriptNotFound`.
- More than one "Expected output" block → `AmbiguousTranscript` naming the count.
  (`mcp-connect-claude-code` has two; the checker must refuse rather than silently
  pick the first.)
- Command not found / times out → propagate; do not map to a verdict.

*Acceptance criteria (each an assertion in the self-test):*
1. Extractor returns the exact 5-line block from a fixture README, trailing newline
   included.
2. Extractor raises `TranscriptNotFound` on a README with no marker.
3. Extractor raises `AmbiguousTranscript` on a README with two marked blocks.
4. `compare` returns `Match` for byte-identical strings.
5. `compare` returns `Drift` when only the count line differs — seeded with the real
   `All 4` vs `All 6` strings from §5 above, as a regression fixture.
6. `compare` returns `Drift` for a missing line, an extra line, and reordered lines
   (three cases).
7. `compare` returns `Drift` when the only difference is a trailing newline.
8. Runner returns `Unrunnable` (not `Drift`) for a command exiting non-zero with
   empty stdout — fixture: `["python3", "-c", "import sys; sys.exit(3)"]`.
9. **End-to-end, no fixtures:** running the checker against the real
   `examples/minimal-agent-loop/` (README + `python3 test_agent.py`) returns `Match`.
   This is the load-bearing test — it proves the checker works on a real README, and
   `minimal-agent-loop` is stdlib-only so it needs no venv.
10. The checker's own self-test imports only the stdlib and passes with `python3
    test_check_transcript.py` from a bare interpreter.

**3. Interfaces.** No bodies.

```python
# check_transcript.py

DEFAULT_MARKER = "Expected output"

class TranscriptNotFound(Exception): ...
class AmbiguousTranscript(Exception): ...

@dataclasses.dataclass(frozen=True)
class Match: ...

@dataclasses.dataclass(frozen=True)
class Drift:
    expected: str
    actual: str
    diff: str          # unified diff, difflib

@dataclasses.dataclass(frozen=True)
class Unrunnable:
    exit_code: int
    stderr_tail: str

Verdict = Match | Drift | Unrunnable

# --- pure core (no I/O) ---
def extract_transcript(readme_text: str, *, marker: str = DEFAULT_MARKER) -> str: ...
def compare(expected: str, actual: str) -> Verdict: ...

# --- imperative shell ---
def check(readme_path: Path, command: Sequence[str], cwd: Path) -> Verdict: ...
def main(argv: Sequence[str]) -> int: ...   # exit codes per the table above
```

CLI shape: `python3 check_transcript.py <example-dir> -- python3 test_agent.py`

Ship `README.md` (what it is, why exact match, why no `--update`, the three verdicts
and their exit codes, an "Expected output:" block of its own — which the checker can
then be pointed at itself) and `test_check_transcript.py`. **No `requirements.txt`:
stdlib only.**

**4. Also in commit A — fix the drifted README.** Replace the block in
`examples/typed-tool-registry/README.md` lines 44–51 with the verified 6-line
transcript from §5, and update line 16's `test_agent.py` row to mention the two
`run_agent` cases so the file describes what it runs. Then demonstrate the fix:
`python3 check_transcript.py ../typed-tool-registry -- python3 test_agent.py` returns
`Match` (needs `anthropic` — build a scratch venv as the health check does, or run
from one). Paste the before/after verdicts into the PR body.

### Commit B — validate `max_attempts` at the boundary

In `examples/tool-error-policy/`:
1. **Failing test first** (CLAUDE.md §6): add to `test_agent.py` a case asserting
   `call_tool_with_retry(..., max_attempts=0)` raises `ValueError`. Confirm it fails
   with `AssertionError` before fixing.
2. Add a guard at the top of `call_tool_with_retry` (`agent.py:209`, before the
   loop): `if max_attempts < 1: raise ValueError(f"max_attempts must be >= 1, got {max_attempts}")`.
   Do **not** touch the loop or the terminal `AssertionError` — that assert is
   correct and should stay unreachable.
3. Update the docstring at `agent.py:207`: the `ValueError` is raised *here*, not
   propagated from `policy.classify`.
4. Bump the self-test count in `examples/tool-error-policy/README.md` to match the
   new total, and re-run `check_transcript.py` against it to prove the count is right
   — the new checker earning its keep on the same day it ships.
5. Fix `BACKLOG.md` line 38: the function is in `agent.py`, not `policy.py`.

**"It works" =** `python3 test_check_transcript.py` passes from a bare interpreter
(criterion 10), the end-to-end `minimal-agent-loop` check returns `Match` (criterion
9), the typed-tool-registry check flips from `Drift` to `Match`, and
`tool-error-policy`'s suite is green with the new boundary test.

## Open questions

- **Should this eventually run over all 9 examples?** Yes, but it needs per-example
  venvs, which is the health check's existing machinery. Deliberately deferred — the
  natural follow-up is teaching the health check to call this checker instead of
  hand-comparing. Worth a backlog entry; I did not add one (maintainer's call).
- **`mcp-connect-claude-code` has two "Expected output" blocks**, one billed/live and
  unreproducible. The spec says refuse with `AmbiguousTranscript`. The better
  long-term answer is probably an explicit opt-out marker in the README (Go's
  "no `// Output:` comment ⇒ compile but don't run"), but I did not want to invent
  README syntax in the same increment that introduces the checker. Left open.
- **Is exact match too brittle for the other 7 transcripts?** I only verified
  determinism for `minimal-agent-loop` (exact) and `typed-tool-registry` (exact once
  deps are present). The other 7 print `ok` lines in fixed order from list-driven
  suites, so they *look* deterministic, but I did not run them. Confirm before any
  repo-wide sweep.
- I could not reach HN's web UI (HTTP 429) and the `hn-search` MCP server would not
  start, so practitioner sentiment in §4 comes from Algolia API results and search
  snippets only, not from reading full threads.

## Noticed, left alone

- **`.mcp.json` is broken in a fresh checkout.** It points `hn-search` at
  `examples/mcp-hn-search/.venv/bin/python3`, but `.venv/` is gitignored
  (`.gitignore:9`) and absent — `claude mcp list` reports
  `✘ Failed to connect — ENOENT`. So the researcher's documented HN tool is
  unavailable to every cycle until someone manually creates that venv. Not this
  cycle's topic; worth a backlog entry.
- 5 READMEs document `python <file>.py` where only `python3` exists on this box
  (already recorded in `logs/last-health.md`, still unfixed).
- `examples/tool-error-policy/__pycache__/` is in the working tree (gitignored).

## Sources

- [Testable Examples in Go — go.dev/blog/examples](https://go.dev/blog/examples) (2015; design precedent, mechanism unchanged — flagged as old)
- [Testing in Go: philosophy and tools — LWN](https://lwn.net/Articles/821358/) (2020; doctest lineage — flagged as old)
- [How to run doctests — pytest documentation (stable)](https://docs.pytest.org/en/stable/how-to/doctest.html)
- [phmdoctest](https://tmarktaylor.github.io/phmdoctest/)
- [mktestdocs](https://github.com/koaning/mktestdocs)
- [bashtestmd](https://github.com/Sovereign-Labs/bashtestmd)
- [markdown2bash](https://github.com/michaelvl/markdown2bash)
- [Why CI/CD Still Doesn't Include Continuous Documentation — DEV](https://dev.to/nilzkool/why-cicd-still-doesnt-include-continuous-documentation-m09) (2025)
- [Documentation Rot — Devonair](https://devonair.ai/blog/pain-points/documentation-rot) (undated)
- [Ask HN: keeping code/docs/tests from rotting](https://news.ycombinator.com/item?id=44724572) (2025-07-29)
- [Unit tests as documentation — HN](https://news.ycombinator.com/item?id=41871629) (2024-10)
- Local, this cycle: `logs/last-health.md`, `logs/lab-health-2026-08-10_210003.log`,
  and scratch-venv runs of `examples/typed-tool-registry/test_agent.py` and
  `examples/tool-error-policy/agent.py`.
