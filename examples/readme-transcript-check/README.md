# README transcript check (executable documentation, three verdicts)

Every example README in this repo hand-copies its self-test transcript into a
fenced block. Nothing binds that copy to the program, so it rots quietly:
`typed-tool-registry`'s block claimed **"All 4 self-tests passed"** for nine days
while the suite actually printed **"All 6"**. The nightly reviewer only ever sees
that night's diff, so a transcript that was correct when it landed and drifted
later is invisible forever after.

This is a dependency-free checker that extracts one README's documented
transcript, runs the command that is supposed to produce it, and compares the
two with `==` — plus a driver that does it for every example in the repo at
once.

From the research notes:
[`research/2026-08-11-readme-transcript-drift.md`](../../research/2026-08-11-readme-transcript-drift.md)
(the checker) and
[`research/2026-09-09-transcript-check-sweep.md`](../../research/2026-09-09-transcript-check-sweep.md)
(the sweep and the opt-out directive).

## What's here

| File | What it is |
|------|-----------|
| `check_transcript.py` | The checker. Pure core (`extract_transcript`, `compare`, `exit_code`, `format_verdict`) above an imperative shell (`check`, `main`) that owns the filesystem and the subprocess. |
| `test_check_transcript.py` | Offline self-test: 10 assertions, one per acceptance criterion. Stdlib only, no key, no network. |
| `sweep.py` | The repo-wide driver. Pure core (`command_script`, `plan_target`, `summarize`) above a shell (`build_interpreter`, `run_sweep`, `main`) that builds a scratch virtualenv per example and runs its self-test. |
| `test_sweep.py` | Offline self-test for the sweep: 14 assertions, including a real two-example run. Stdlib only, no key, no network. |

No `requirements.txt` — this is stdlib only, on purpose. `phmdoctest`,
`mktestdocs` and `bashtestmd` all solve a neighbouring problem, but pinning,
installing and teaching a dependency costs more than the 60 lines of comparison
it would replace (reasoning in §3 of the note).

## Run the self-test (no API key, no network, no dependencies)

```bash
cd examples/readme-transcript-check
python3 test_check_transcript.py
```

Expected output:

```
ok  extractor returns the marked block exactly, trailing newline included
ok  extractor raises TranscriptNotFound instead of returning ''
ok  extractor refuses to guess between two marked blocks
ok  compare returns Match for byte-identical transcripts
ok  compare catches the real 'All 4' vs 'All 6' drift, with a diff
ok  compare catches a missing line, an extra line, and a reordering
ok  compare catches a difference of only the trailing newline
ok  a non-zero exit is Unrunnable, never Drift
ok  end to end: minimal-agent-loop's real README matches its real output
ok  exit codes follow the table and bad usage never yields a verdict

All 10 self-tests passed.
```

The ninth line is the load-bearing one. It uses no fixtures: it points the
checker at the real [`examples/minimal-agent-loop/`](../minimal-agent-loop/)
README and runs that example's real self-test. (`minimal-agent-loop` is
stdlib-only, so it needs no virtualenv on any machine.) The other nine tests
prove the checker is internally consistent; that one proves it is true.

## Use it

```bash
cd examples/readme-transcript-check
python3 check_transcript.py ../minimal-agent-loop -- python3 test_agent.py
```

which prints:

```
MATCH  ../minimal-agent-loop/README.md vs `python3 test_agent.py`
  documented transcript is byte-identical to stdout
```

The block it checks is the fenced block whose nearest preceding non-blank line
contains the marker `Expected output` — the shape every README in this repo
already uses, so no new syntax was invented. A README that merely *mentions* the
marker in prose is not affected; only a marker sitting directly above a fence
counts as a claim.

## The three verdicts

Two states would be a bug. In a bare checkout, `typed-tool-registry`'s self-test
exits 1 with empty stdout (`ModuleNotFoundError: No module named 'anthropic'`),
and empty stdout is certainly not the documented transcript — so a two-state
comparator reports **drift** on a README that may be perfectly correct and merely
unrunnable here. That is a false accusation, and it would fire on every machine
that has not installed that example's dependencies, which is most of them.

| Verdict | When | Exit |
|---|---|---|
| `Match` | the command exited 0 and stdout equals the documented block exactly | 0 |
| `Drift(expected, actual, diff)` | the command exited 0 and printed something else | 1 |
| `Unrunnable(exit_code, stderr_tail)` | the command exited non-zero; the README was **not** judged | 2 |

A non-zero exit is never reported as drift, even when stdout also differs. The
three are a tagged union, so "unrunnable but also drifted" is not representable.

Three more codes exist for the checker's own failures, kept disjoint from the
verdicts so automation can never confuse "your README is wrong" with "I could not
run the check":

| Exit | Meaning |
|---|---|
| 64 | bad usage (`EX_USAGE`) |
| 65 | no README, no marked block, or two marked blocks (`EX_DATAERR`) |
| 70 | the check itself could not run — command not found, timeout (`EX_SOFTWARE`) |

`check()` itself lets `OSError` and `TimeoutExpired` propagate untouched, exactly
as the spec requires; only `main()`, the outermost shell, turns them into exit 70
with a message. Mapping them to a *verdict* would be the lie; giving the CLI a
distinct exit code for them is not.

## Two deliberate refusals

**Exact string equality, no normalization.** Blank lines, trailing whitespace and
the final newline all count. Go's testable examples — the design this copies —
offer `// Unordered output:` as an explicit opt-in escape hatch rather than a
fuzzy default, because a comparator that is fuzzy by default stops catching the
thing it exists to catch. The only concession is that the README's line endings
are read as `\n`, matching how the subprocess's stdout is captured.

**There is no `--update` flag.** A checker that can rewrite the documentation it
disagrees with lets a genuine regression re-document itself as correct, silently,
and then reports green. Fixing a drifted README is a human edit that shows up in
a diff. `check()` never opens the README for writing, and the end-to-end test
asserts the file's bytes are unchanged after a run.

**And one refusal to guess.** A README carrying two marked blocks gets
`AmbiguousTranscript` naming the count, and exit 65 — never a silent check of
whichever came first.

The research note expected `mcp-connect-claude-code` to be that case. Measured
here, it is not: it mentions the marker twice, but only one of those sits above a
fence, and that one is the **billed live-API** transcript. So the hazard there is
the opposite of ambiguity — pointed at that README the checker would cheerfully
compare against output that was never reproducible offline. Across all 19 example
READMEs today, 15 carry exactly one marked block, 4 carry none, and none are
ambiguous; the `AmbiguousTranscript` path is real and tested, but currently only
by fixture.

## The fourth answer: opting out

Go's answer to the unreproducible case is that omitting the output comment means
"compile but do not run" — a legitimate third state rather than a failure. Here
it is a directive on the line **above** the marker:

```
<!-- transcript-check: skip — billed live-API run, captured once during this build; see run_e2e.sh -->
Expected output (verified during this build):
(...the fenced transcript, unchanged, follows here...)
```

`extract_transcript` then raises `TranscriptOptOut(reason)` instead of returning
a block, and the sweep reports `OPT-OUT` with the author's reason rather than
either checking it (a lie) or failing it (a false accusation). It is an HTML
comment, so nothing changes for a human reading the rendered README.

Three details are load-bearing:

- **A reason is mandatory.** `TranscriptOptOut("")` raises `ValueError`. A skip
  with no stated reason is exactly the kind of thing that is still there,
  unexplained, two years later.
- **Above the marker, not below it.** Between the marker and the fence, the
  directive would become the fence's nearest preceding line and the block would
  stop being marked at all — so that placement is not silently accepted, it
  reports `NO TRANSCRIPT`. Pinned by `test_the_directive_must_sit_above_the_marker`.
- **Ambiguity still wins.** A README with two marked blocks, one of them opted
  out, is reported ambiguous. The opt-out excuses a block from being checked; it
  does not excuse a README from having one transcript.

The one directive in the repo today is
[`mcp-connect-claude-code`](../mcp-connect-claude-code/)'s.

## Sweeping the whole repo

`check_transcript.py` needs you to name the command. `sweep.py` reads it out of
the README instead, so the whole portfolio can be checked in one pass:

```bash
cd examples/readme-transcript-check
python3 sweep.py                       # every example under ../
python3 sweep.py --only tool-error-policy,typed-tool-registry
python3 sweep.py --scratch /tmp/sweep  # keep the venvs for the next run
```

which prints a counts line, one line per example, and then the findings.
Abridged here — a real run lists all nineteen, and the numbers move as
examples land, so nothing checks this block:

```
Transcripts: 14 match / 0 drift / 0 unrunnable / 1 opt-out / 4 no-transcript / 0 error of 19

MATCH         context-editing-preview
OPT-OUT       mcp-connect-claude-code             billed live-API run, captured once during this build; see run_e2e.sh
NO TRANSCRIPT mcp-hn-search
MATCH         typed-tool-registry

No findings: every transcript that could be checked matched its output.
```

Exit 0 when there are no findings, 1 when there is at least one, 64 when a flag
names an example that does not exist, and 70 when a virtualenv could not be
built. The last two are kept off 1 for the same reason the checker keeps its
own failures off the verdict codes: "I could not run the sweep" must never be
readable as "your README is wrong", nor as "clean".

**How it finds the command.** The run blocks are not uniform (`python` vs
`python3`, an inline `pip install`, an inline `python3 -m venv .venv`), so the
rule is narrow: *the last whitespace-delimited `*.py` token in the fenced block
immediately preceding the marked transcript block.* That yields `test_preview.py`,
`test_placement.py`, `test_server.py`, `test_compaction.py`, `test_agent.py`… for
all 14 checkable examples. The sweep then supplies the **interpreter** itself
rather than executing the README's shell: a scratch virtualenv's `python` when
the example has a `requirements.txt`, plain `python3` otherwise. A block with no
`*.py` token is `CommandMissing` — a finding — never a guessed `test_<dirname>.py`.

**Where the virtualenvs go.** Under `--scratch` (default: a temporary directory,
deleted on exit), never inside an example — the health check must not leave a
`.venv/` lying around. Each venv records the `requirements.txt` it was built
from, so a second example, a second run against the same `--scratch`, or a build
that died halfway all do the right thing without a `--force` flag.

**What a full run costs.** Nothing, in API terms: every command the sweep runs is
an example's *offline* self-test — mocked, fixtured or stdlib-only. The billed
paths (`agent.py`, `run_e2e.sh`) are never invoked; the one README whose only
transcript is billed opts out. The one thing a full run does need is **network,
for `pip`**, to build ~13 virtualenvs. `--only` a stdlib-only example, or
`test_sweep.py`, needs neither:

```bash
cd examples/readme-transcript-check
python3 test_sweep.py     # 14 assertions, no key, no network, no venv
```

**Into the nightly health check.** `.pipeline/health.sh` already turns every
`- FAIL ` line under `## Example results` into a backlog item, so the sweep emits
findings in exactly that shape for the health agent to copy verbatim:

```
- FAIL  examples/foo/ — README transcript drift: line 6: README has 'All 4 self-tests passed.', output has 'All 6 self-tests passed.'
```

`OPT-OUT` and `NO TRANSCRIPT` lines are deliberately not findings, and produce no
`- FAIL`.

## Checking the checker with the checker

This README documents its own self-test, in the same shape as every other
example, so it is its own test case:

```bash
cd examples/readme-transcript-check
python3 check_transcript.py . -- python3 test_check_transcript.py
```

If someone adds an eleventh test and forgets this file, that command goes from
exit 0 to exit 1 with a one-line diff.

## Known limits

- **`tool-error-policy`'s transcript ends `...passed in 0ms.`**, a measured
  duration. It is stably `0ms` on this machine — the first full sweep matched it
  — but exact match makes that line a hostage to a slower one. Machine-dependent
  values in a documented transcript are a wart in the *transcript*, not in the
  comparator, and the sweep would report it as drift on the machine where it
  finally bites.
- **All 14 checkable transcripts matched on the first full sweep** (2026-09-09),
  which is the first time twelve of them were ever run through a checker. That is
  a measurement of one machine on one day, not a proof of determinism.
- **A marker inside a fenced block is counted as a marker.** The rule is "the
  nearest preceding non-blank line", and it does not know it is inside a code
  block — so a README whose *last line before a closing fence* contains
  `Expected output` is reported ambiguous. That is why the directive example
  above ends on a `(...)` line. No README in the repo hits this accidentally.
- **The command rule is one narrow heuristic.** The last `*.py` token in the
  block above the transcript. It covers all 14 today; a README that documents a
  non-Python command, or two commands where only one is transcribed, gets
  `CommandMissing` rather than a guess.
- **The sweep runs, but nothing runs the sweep on a schedule.** The health agent
  is told to (`.claude/agents/agentlab-health.md` §1); `.pipeline/health.sh` is
  unchanged and does not invoke it directly.

## Explicitly out of scope

Regex or fuzzy matching, an update mode, multi-block READMEs, stderr comparison,
CI wiring, honouring the `pip`/`venv` lines a README actually writes (the sweep
substitutes its own interpreter), and running any billed command — including the
live transcript in [`mcp-connect-claude-code`](../mcp-connect-claude-code/),
which opts out.
