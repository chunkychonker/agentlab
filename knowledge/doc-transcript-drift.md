# Documentation transcript drift

A README that pastes a program's output is an unversioned copy of that output. It is
correct on the day it lands and decays silently afterwards. This note records the
failure mode, why a nightly-diff reviewer structurally cannot catch it, and the
verdict taxonomy a checker needs to be useful rather than noisy.

## The failure mode

Measured in this repo on 2026-08-11: **9 of 13 example READMEs** carry a fenced
"Expected output:" block holding a deterministic offline self-test transcript
(`ok  <claim>` lines + `All N self-tests passed.`). One of the nine
(`typed-tool-registry`) was already wrong — README said `All 4`, the suite emitted
`All 6` — and had been wrong since the example landed.

The tell: the *code* was self-updating (`print(f"\nAll {len(tests)} self-tests passed.")`),
the README was frozen. Whenever the source of truth computes a value and the doc
hardcodes it, the doc is a latent lie.

## Why nightly review cannot catch it

The pipeline's reviewer sees only that night's working-tree diff (see `PIPELINE.md`).
A transcript that was true when it landed and drifted later never appears in a
subsequent diff, so no reviewer ever looks at it again. Periodic lateral sweeps (the
health check) are the only thing that can find it — and only if the sweep is a
re-runnable check rather than a human-style comparison.

**Generalization:** any invariant that spans two files which are never edited
together is invisible to diff-scoped review. Those invariants need a test, not a
reviewer instruction.

## Design: three verdicts, not two

The naive comparator (`run command; string ==`) is wrong in one specific way that
makes it unusable. Verified locally: in an environment without that example's
dependencies installed, `python3 test_agent.py` exits 1 with **empty stdout**
(`ModuleNotFoundError`). A two-state MATCH/DRIFT comparator reports this as drift —
a false accusation, since the README may be perfectly accurate.

```
Match        exit 0, stdout == documented block
Drift        exit 0, stdout != documented block
Unrunnable   exit != 0                            <- never report this as Drift
```

`Unrunnable` must be checked *before* comparing output, not after.

## Rules worth keeping

- **Exact string match, including blank lines and the trailing newline.** No
  stripping, no regex, no normalization. Fuzzy-by-default comparators stop catching
  the thing they exist for. If nondeterminism is genuinely present, make it an
  explicit opt-in escape hatch (Go's `// Unordered output:`), never the default.
- **No `--update` / auto-accept flag.** A blessing flag lets a real regression
  rewrite its own documentation into agreement. The whole value is that a human has
  to look.
- **"No verifiable output" is a legitimate third state, not a failure.** Some
  documented transcripts are from billed live runs and are not reproducible. Go's
  precedent: an example with no `// Output:` comment is compiled but not executed.
  Model this explicitly rather than forcing every block to be checkable.
- **Dependency-free checkers get run.** A verifier that itself needs a venv will be
  skipped exactly when the environment is degraded. Keep it stdlib-only.

## Prior art (the design is settled)

- **Go testable examples** — the canonical form: captured stdout compared against a
  trailing `// Output:` comment, exact match, with `// Unordered output:` as the one
  escape hatch. [go.dev/blog/examples](https://go.dev/blog/examples) (2015; old, but
  the mechanism is unchanged).
- **Python `doctest`** — the ancestor Go's design cites. `pytest --doctest-glob="*.md"`
  runs markdown, but only `>>>` REPL sessions, not shell-command transcripts.
- **`phmdoctest`** pairs a markdown code block with the following output block; the
  closest match in shape. `bashtestmd` has a `bashtestmd:compare-output` tag doing
  exactly this for shell blocks.

For a handful of uniform blocks in one repo, none of these is worth a dependency —
the whole comparison is extract-block + run + `==`. The interesting part is the
verdict taxonomy above, which the off-the-shelf tools do not model.

## Related

- [[tool-use-loop]] and [[typed-tool-registry]] — examples whose READMEs carry the
  transcripts in question
- [[tool-failure-taxonomy]] — same underlying instinct: refuse to collapse
  distinguishable failure classes into one bucket, because the response differs
- [[pipeline-claim-lifecycle]] — another invariant spanning files that are never
  edited together, and so invisible to diff-scoped nightly review
