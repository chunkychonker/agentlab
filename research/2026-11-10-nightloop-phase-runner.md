# nightloop milestone 3: phase runner

Mode: project:nightloop. Milestone 3 ("Phase runner"), the topmost not-started
one; milestones 1 and 2 are done (#78, #83). `logs/research-context.md` did not
exist this cycle, so open PRs and the `examples/`/`projects/` listing were not
checked from it; the work lives under `projects/nightloop/` and no `examples/`
directory is proposed, so there is no name to collide with.

## Question

What is the smallest runner that executes one phase as a headless `claude -p`
call behind an interface, records one metrics line, hands the gate its floor,
and can be tested with a fake that never touches a model?

## Findings

**The command line.** Sources: Claude Code headless docs
(<https://code.claude.com/docs/en/headless>, fetched 2026-11-10) and agentlab's
own working runner, `.pipeline/run.sh` `run_phase` (lines 485-574) and
`.pipeline/phase_tools.sh`.

- `claude -p <prompt> --model <m> --output-format json --permission-mode dontAsk --agent <name> --allowedTools <rule>...`.
  Order matters: `--allowedTools` and `--disallowedTools` swallow every
  following word up to the next flag, so the prompt must come first (the bash
  runner documents this at run.sh:492). In Python the argv is a list, so put
  the prompt first and pass each rule as its own element, or join rules with a
  comma into a single value; the first is what the live pipeline does and is
  proven. A unit test should pin "prompt is argv[2], before any variadic flag".
- `dontAsk` denies every call that would otherwise prompt. Calls covered by
  `--allowedTools` still run, and read-only actions (reads in the working
  directories, the read-only command set) need no approval and run. So an empty
  allowlist is not "no tools", which is why milestone 1 already rejects it.
- Exit code: 0 on success, non-zero when the run fails. SIGTERM gives 143 and
  records no result. Failures inside the run (missing auth) print the failure
  as the `result` on stdout, and the pipeline's own fixture
  (`.pipeline/test_metrics.sh:23`) shows such a run can carry
  `"is_error": true` with `"subtype": "success"`. Never infer success from
  `subtype`; use rc and `is_error`.
- `--output-format json` prints one object with `result` (string),
  `is_error`, `duration_ms`, `num_turns`, `session_id`, `total_cost_usd`,
  `usage` (`input_tokens`, `output_tokens`, `cache_read_input_tokens`,
  `cache_creation_input_tokens`) and `permission_denials` (array of
  `{tool_name, tool_input}`). Field list taken from the pipeline's recorded
  fixtures and `metrics.sh`; the docs page confirms `total_cost_usd`, session
  ID and `permission_denials` exist but does not publish a full schema. Treat
  every field except `result` as optional: absent means `None`, not 0.
- `total_cost_usd` is a client-side estimate; on a subscription it is notional.
  Label it that way in the record's docstring.
- A 10-minute idle ceiling on background work (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`,
  docs, 2026-11-10) is what killed agentlab's research phases in September.
  The runner should expose a per-phase wall-clock `timeout_s` (subprocess
  timeout) separate from it, and surface a timeout as its own outcome, not
  as a generic failure.
- `--bare` is documented as the recommended scripted mode, but it skips
  hooks from `--settings` and plugins loaded by flag, and uses `ANTHROPIC_API_KEY`
  only (no OAuth). nightloop's milestone 7 needs the publish-guard hook, so the
  runner must NOT pass `--bare` by default. Leave it out and say so.
- `--max-budget-usd` exists (headless docs: the run stops remaining background
  work when it hits it). Out of scope for m3; a later milestone can add a cap.

**What the gate needs from the runner** (PLAN current-state note, milestone 2):
take the floor as `int(time.time())` before launching, and consider a
one-second slack (`floor - 1`) because file mtimes can lag the clock by a few
ms. Recommendation: adopt the slack, but inside the runner, as one named
constant, and record it in the decisions log. Cost of slack: a file written
up to 1s before the phase started counts FRESH. That is a theoretical
false-pass window that a previous phase's artifact could only hit if the
phases run back to back in under a second, which a model call cannot.
(Judgement, not a verified fact.)

**Seam shape.** Per CLAUDE.md section 3 the core defines the interface and the
edge implements it. A `Runner` protocol, `run(request: PhaseRequest) ->
RawRun`, where `RawRun` is `(returncode, stdout, stderr, timed_out)`. The
subprocess implementation and the fake both satisfy it. Everything else
(argv building, JSON parsing, metrics record, gate call) is pure or takes the
runner and clock as parameters.

## Build proposal

Extends `projects/nightloop/`; contradicts no decisions-log entry (stdlib only,
no real model in tests, built under `projects/`).

### Intent

Run one configured phase through an injectable runner, then gate it and record
one metrics line. Out of scope: the cycle sequence and recovery branches (m4),
claims (m5), CLI (m6), schedulers, retry/backoff, budget caps, `--bare`,
usage-limit detection (note it as a follow-up, the bash runner has it).

### Files (new, under `projects/nightloop/nightloop/phase/`)

- `request.py`: `PhaseRequest` frozen dataclass (`name`, `phase: Phase`,
  `prompt`, `cwd: Path`, `timeout_s: int`); validates non-empty prompt,
  positive int timeout.
- `argv.py` (pure): `build_argv(req) -> tuple[str, ...]`.
- `result.py` (pure): `parse_cli_json(stdout) -> CliResult`; typed optional
  fields; raises `CliOutputError` on anything that is not exactly one JSON
  object with a string `result`.
- `metrics.py` (pure): `PhaseRecord` dataclass and `to_json_line(record) -> str`
  (compact, stable key order, newline-free).
- `runner.py`: `Runner` Protocol plus `RawRun`; `SubprocessRunner` (only file
  that imports `subprocess`) and `FakeRunner` (scripted `RawRun`s, records
  requests received).
- `execute.py`: `run_phase(req, runner, *, now, repo, verdict_path, metrics_sink) -> PhaseOutcome`
  with `now: Callable[[], float]` and `metrics_sink: Callable[[str], None]`
  injected, so the function itself does no I/O.
- Export `run_phase`, `PhaseRequest`, `PhaseOutcome`, `Runner`, `RawRun`,
  `FakeRunner`, `SubprocessRunner` from `nightloop/phase/__init__.py`.

### Interfaces (no bodies)

```python
class Outcome(enum.Enum):
    GATED_PASS      # rc 0, not is_error, gate.advance True
    GATE_FAILED     # rc 0, not is_error, gate.advance False
    CLI_ERROR       # rc != 0, or is_error true
    TIMED_OUT       # runner reported timed_out
    UNREADABLE      # rc 0 but stdout is not one JSON object

@dataclass(frozen=True)
class PhaseOutcome:
    outcome: Outcome
    gate: GateResult | None      # None unless the gate ran
    record: PhaseRecord

def build_argv(req: PhaseRequest) -> tuple[str, ...]: ...
def parse_cli_json(stdout: str) -> CliResult: ...
def run_phase(req, runner, *, now, repo, verdict_path, metrics_sink) -> PhaseOutcome: ...
```

### Behavioural spec

Inputs: a valid `PhaseRequest`, a `Runner`, a clock, a sink. Outputs: a
`PhaseOutcome`, and exactly one line passed to `metrics_sink`.

Invariants:
1. Exactly one metrics line per call, including TIMED_OUT, CLI_ERROR and
   UNREADABLE. (Metrics are a passenger: a sink that raises propagates; it
   does not change the outcome that was decided first.)
2. The floor is `int(now()) - FLOOR_SLACK_S`, read once, before `runner.run`.
3. The gate runs only when rc is 0, `is_error` is not true, and stdout parsed.
   A phase that exits non-zero never gets `GATE_FAILED`; it is `CLI_ERROR`.
4. `build_argv` always contains `--permission-mode dontAsk`, `--agent <agent>`,
   `--model <model>`, `--output-format json`, never `--bare`, never
   `--dangerously-skip-permissions`, and places the prompt before the first
   variadic flag.
5. A missing JSON field yields `None` in the record, never 0.

Failure modes: `CliOutputError` is caught inside `run_phase` and becomes
UNREADABLE (it is a recorded outcome, not swallowed: it appears in the record
as `parse_error: true`). A `ValueError` from the gate (bad floor) is a
programming error and propagates.

### Acceptance criteria (tests, no model, no network)

- A1 `build_argv` for a research phase equals an exact expected tuple; prompt
  index is 2 (after `claude`, `-p`).
- A2 argv never contains `--bare` or `--dangerously-skip-permissions` for any
  of the three phases of `examples/agentlab.nightloop.toml`.
- A3 `parse_cli_json` accepts the recorded fixture from
  `.pipeline/test_metrics.sh` line 20 and returns the cost, turns, tokens;
  rejects empty, two objects, a JSON array, and a missing `result`.
- A4 `is_error: true` with rc 0 gives `CLI_ERROR` and the gate is not called
  (assert via a gate spy that is a real function, not a mock of the SUT).
- A5 non-zero rc gives `CLI_ERROR`; `timed_out=True` gives `TIMED_OUT`; both
  still emit one metrics line.
- A6 a research phase whose fake runner writes a note to a tmp repo after the
  floor gives `GATED_PASS`; the same note with an mtime older than the floor
  gives `GATE_FAILED`. Use `os.utime` for the stale case.
- A7 slack: a file whose mtime is `floor_taken - 1` is FRESH, `floor_taken - 2`
  is STALE (pins the constant).
- A8 review phase with a verdict file `VERDICT: PASS` gives `GATED_PASS`;
  `VERDICT: FAIL` gives `GATE_FAILED`.
- A9 the metrics line parses as JSON, has the keys `phase, model, rc, outcome,
  duration_ms, num_turns, tokens, total_cost_usd, session_id, is_error,
  parse_error, denials`, and has `None` (JSON null) for a field the CLI omitted.
- A10 `permission_denials` of length 2 gives `denials: 2`.
- A11 purity: `execute.py`, `argv.py`, `result.py`, `metrics.py` import neither
  `subprocess` nor `os` (extend the style of `tests/test_gates_purity.py`).
- A12 `SubprocessRunner` is covered with a stand-in executable (a tiny Python
  script that prints a canned JSON object, and one that sleeps past the
  timeout), never `claude`.

Suite command: `cd projects/nightloop && python3 -I -m unittest discover -v`,
must stay green on 3.11 to 3.13.

Same-day scope check: seven small files plus tests; no new dependencies.

## Open questions

- Full `--output-format json` schema is not published on the headless page; the
  field list is from agentlab's recorded fixtures. A live smoke run (human step
  per the decisions log) should diff a real output against `CliResult`.
- Whether `claude -p` with `--agent` and `--permission-mode dontAsk` works the
  same on the user's installed version was not re-verified today; the bash
  pipeline uses exactly this combination nightly, so it is likely fine.
- Usage-limit detection (bash `usage_limit_reset`) is deliberately left out; it
  should be its own item, perhaps in m4 where it decides whether the night stops.
- `logs/research-context.md` was absent, so open PRs were not checked for an
  overlapping nightloop PR.
