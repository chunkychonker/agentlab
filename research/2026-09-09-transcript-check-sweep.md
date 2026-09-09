# Repo-wide README transcript sweep, and the opt-out marker it needs

**Date:** 2026-09-09
**Backlog item:** "Teach the health check to run
`examples/readme-transcript-check/check_transcript.py` over every example README
instead of spot-checking transcripts by hand" (## Pipeline & repo hygiene — the
topmost unclaimed `[ ]`; no open PR covers it — #36/#39/#40 are unrelated).

## Question

`examples/readme-transcript-check/check_transcript.py` (PR from the 2026-08-11
note) verifies **one** README's documented transcript against **one** command.
The note deferred the repo-wide sweep because it "needs per-example venvs, which
is the health check's existing machinery". What is the smallest increment that
turns the single-README checker into a sweep the health phase can run, and what
has to change in `check_transcript.py` for the sweep to handle the one README
(`mcp-connect-claude-code`) whose only documented transcript is a billed live run?

## Findings

### 1. What exists today

`check_transcript.py` is a pure core (`extract_transcript`, `compare`,
`exit_code`, `format_verdict`) under a thin shell (`check`, `main`):

- `extract_transcript(readme_text, *, marker="Expected output") -> str` — returns
  the body of the single fenced block whose **nearest preceding non-blank line
  contains** `marker`. Raises `TranscriptNotFound` (0 marked blocks / unclosed
  fence) or `AmbiguousTranscript` (>1 marked block).
- `compare(expected, actual) -> Match | Drift | Unrunnable` — exact string
  equality, no normalization beyond `\n` line endings. `Unrunnable` is returned
  by `check()` (not `compare`) whenever the command exits non-zero, **before**
  comparing output — a missing dependency is never reported as drift.
- `check(readme_path, command, cwd, *, marker, timeout=120)` runs `command` in
  `cwd` and judges the README. Never writes the README (no `--update` by design).
- CLI: `python3 check_transcript.py <example-dir> -- <command...>`; exit codes
  0 match / 1 drift / 2 unrunnable / 64 usage / 65 no-single-transcript / 70
  check-couldn't-run.

Its self-test is 9 fixture assertions + **1 real end-to-end** that points the
checker at `examples/minimal-agent-loop/` (stdlib-only, no venv) and asserts
`Match`. Stdlib only.

### 2. Current ground truth across `examples/` (measured this cycle)

I ran `extract_transcript` against all 18 example READMEs besides
`readme-transcript-check` itself (which extracts cleanly to one block and
`check_transcript.py . -- python3 test_check_transcript.py` returns `MATCH`
today):

- **14 have exactly one marked block** (checkable): `context-editing-preview`,
  `mcp-connect-claude-code`, `mcp-hello-world`, `mcp-resources-vs-tools`,
  `minimal-agent-loop`, `orchestrator-subagents`, `prompt-caching-tool-loop`,
  `server-side-compaction`, `skill-anatomy`, `skill-reference-files-bigquery`,
  `skill-script-execution-word-counter`, `streaming-tool-loop`,
  `tool-error-policy`, `typed-tool-registry`.
- **4 have no marked block** (not a finding — nothing to check): `mcp-hn-search`,
  `skill-permission-suppression`, `skill-reference-files`, `skill-script-execution`.
- **0 are ambiguous.**
- **`mcp-connect-claude-code`'s one marked block is the billed live transcript**
  (`--- attempt 1/2 --- PASS: mcp server connected ...`), introduced by
  `Expected output (verified during this build):` above the `./run_e2e.sh`
  block. Pointed at that README the checker would happily "verify" against output
  that is not reproducible offline — the hazard the `readme-transcript-check`
  README already calls out as *the opposite of ambiguity*.

So the sweep's shape is: 14 real checks + 1 opt-out (`mcp-connect-claude-code`)
+ 4 "no transcript".

### 3. The command per example is a non-uniform shell snippet — but one narrow rule extracts it

The health agent today "reads the README for the documented self-test command —
don't guess a convention". The command blocks are genuinely varied:

```
context-editing-preview     python test_preview.py
prompt-caching-tool-loop    python3 test_placement.py         (test_report.py has no transcript block)
mcp-hello-world             python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
                            .venv/bin/python test_server.py
orchestrator-subagents      pip install -r requirements.txt
                            python test_agent.py
typed-tool-registry         pip install -r requirements.txt
                            python test_agent.py
```

`python` vs `python3`, inline `pip install`, inline `venv` creation. Parsing the
block into a runnable shell command is fiddly and per-example. **But one rule
covers all 14:** *the test script is the last whitespace-delimited `*.py` token
in the fenced code block immediately preceding the marked "Expected output"
block.* Verified by hand against all 14 — it yields `test_preview.py`,
`test_placement.py`, `test_server.py`, `test_agent.py`, `test_compaction.py`,
`test_validate_skill.py`, `test_check_references.py`… every time. The sweep then
supplies the **interpreter** itself (a scratch-venv `python` when the example has
`requirements.txt`, plain `python3` otherwise) rather than trying to honour the
README's `pip`/`venv` lines. `mcp-connect-claude-code`'s preceding block has no
`.py` token at all — which is fine, because the opt-out directive (below) is
detected first and that example never reaches command extraction.

### 4. The opt-out marker: Go's precedent is still the right one, and still current

Go's testable examples remain the canonical design and the mechanism is
unchanged in 2025–2026: *"Without an output comment, the example is compiled but
isn't executed"*, and *"if there is a space between 'Output' and ':', the example
test is not run"*
([Go testable examples, golangspec / Medium](https://medium.com/golangspec/gos-testable-examples-under-the-hood-4a4db8db447f);
corroborated in a 2026-07 Go proposal write-up,
[rednafi.com](https://rednafi.com/shards/2026/07/go-example-any-signature/)). The
lesson carried into `knowledge/doc-transcript-drift.md` already: *"No verifiable
output is a legitimate third state, not a failure… model this explicitly rather
than forcing every block to be checkable."*

Current practitioner tools in this exact space (README/markdown blocks executed
in CI) confirm the pattern is alive:
[`pytest-codeblocks`](https://github.com/nschloe/pytest-codeblocks) (Python + shell
blocks under pytest),
[RUNME "ReadmeOps"](https://runme.dev/blog/readmeops-testing-docs-in-ci)
(2024–2025, runs README steps in CI/CD),
[`bbt`](https://github.com/LionelDraghi/bbt) ("Show HN", 2026-02 — "When I run X
→ Then the output contains Y", tests embedded in Markdown). None is worth a
dependency for ~14 uniform blocks, and none models the MATCH/DRIFT/**UNRUNNABLE**
taxonomy this repo needs — the same conclusion the 2026-08-11 note reached.
Practitioner *consensus* here is thin (bbt has 3 points / 0 comments), so the
case rests on the measured local base rate and the Go precedent, not on hype.

**Design chosen:** an explicit HTML-comment directive on the line immediately
above the `Expected output` marker line:

```
<!-- transcript-check: skip — billed live-API run, not reproducible offline -->
Expected output (verified during this build):
```

`check_transcript.py` gains a `TranscriptOptOut(reason)` exception. Detection is
additive and orthogonal to the existing fence-matching: after
`extract_transcript` locates the single marked block, if the nearest non-blank
line *above the marker line* starts with `<!-- transcript-check: skip`, raise
`TranscriptOptOut` carrying the text after `skip`. Every README without the
directive behaves exactly as today (expand-only; no consumer breaks).

### 5. The determinism sub-question is answered *by running the sweep*, not by a separate audit

The note only ever verified `minimal-agent-loop` and `typed-tool-registry` as
deterministic. The other twelve "look list-driven and stable but were not run".
The sweep **is** that verification: run it over all 14 and any non-determinism
surfaces as `DRIFT`. One known wart to expect, already documented in the
`readme-transcript-check` README: `tool-error-policy`'s transcript ends
`...passed in 0ms.` — a measured duration that is stably `0ms` on the pipeline
box but is a hostage to a slower machine. If the sweep flags it, the fix is to
the *transcript* (drop the timing line), not the comparator — a normal follow-up
backlog item, not this cycle's problem.

### 6. Findings already have a path into the backlog — no bash change needed

`.pipeline/health.sh:health_findings()` already parses `- FAIL ` lines under
`## Example results` and turns each into a backlog item (via `backlog.sh`). A
drifted transcript fits the health agent's existing FAIL definition verbatim
("output contradicts the README"). So the only wiring needed is a short
instruction in `.claude/agents/agentlab-health.md` telling the agent to run the
sweep once and emit each `DRIFT`/`UNRUNNABLE` as
`- FAIL  examples/<name>/ — README transcript drift: <detail>` in that section.
`OPT-OUT` and `NO TRANSCRIPT` lines are explicitly *not* findings. No change to
`health.sh`, `backlog.sh`, or `run.sh`.

### 7. No Anthropic API surface in this increment

The sweep composes `check_transcript.py` and runs example **self-tests**, every
one of which is offline / mocked / no-key (the billed paths — `run_e2e.sh`,
live `agent.py` — are separate and are the opt-out / not-swept case). So a full
real sweep costs **$0** (only `pip install` needs network), and the `claude-api`
skill's model-id/param/pricing guidance does not apply here. Flagging this so the
builder does not go looking for a model constant to pin.

## Build proposal

Extends the existing `examples/readme-transcript-check/` (composes
`check_transcript.py`; **not** a new `examples/` dir, so no name collision to
check). Three parts, in priority order; parts 1–2 are the must-ship, part 3 is a
~10-line instruction edit.

### Layer 1 — Intent

Turn the single-README transcript checker into a driver that sweeps every
`examples/*/` README with a documented transcript, building a scratch venv per
example that needs one, and reports one line per example plus aggregate counts;
and give `check_transcript.py` an explicit opt-out directive so a README whose
only transcript is a non-reproducible billed run is reported as `OPT-OUT`, not
falsely checked. **Out of scope:** changing `compare`/`Match`/`Drift`/`Unrunnable`
semantics; fuzzy matching; an `--update` mode; multi-block READMEs; fixing any
transcript the sweep flags (that is a follow-up backlog item, per the health
check's "never fixes" rule); rewriting `health.sh`/`backlog.sh`/`run.sh`;
`test_report.py`-style second commands that have no documented block.

### Layer 2 — Behavioral spec

**New in `check_transcript.py`:**

- `class TranscriptOptOut(Exception)` — carries `reason: str`.
- `extract_transcript` raises `TranscriptOptOut` when the single marked block is
  preceded (nearest non-blank line above the *marker* line) by a line beginning
  `<!-- transcript-check: skip`. `reason` is the text between `skip` and `-->`,
  stripped (leading `—`/`-`/`:` and spaces removed). Everything else about
  `extract_transcript` is unchanged.
- Acceptance:
  1. `extract_transcript` on a fixture README with the directive raises
     `TranscriptOptOut` whose `reason` is the fixture's reason string exactly.
  2. `extract_transcript` on the same README *without* the directive line still
     returns the block (proves expand-only).
  3. A README with the directive but **two** marked blocks still raises
     `AmbiguousTranscript` (ambiguity check precedes opt-out).
  4. Existing 10 self-tests still pass unchanged.

**New file `sweep.py`:**

- Pure core (no I/O):
  - `command_script(readme_text: str) -> str` — the last `*.py` token in the
    fenced block immediately preceding the marked "Expected output" block. Raises
    `CommandNotFound` if there is no preceding fenced block or it has no `*.py`
    token.
  - `plan_target(readme_text: str) -> Target` where
    `Target = Checkable(script: str) | OptOut(reason: str) | NoTranscript() |
    Ambiguous(count: int) | CommandMissing()` — dispatches on
    `extract_transcript`'s outcome (`TranscriptNotFound`→`NoTranscript`,
    `AmbiguousTranscript`→`Ambiguous`, `TranscriptOptOut`→`OptOut`, success→
    `command_script` then `Checkable`/`CommandMissing`).
  - `summarize(results: Sequence[tuple[str, Outcome]]) -> str` — a report:
    a counts line
    `Transcripts: <m> match / <d> drift / <u> unrunnable / <o> opt-out / <n> no-transcript / <e> error of <total>`,
    then one human line per example, then a machine-greppable
    `- FAIL  examples/<name>/ — <reason>` line for every `Drift`, `Unrunnable`,
    `Ambiguous`, and `CommandMissing` (nothing for `Match`, `OptOut`,
    `NoTranscript`) so `.pipeline/health.sh` can lift them verbatim.
- Imperative shell:
  - `build_interpreter(example_dir: Path, scratch_root: Path) -> list[str]` —
    `[<scratch venv>/bin/python]` after `python3 -m venv` + `pip install -q -r
    requirements.txt` when `requirements.txt` exists; else `["python3"]`.
    Idempotent per `(example_dir, scratch_root)`. Failure modes stated in the
    docstring (venv creation / pip failure → raise, do not map to a verdict).
  - `run_sweep(examples_root: Path, scratch_root: Path, *, only: Sequence[str]
    | None = None) -> list[tuple[str, Outcome]]` — for each dir under
    `examples_root` (or just `only`), `plan_target`; for `Checkable`, call
    `check_transcript.check(readme, build_interpreter(...) + [script], example_dir)`.
    Results in directory-sorted order.
  - `main(argv) -> int` — flags `--examples-root`, `--scratch DIR` (default a
    `tempfile.mkdtemp`), `--only a,b`; prints `summarize(...)`; exit 0 iff no
    `- FAIL` line was emitted, else 1.
- Invariants: `sweep.py` never writes any README (inherited from `check`); the
  pure core touches no filesystem/subprocess/clock; a non-zero example exit is
  `Unrunnable`, never `Drift` (inherited).
- Failure modes: `CommandNotFound`/`Ambiguous`/`CommandMissing` are *reported*
  outcomes, not exceptions out of `run_sweep`; venv/pip errors propagate from
  `build_interpreter`; `check_transcript`'s `OSError`/`TimeoutExpired` propagate.

**Acceptance criteria (each an assertion in `test_sweep.py`, stdlib only):**

1. `command_script` returns `test_agent.py` from a `python test_agent.py` block.
2. `command_script` returns `test_server.py` from a
   `.venv/bin/python test_server.py` block (last `.py` token wins).
3. `command_script` raises `CommandNotFound` for a block whose only lines are
   `cd ...` and `./run_e2e.sh` (the `mcp-connect-claude-code` shape).
4. `plan_target` → `NoTranscript()` for a README with no marker.
5. `plan_target` → `OptOut("billed live-API run, not reproducible offline")` for
   a README carrying the directive.
6. `plan_target` → `Checkable("test_x.py")` for the normal shape.
7. `plan_target` → `Ambiguous(2)` for two marked blocks.
8. `summarize` emits exactly one `- FAIL  examples/foo/ — ` line for a `Drift`
   outcome and **none** for `Match` / `OptOut` / `NoTranscript`.
9. `summarize`'s counts line reflects a mixed result list (m/d/u/o/n/e all
   exercised).
10. **Real end-to-end, no fixtures:** `run_sweep(<repo>/examples, tmp,
    only=["minimal-agent-loop", "readme-transcript-check"])` returns `Match` for
    both (both stdlib-only, so no venv is built) and neither README's bytes
    change. This is the load-bearing test.
11. `test_check_transcript.py` and `test_sweep.py` both pass from a bare
    interpreter (`python3 test_sweep.py`), no key, no network, no venv.

### Layer 3 — Interfaces (no bodies)

```python
# check_transcript.py  (additions only)
class TranscriptOptOut(Exception):
    def __init__(self, reason: str) -> None: ...
    reason: str
# extract_transcript(...) gains one raise-path; signature unchanged.

# sweep.py
DIRECTIVE_PREFIX = "<!-- transcript-check: skip"

class CommandNotFound(Exception): ...

@dataclasses.dataclass(frozen=True)
class Checkable:      script: str
@dataclasses.dataclass(frozen=True)
class OptOut:         reason: str
@dataclasses.dataclass(frozen=True)
class NoTranscript:   ...
@dataclasses.dataclass(frozen=True)
class Ambiguous:      count: int
@dataclasses.dataclass(frozen=True)
class CommandMissing: ...
Target = Checkable | OptOut | NoTranscript | Ambiguous | CommandMissing

# Outcome = check_transcript.Verdict | OptOut | NoTranscript | Ambiguous | CommandMissing

def command_script(readme_text: str) -> str: ...
def plan_target(readme_text: str) -> Target: ...
def summarize(results: Sequence[tuple[str, "Outcome"]]) -> str: ...

def build_interpreter(example_dir: Path, scratch_root: Path) -> list[str]: ...
def run_sweep(examples_root: Path, scratch_root: Path, *,
              only: Sequence[str] | None = None) -> list[tuple[str, "Outcome"]]: ...
def main(argv: Sequence[str]) -> int: ...
```

CLI: `python3 sweep.py` (from `examples/readme-transcript-check/`) sweeps the
whole `examples/` tree; `--only minimal-agent-loop` restricts it.

### Part 3 — wire it into the health check (~10 lines, do last)

- `examples/readme-transcript-check/README.md`: new "Sweeping the whole repo"
  section — what `sweep.py` does, the command-extraction rule, the opt-out
  directive, and that a full run needs network for `pip` but costs $0 in API
  terms. Add `sweep.py` / `test_sweep.py` to the file table. Keep the existing
  "Known limits" bullet honest (it currently says the sweep is "deliberately not
  in this increment" — update it).
- `examples/mcp-connect-claude-code/README.md`: add the
  `<!-- transcript-check: skip — billed live-API run, captured once during this
  build; see run_e2e.sh -->` line directly above `Expected output (verified
  during this build):`. One line; changes nothing rendered.
- `.claude/agents/agentlab-health.md` §1 ("Every example still runs"): add a
  bullet — after the per-example run, also run
  `python3 examples/readme-transcript-check/sweep.py` once (it builds its own
  scratch venvs) and fold each `DRIFT`/`UNRUNNABLE` line into `## Example
  results` as `- FAIL  examples/<name>/ — README transcript drift: <detail>`;
  `OPT-OUT` and `NO TRANSCRIPT` are not findings.

### "It works" =

`python3 test_sweep.py` passes from a bare interpreter (criteria 1–11, incl. the
real 2-example run at #10); `python3 test_check_transcript.py` still prints "All
10"; and the builder runs the full `python3 sweep.py` once for real and pastes
its output into the PR body (expected: 14 checks — mostly `MATCH`, possibly a
`DRIFT` on `tool-error-policy`'s `0ms` line — plus `OPT-OUT mcp-connect-claude-code`
and 4 `NO TRANSCRIPT`). If the build box has no network for `pip`, ship on the
offline suite alone and say so, exactly as `check_transcript.py` originally did.

## Open questions

- **Does the full sweep flag anything beyond `tool-error-policy`'s timing line?**
  Twelve transcripts have never been run through the checker. The sweep is the
  first time they will be; genuine drift found on day one is a *result*, filed as
  a normal backlog item, not a reason to hold the increment.
- **Directive placement — above vs. same-line.** I picked "nearest non-blank line
  above the marker line" for a clean, orthogonal check. Putting it *after* the
  marker (between marker and fence) would also work and might read better; the
  builder may swap if the implementation is simpler, as long as `test_sweep.py`
  pins whichever is chosen.
- **`build_interpreter` reuse of venvs across a run.** Spec says idempotent per
  `(example_dir, scratch_root)`; whether to cache within one `run_sweep` call or
  rebuild per example is a perf choice, not a correctness one — left to the
  builder. A full sweep builds ~13 venvs; that is the health check's existing
  cost profile, run once every 7 nights.
- **`hn-search` MCP server was unavailable this cycle** (`mcp__hn-search__*`
  tools not registered — the missing-`.venv` problem the 2026-08-11 note already
  recorded). Practitioner reception in §4 is from WebSearch + a direct HN Algolia
  query only.

## Sources

- `examples/readme-transcript-check/{check_transcript.py,test_check_transcript.py,README.md}`
  and `research/2026-08-11-readme-transcript-drift.md` (this repo) — the checker
  being extended and its design rationale.
- `.pipeline/health.sh`, `.claude/agents/agentlab-health.md` (this repo) — how a
  `- FAIL` line under `## Example results` becomes a backlog item.
- `knowledge/doc-transcript-drift.md` (this repo) — the MATCH/DRIFT/UNRUNNABLE
  taxonomy and the "no verifiable output is a legitimate third state" rule.
- [Go's Testable Examples under the hood — golangspec / Medium](https://medium.com/golangspec/gos-testable-examples-under-the-hood-4a4db8db447f)
  (design precedent; mechanism unchanged — old but current).
- [Go examples with any signature (accepted proposal) — rednafi.com](https://rednafi.com/shards/2026/07/go-example-any-signature/)
  (2026-07; confirms the "no Output comment ⇒ compiled but not run" rule is still live).
- [pytest-codeblocks](https://github.com/nschloe/pytest-codeblocks) — current tool, Python + shell blocks from READMEs under pytest.
- [ReadmeOps: Integration Testing Markdown Docs in CI/CD — RUNME](https://runme.dev/blog/readmeops-testing-docs-in-ci) (2024–2025).
- [Show HN: bbt – Black Box Testing Directly from Your Documentation](https://github.com/LionelDraghi/bbt) (2026-02; 3 points, 0 comments — pattern is alive, consensus is thin).
- [How to test code blocks in documentation — Percona Community](https://percona.community/blog/2023/02/28/doc-testing/) (2023; background).
