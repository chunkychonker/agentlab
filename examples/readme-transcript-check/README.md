# README transcript check (executable documentation, three verdicts)

Every example README in this repo hand-copies its self-test transcript into a
fenced block. Nothing binds that copy to the program, so it rots quietly:
`typed-tool-registry`'s block claimed **"All 4 self-tests passed"** for nine days
while the suite actually printed **"All 6"**. The nightly reviewer only ever sees
that night's diff, so a transcript that was correct when it landed and drifted
later is invisible forever after.

This is a single-file, dependency-free checker that extracts one README's
documented transcript, runs the command that is supposed to produce it, and
compares the two with `==`.

From the research note:
[`research/2026-08-11-readme-transcript-drift.md`](../../research/2026-08-11-readme-transcript-drift.md).

## What's here

| File | What it is |
|------|-----------|
| `check_transcript.py` | The checker. Pure core (`extract_transcript`, `compare`, `exit_code`, `format_verdict`) above an imperative shell (`check`, `main`) that owns the filesystem and the subprocess. |
| `test_check_transcript.py` | Offline self-test: 10 assertions, one per acceptance criterion. Stdlib only, no key, no network. |

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
compare against output that was never reproducible offline. Across all 14 example
READMEs today, 11 carry exactly one marked block, 3 carry none, and none are
ambiguous; the `AmbiguousTranscript` path is real and tested, but currently only
by fixture.

Go's answer to the unreproducible case is that omitting the output comment means
"compile but do not run" — a legitimate third state rather than a failure. Adding
such an opt-out marker would mean inventing README syntax in the same increment
that introduces the checker, so it is left open in the note, and
`mcp-connect-claude-code` is simply not a target for this checker today.

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

- **One README, one command, per invocation.** Sweeping all 9 transcript-bearing
  examples needs a per-example virtualenv, which is the health check's existing
  machinery; teaching the health check to call this instead of hand-comparing is
  the natural follow-up and is deliberately not in this increment.
- **`tool-error-policy`'s transcript ends `...passed in 0ms.`**, a measured
  duration. It is stably `0ms` on this machine, but exact match makes that line a
  hostage to a slower one. Machine-dependent values in a documented transcript
  are a wart in the *transcript*, not in the comparator.
- **Only `minimal-agent-loop` and `typed-tool-registry` are verified
  deterministic.** The other seven look list-driven and stable but were not run.
- **Not wired into the nightly pipeline.** Nothing runs this automatically yet.

## Explicitly out of scope

Regex or fuzzy matching, an update mode, multi-block READMEs, stderr comparison,
per-example virtualenv provisioning, CI wiring, and the billed live transcript in
[`mcp-connect-claude-code`](../mcp-connect-claude-code/).
