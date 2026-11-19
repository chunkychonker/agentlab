# nightloop m7: the CLI (`run`, `run --dry-run`, `report` with per-gate counts)

Mode: project:nightloop, milestone 7. Date: 2026-11-19.

## Question

What is the smallest CLI that exposes `run_night` safely (window check, no
model call in `--dry-run`) and reports, per gate, how many nights it ran and how
many it blocked, from the metrics lines milestones 3-4 already write?

## Findings

**Headless flags are unchanged** (checked against the
[Claude Code CLI reference](https://code.claude.com/docs/en/cli-reference),
fetched 2026-11-19): `-p`, `--agent`, `--allowedTools`,
`--permission-mode` and `--dangerously-skip-permissions` still exist; `--bare`
is still a flag. The page lists no dry-run or plan-only flag for `-p`. So
`--dry-run` has to be nightloop's own: it must build the same `PhaseRequest`s
and argv that `run` would use, and never construct a runner. That keeps m3's
`build_argv` as the single source of truth for what a night would execute. One
side note from the same page: `--enable-auto-mode` was removed in v2.1.111;
nightloop does not use it.

**What is already in the repo (read 2026-11-19):**

- `run_night(cfg, *, run_id, repo, verdict_path, timeout_s, runner, gate,
  workspace, shipper, now, metrics_sink)` is complete. Its docstring says the
  run-window decision belongs to the CLI. `timeout_s` is required with no
  default (decision 2026-11-11: "left to the CLI").
- `Config.window` is `[start_hour, end_hour)`, end exclusive. Bash's
  `schedule_disposition` refuses a bad hour rather than defaulting to RUN, and
  `AGENTLAB_IGNORE_SCHEDULE` (any non-empty value) overrides it for a human at a
  terminal. Reasons are in `.pipeline/schedule.sh`: launchd runs missed jobs on
  wake, so the clock must be re-checked at start.
- Every metrics line has `run_ts`, `cycle`, `phase`, `outcome` (a
  `PhaseStatus`), and `postcondition_state`, which is set exactly when the gate
  ran (`PhaseRecord` enforces it). That is enough to derive the gate counts
  with no new write path: **a gate "ran" on a line whose `postcondition_state`
  is non-null; it "blocked" when `outcome == "GATE_FAILED"`.** A night is a
  distinct `run_ts`.
- `.pipeline/metrics.sh::metrics_summary` is the reference for the table: last
  N distinct `run_ts`, per-phase `n`/`ok`/medians, medians skip nulls, a torn
  or non-JSON line is skipped and never fatal. It needs `jq`; the Python port
  does not.

**Gaps the bash version does not have, which the Python one must decide:**

1. The gate name is the phase's *postcondition*, not the phase. A line only
   carries `phase` and `postcondition_state`, so the report keys gates by phase
   (`research`, `build`, `review`). That is unambiguous because
   `PHASE_POSTCONDITIONS` fixes one gate family per phase. The `NONE`
   postcondition (`postcondition_state == "NONE"`) can never block, so counting
   it as "ran" would flatter the gate; report it as `ungated` and leave it out
   of the ran/blocked columns.
2. "Blocked" must not be inflated by lines where the gate never ran (CLI
   error, timeout, unreadable stdout have `postcondition_state: null`). Those
   are phase failures, not gate decisions, and are counted separately
   (`not_gated`) so a reader can tell "gate never fired" from "model never got
   that far".
3. A skipped later phase (cycle ended at research) writes no line. Nights
   counted per gate is therefore the number of distinct `run_ts` with at least
   one gated line for that phase, not the total nights in the window.
4. The retire-a-gate signal (decision 2026-11-16) needs a window. Default
   7 nights as in `metrics_report.sh`, with `--nights N`. State in the output
   how many nights the window actually held, so "0 blocks in 2 nights" is not
   read as "never fires".

**Design constraints from the Protocol and the decisions log:**

- Standard library only (decision 2026-11-06): `argparse`, not click.
- The core never reads env/clock/files. So: a pure `plan_night` and
  `window_disposition(hour, window, override) -> Run | Refuse(reason)`; a pure
  `summarise(lines, nights) -> Report`; a thin `cli.py` shell that reads the
  clock, the env var, the file, and wires production objects.
- Fail closed on the window: an unreadable clock/hour is Refuse, as in bash.
- `nightloop run` with no `--window-override` and outside the window exits
  non-zero with one line naming the hours, and calls no model.

**Scope decision (needs a new log entry).** PLAN says the CLI "wires claims
into the night". Wiring `reconcile_stranded`/`reconcile_shipped` needs a
commit-and-push step on the claim file (m6 known limits: caller must be on a
clean main, fetch first, commit when `changed`). That is a second concern with
its own failure modes (network, push rejected). Doing it in the same increment
as the CLI shell would be two intents. This note splits it: m7 ships
`run`/`--dry-run`/`report`; claim wiring becomes a follow-up milestone 7b,
added in the PLAN decisions log by this cycle's researcher step is *not* done
(the builder or owner adds it); the builder should add the entry if they agree.

**HN / practitioner check:** skipped; no external library is involved and the
questions here are about this repo's own gates.

## Build proposal

### Layer 1: Intent

Give nightloop a command line: `nightloop run` runs one night if the clock is
inside the configured window, `nightloop run --dry-run` prints the night's plan
(every phase's argv, cwd, timeout, gate) without constructing a runner or
touching git, and `nightloop report` summarises `metrics.jsonl` including, per
gate, nights ran and nights blocked. Out of scope: claim wiring (see above),
schedulers (m9, m10), plugin (m8), changing any m1-m6 module's behaviour, a
config-file generator, colour/TTY niceties, a `--json` report.

### Layer 2: Behavioural spec

Inputs: `--config PATH` (default `nightloop.toml`), `--repo PATH` (default cwd),
`--metrics PATH` (default `<repo>/logs/metrics.jsonl`), `--timeout-s N`
(positive int, default 3600; this is the CLI's policy, per the 2026-11-11
decision), `--nights N` (report window, default 7), `--ignore-window` flag
(replaces bash's env var; see Open questions), `--hour H` hidden/test-only? No:
instead the shell takes `now` from `time.localtime`, tests call the pure
function.

Exit codes: 0 ok / night ran; 1 night ran but zero cycles shipped? **No** keep
it simple: 0 when `run_night` returned (shipped or not; reports are in the
metrics), 2 usage/config error (argparse and `ConfigError` messages naming the
field), 3 refused by window, 4 usage-limited night, 5 missing/empty metrics for
`report`.

Behaviours, each an acceptance criterion:

- **W1** `window_disposition(hour, window, ignore)` returns `Run` iff
  `start <= hour < end`, or `ignore` is true; hour 24, -1, a bool or a non-int
  is `Refuse` (even when `ignore` is true? No: `ignore` only overrides the
  window, a nonsense hour still refuses, since a bad clock should be loud).
  Boundary tests: hour == start runs, hour == end refuses, 08 and 09 irrelevant
  (ints, no octal trap) but include them.
- **W2** Outside the window, `run` exits 3, prints one line containing both
  hours, and the injected runner is never called (spy asserts zero calls).
- **D1** `--dry-run` prints, for each cycle 1..cycles and each phase in
  `PHASE_ORDER`: phase name, model, gate, timeout, cwd, and the argv from
  `build_argv`. It exits 0 inside or outside the window (a plan is not a run),
  and says which it would do. With a `RaiseOnCall` runner and workspace
  injected, nothing is called; no `.git` access (test runs in a temp dir that
  is not a repo).
- **D2** Dry-run argv equals the argv a fake runner sees from a real `run` of
  the same config (compare the two; guards drift).
- **R1** `summarise(lines, nights)` keeps the last `nights` distinct `run_ts`,
  skips non-JSON and non-object lines and counts them (`skipped`), never raises
  on them.
- **R2** Per phase: `n` records, `ok` (rc 0), and gate stats `ran` (lines with
  non-null `postcondition_state` other than `NONE`), `blocked` (`outcome ==
  "GATE_FAILED"`), `not_gated` (null state), `nights_ran` (distinct run_ts
  among `ran` lines), `nights_blocked`.
- **R3** A `NONE` state is reported as `ungated`, never in `ran`.
- **R4** Fixture: 5 nights, review blocks on 2, research never blocks => review
  row `nights_ran=5, nights_blocked=2`, research row `nights_blocked=0` and
  flagged `never blocked in 5 night(s)`. With fewer than `nights` nights in the
  file the header says `N night(s) of data (asked for M)`.
- **R5** Medians skip nulls and show `-` when nothing remains (matches
  `metrics_summary`); duration reported in minutes to one decimal.
- **R6** Missing or empty file: exit 5 with a message naming the path.
- **E1** A bad config prints the `ConfigError` field and exits 2, with no
  traceback and no runner constructed.
- **E2** `claude`/`git` not on PATH (`OSError`) is not caught into a
  default: it exits non-zero with the message (m3 decision: environment error
  stops the night loudly).

Invariants: `--dry-run` and `report` write nothing (checksum the temp dir
before/after). `run` appends metrics only through the existing sink; the sink
opens the file in append mode, one `write` per line, flushed, creating
`logs/` if missing. No test calls a real model, git remote, or `gh`.

Failure domain: the CLI process only. A crash mid-night leaves what m4's
recovery leaves; the CLI adds no new persistent state beyond the append-only
metrics file.

### Layer 3: Interfaces (no bodies)

```python
# nightloop/cli/window.py      (pure)
@dataclass(frozen=True)
class Run: ...
@dataclass(frozen=True)
class Refuse: reason: str
def window_disposition(hour: int, window: Window, *, ignore_window: bool) -> Run | Refuse: ...

# nightloop/cli/plan.py        (pure)
@dataclass(frozen=True)
class PlannedPhase: cycle: int; name: str; model: str; gate: str; timeout_s: int; cwd: Path; argv: tuple[str, ...]
def plan_night(cfg: Config, *, repo: Path, verdict_path: Path, timeout_s: int) -> tuple[PlannedPhase, ...]: ...
def render_plan(plan: tuple[PlannedPhase, ...], disposition: Run | Refuse) -> str: ...

# nightloop/cli/summary.py     (pure)
@dataclass(frozen=True)
class GateStats: ran: int; blocked: int; not_gated: int; ungated: int; nights_ran: int; nights_blocked: int
@dataclass(frozen=True)
class PhaseRow: phase: str; n: int; ok: int; gate: GateStats; median_min: float | None; median_turns: float | None; median_out_tokens: float | None; median_cost_usd: float | None
@dataclass(frozen=True)
class Report: nights_asked: int; nights_found: int; skipped_lines: int; rows: tuple[PhaseRow, ...]
def summarise(lines: Iterable[str], nights: int) -> Report: ...   # ValueError if nights < 1
def render_report(report: Report) -> str: ...

# nightloop/cli/metrics_file.py (edge)
def append_sink(path: Path) -> Callable[[str], None]: ...
def read_lines(path: Path) -> list[str]: ...        # raises MetricsFileError

# nightloop/cli/main.py         (edge; the only module that reads clock/env/argv)
def main(argv: Sequence[str] | None = None, *, clock=time.time, runner_factory=SubprocessRunner, ...) -> int: ...
# nightloop/__main__.py: raise SystemExit(main())
```

`pyproject.toml` gains `nightloop.cli` in `packages` and
`[project.scripts] nightloop = "nightloop.cli.main:main"`. `main` takes its
runner/workspace/shipper factories as keyword arguments so tests inject fakes;
production defaults are `SubprocessRunner`, `GitWorkspace`, `GhShipper`.

### Self-test ("it works")

`cd projects/nightloop && python3 -I -m unittest discover -v` passes, the 366
existing tests unchanged plus new ones for W1-W2, D1-D2, R1-R6, E1-E2,
including the purity test pattern (`tests/test_cli_purity.py`: `window`,
`plan`, `summary` import no I/O module). Mutation checks the builder should
run: `<=` instead of `<` on the window end; counting `NONE` as ran; counting
`null` state as blocked; not skipping torn lines; dry-run constructing the
runner. Each must fail a test.

## Open questions

- **Override mechanism:** bash uses an env var (any non-empty value). The core
  rule is no env reads inside logic; this note proposes a `--ignore-window`
  flag read at the edge instead. If the owner wants launchd-style parity with
  `AGENTLAB_IGNORE_SCHEDULE`, `main` can map an env var onto the same flag.
- **Default `--timeout-s`:** 3600 is a guess; bash has no phase timeout (the
  600s background-task ceiling is a separate matter, see
  `knowledge/` notes). The owner should confirm.
- Real `GhShipper` and `GitWorkspace` construction in `main` is covered only by
  construction tests; the live check from m5/m6 is still a human step.
- Whether claim wiring becomes m7b: a decisions-log entry is needed (not added
  by this cycle; the PLAN's decisions log is the owner's).
- I could not confirm a `claude -p` dry-run/plan flag beyond the reference
  page, so `--dry-run` is defined as nightloop-side only.
