# nightloop milestone 2: gates library

Date: 2026-11-09. Mode: `project:nightloop`. Milestone: 2 (gates library).

## Question

How should agentlab's two bash gates, `.pipeline/verdict.sh` (fail-closed
review verdict) and `.pipeline/postcondition.sh` (artifact freshness), be
ported to a stdlib-only, pure-core Python library that implements the
`Postcondition` enum from milestone 1, without losing any edge case
`test_gates.sh` already pins?

## Findings

This milestone is a port, so the primary sources are in-repo and no web
research was needed. I did not search the web, and no external source is cited.
Everything below was read from the repo on 2026-11-09.

**Verdict gate** (`.pipeline/verdict.sh`, cases V1-V13 in `.pipeline/test_gates.sh`):
- Output is one of PASS / FAIL / MISSING / MALFORMED. The bash return codes
  are 0 / 1 / 2 / 3.
- MISSING: file absent, unreadable, or zero bytes.
- MALFORMED: any non-empty file without exactly one `^VERDICT:` line at
  column 0, or whose word is not `PASS` or `FAIL`.
- Whitespace-only is MALFORMED, not MISSING (V2b). Zero bytes is MISSING (V2).
- Strict cases:
  - lowercase `pass` is MALFORMED (V8)
  - indented `  VERDICT: PASS` is MALFORMED (V10)
  - two VERDICT lines are MALFORMED even when they agree on nothing (V6)
  - prose containing "PASS" does not count (V11)
  - a later "PASS" in prose does not flip a FAIL (V12)
  - `VERDICT:PASS` with no space parses (V9)
- A gotcha in the bash regex: `sed 's/^VERDICT:[[:space:]]*\([A-Za-z]*\).*/\1/'`
  captures the whole letter run, so `VERDICT: PASSED` yields the word `PASSED`,
  which is MALFORMED. Python must do the same by capturing `[A-Za-z]*` and then comparing
  exactly, not by matching a `PASS` prefix. `VERDICT: PASS - ship it` is PASS
  because trailing text after a non-letter is ignored. The builder should pin
  both.
- Python-specific trap: `str.splitlines()` also splits on `\x0b`, `\x0c`,
  `\x1c-\x1e`, `\x85`, ` ` and ` `. `re.MULTILINE` `^` only splits
  on `\n`. Use `text.split("\n")` or `re.MULTILINE`. Otherwise a verdict line
  smuggled after a unicode line separator would count. `\r\n` files must still
  parse: the bash `[A-Za-z]*` capture stops at `\r`, so `VERDICT: PASS\r` is PASS.
- Non-UTF-8 bytes must not crash the gate. Decode with `errors="replace"`, or
  read bytes and match on bytes. A crash is not fail-closed in the library's
  contract (a raise would be a different outcome than the four words).

**Freshness gate** (`.pipeline/postcondition.sh`, cases PC1-PC14):
- `artifact_freshness(dir, glob, floor)` returns FRESH, STALE or MISSING.
- MISSING: dir absent or unreadable, no matching file, or the floor is invalid
  (non-numeric or negative, PC5/PC6).
- STALE: matches exist, but every mtime is below the floor. This is the
  2026-09-01 incident: `research/` was full of old notes, and only "newer than
  this phase's start" catches it (PC3).
- The floor is the phase's start time, not the run's start time. With 2 cycles a
  run-start floor lets cycle 1's note satisfy cycle 2.
- Prune `.venv`, `__pycache__`, `node_modules` and `.git` (PC11-PC13). Pruning
  must not hide real work next to them.
- Comparison is `mtime >= floor`, inclusive.
- The glob does not match other file types (PC7, a fresh `scratch.txt` does not
  count).
- A file whose mtime cannot be read counts as found but never as fresh.
- `increment_built` sweeps `examples` and `projects` and takes FRESH from either
  (PC10). It reports STALE if either root has only old work, and MISSING if
  neither has any file (PC8, PC9).
- `phase_no_postcondition` returns NONE (PC14).
- Bash `find -name` matches the basename and recurses. Python's `fnmatch` on the
  basename gives the same result. Do not use `Path.glob("*.md")` for the recursive
  case without `**`.
- Bash uses BSD `stat -f %m`. Python's `os.stat().st_mtime` is portable, which
  is a gain over the reference. Compare floats against an integer floor
  directly. Do not truncate the mtime to an int first, or a file written at
  `floor + 0.5` would still pass but one written at `floor - 0.5` would be
  rounded up to `floor` and wrongly pass. Truncating is exactly the bug.

**Fit with milestone 1** (`projects/nightloop/nightloop/model.py`):
`Postcondition` already has `RESEARCH_NOTE_FRESH`, `INCREMENT_BUILT`,
`REVIEW_VERDICT` and `NONE`, and a `PHASE_POSTCONDITIONS` map saying which
phase may use which. Decision log, 2026-11-06: stdlib-only, no test calls a model.
Milestone 3's runner will need a single entry point that, given a
`Postcondition` and the phase start time, returns a typed result. The library
therefore needs a dispatcher in addition to the two checks.

**Layering.** CLAUDE.md says core logic never imports I/O. Split into a pure
core (verdict text in, verdict out; a list of (path, mtime) in, state out) and a
thin filesystem shell that reads files and walks directories. The bash gates
mix both, and the pure halves are what is worth unit-testing exhaustively.

## Build proposal

### Intent

Provide the library that decides, without a model, whether a phase's output
authorises moving on. Out of scope: running phases (milestone 3), the cycle
loop, claim tracking, the CLI, and reading `.pipeline/` bash files. No
dependence on `.pipeline/` at runtime. The bash files remain the reference only.

### Behavioral spec

Location: `projects/nightloop/nightloop/gates/` (package), tests in
`projects/nightloop/tests/test_gates_*.py`. Python 3.11+, stdlib only.

Types (in `gates/outcome.py`):
- `Verdict` enum: `PASS`, `FAIL`, `MISSING`, `MALFORMED`.
- `Freshness` enum: `FRESH`, `STALE`, `MISSING`.
- `GateResult` frozen dataclass: `postcondition: Postcondition`,
  `outcome: str` (the enum member name), `advance: bool`, `detail: str` (one
  line for the log). `advance` is True only for `Verdict.PASS`, `Freshness.FRESH`,
  or `Postcondition.NONE`. It is the only field the orchestrator branches on.

Pure core:
- `parse_verdict(text: str | None) -> Verdict`.
  `None` means the file was absent or unreadable. `""` is MISSING. Otherwise
  follow the rules above exactly. Never raises.
- `classify_freshness(mtimes: Sequence[float | None], floor: int) -> Freshness`.
  `mtimes` are the matching files' mtimes, with `None` for unreadable. Empty is
  MISSING. Any value `>= floor` is FRESH. Otherwise STALE. The floor must be
  `int >= 0` (and not `bool`), else raise `ValueError`.
  Rationale for raising: the floor is the caller's bug and not data. The
  edge function `check_*` wrappers below turn it into MISSING only if that is
  chosen in review; the default proposal is to raise, because a bad floor is a
  programming error (matching the doc in `network_retry.sh`). If the reviewer
  prefers strict parity with PC5/PC6, return MISSING instead. The builder must
  pick one, log it in `PLAN.md`'s decisions log, and test it.

Filesystem shell (`gates/fs.py`):
- `check_verdict(path: Path) -> Verdict`: read bytes, decode with
  `errors="replace"`, call `parse_verdict`. Absent, directory or unreadable
  gives MISSING.
- `check_freshness(root: Path, patterns: str, floor: int) -> Freshness`: walk
  with `os.walk`, pruning `PRUNED_DIRS = {".venv", "__pycache__", "node_modules", ".git"}`,
  `fnmatch` on the basename, `os.stat` per file, `OSError` becomes `None`.
  Missing root or non-directory gives MISSING. Do not follow symlinked directories.
- `check_increment(roots: Sequence[Path], floor: int) -> Freshness`: any root
  FRESH is FRESH; else any STALE is STALE; else MISSING.

Dispatcher (`gates/dispatch.py`):
- `evaluate(post: Postcondition, repo: Path, phase_started: int, verdict_path: Path) -> GateResult`.
  Defaults, named once in `gates/constants.py`: research dir `research`, glob
  `*.md`, increment roots `examples` and `projects`, increment glob `*`.
  These mirror `postcondition.sh` and are parameters of the shell functions,
  not config, in this milestone.
- `NONE` returns `advance=True` without touching the filesystem.

Invariants:
- Fail closed: every outcome other than PASS / FRESH / NONE has `advance=False`.
- `evaluate` and the `check_*` functions never raise for filesystem or content
  problems; only `ValueError` for a bad floor.
- The pure core imports nothing from `os`, `pathlib` or `time`.
- No function reads the clock. The caller passes `phase_started`.

### Interfaces (stubs, no bodies)

```python
def parse_verdict(text: str | None) -> Verdict: ...
def classify_freshness(mtimes: Sequence[float | None], floor: int) -> Freshness: ...
def check_verdict(path: Path) -> Verdict: ...
def check_freshness(root: Path, pattern: str, floor: int) -> Freshness: ...
def check_increment(roots: Sequence[Path], floor: int) -> Freshness: ...
def evaluate(post: Postcondition, repo: Path, phase_started: int, verdict_path: Path) -> GateResult: ...
```

### Acceptance criteria

All run with `cd projects/nightloop && python3 -I -m unittest discover -v`, no
network, no `claude`, temp dirs only.

1. Every verdict case V1-V12 from `test_gates.sh` has a Python test with the
   same expected word (V13, the live repo file, is dropped: it reads outside
   the sandbox). Plus: `\r\n` line endings parse as PASS; `VERDICT: PASSED` is
   MALFORMED; `VERDICT: PASS - ship it` is PASS; a verdict line after a
   ` ` is MALFORMED (not seen as a line start); invalid UTF-8 bytes
   elsewhere in an otherwise valid PASS file still parse as PASS; a directory
   passed as the path is MISSING.
2. Every freshness case PC1-PC13 has a test with the same expected word,
   using real files with `os.utime` set to 2026-01-01 for "old" files.
   Plus: mtime exactly equal to the floor is FRESH; `floor - 0.5` is STALE (no
   int truncation); an unreadable-mtime entry (`None`) alone is STALE, not
   FRESH, and with an empty list is MISSING; a symlink loop does not hang.
3. `evaluate` returns `advance=True` for exactly PASS, FRESH and NONE, checked
   by a table over all `Verdict` and `Freshness` values.
4. `evaluate` with `REVIEW_VERDICT` ignores `phase_started`, and with
   `RESEARCH_NOTE_FRESH` ignores `verdict_path`.
5. A static test asserts that `gates/verdict.py` and `gates/freshness.py` (the
   pure modules) do not import `os`, `pathlib`, `time` or `subprocess`
   (parse `ast`).
6. The existing 47 milestone-1 tests still pass.

### Failure domain

Pure library; nothing runs it yet. A bug here would only matter once milestone
4 wires it in. When it is wired in, a false PASS ships an unreviewed PR, and a
false non-PASS wastes one night. Both directions are covered by the cases above.

## Open questions

- Bad floor: raise `ValueError` (proposed) or return MISSING as bash does
  (PC5/PC6)? The proposal prefers raising, since a bad floor is a caller bug
  and is a typed value in Python, but the builder must record the choice in the
  decisions log.
- Whether the research glob should require a date-prefixed filename. The bash
  gate does not; the proposal keeps parity.
- Whether `.pipeline/test_gates.sh` case ids should be kept in the Python test
  names for traceability. The proposal says yes (`test_V8_...`).
- Windows is untested; `os.walk` and `fnmatch` case rules differ there. Out of
  scope, since the tool targets macOS and Linux.
