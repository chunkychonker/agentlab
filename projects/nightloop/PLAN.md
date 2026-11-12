# nightloop (working name)

## Goal

A small, installable tool that gives any git repo a gated overnight loop:
research → build → review → PR, one increment per night, using headless Claude
Code. It packages what agentlab's own pipeline learned the hard way:
mechanical gates around model judgment (a fail-closed verdict parse, per-phase
artifact checks, a clean-merge check), least-privilege phases, per-phase cost
metrics, and recovery of failed cycles onto branches. For: developers who want
an unattended agent working a backlog in their repo without trusting it blindly.

## Scope

In scope: a Python CLI and library (config, gates, phase runner, cycle
orchestrator, claim tracking), a Claude Code plugin with the phase agents and
publish-guard hook, schedulers for launchd and GitHub Actions, a reviewer
benchmark with a published catch rate, docs, and adoption on one repo other
than agentlab.

Out of scope: replacing agentlab's live `.pipeline/` (it keeps running on bash
until nightloop has proved itself, and that switch is a separate human
decision); automatic merge-conflict resolution; running cycles in parallel;
non-Claude models; a hosted service.

## Milestones

Ordered; the researcher picks the topmost `not-started` item each cycle (or
resumes the topmost `in-progress`/`blocked-but-now-unblocked` one — never
skip ahead of an unfinished predecessor without saying why in the decisions
log). Status values: `not-started` / `in-progress` / `blocked: <reason>` /
`done <PR#>`.

1. [done 78] Config: `nightloop.toml` schema and loader. Covers repo,
   run window, cycles, and per phase: agent, model, postcondition, and tool
   allowlist. Invalid config fails at load with a message naming the field.
   Pure; no I/O beyond reading the file it is handed.
2. [done 83] Gates library: the fail-closed review-verdict parse
   (PASS / FAIL / MISSING / MALFORMED) and per-phase artifact freshness
   checks, with the edge cases from agentlab's `test_gates.sh` ported as tests.
3. [done 88] Phase runner: runs one phase as
   `claude -p --agent … --permission-mode dontAsk --allowedTools … --output-format json`
   behind an interface, records one metrics JSON line per phase, and ships a
   fake runner for tests. No test calls a real model.
4. [done 89] Cycle orchestrator: runs the sequence with its gates; a failed
   cycle is snapshotted to a recovery branch and never leaks into the next.
   End-to-end tests drive it with the fake runner.
5. [not-started] Real shipper: a `Shipper` that pushes the cycle's branch,
   opens the PR with `gh pr create`, puts the review record in the PR body,
   and runs the mergeable check. A failed push, PR creation or check is
   `NotShipped(reason)`, never a crash or a silent success. Recovery also
   catches commits left on local `main` ahead of `origin/main`, not only
   uncommitted changes, so the shipper can never push a failed cycle's
   commits. Tests use a local bare `origin` and a fake `gh`; no test talks
   to GitHub.
6. [not-started] Claim tracking: the backlog claim lifecycle
   (`[ ]` → `[building]` → `[done #N]`), including stranded-claim recovery.
7. [not-started] CLI: `nightloop run`, `nightloop run --dry-run` (prints the
   night's plan without calling a model), and `nightloop report` (metrics
   summary).
8. [not-started] Plugin: phase agent templates plus the publish-guard
   PreToolUse hook, packaged as a Claude Code plugin.
9. [not-started] Scheduler, macOS: generate and install a launchd job
   idempotently, honouring the run window.
10. [not-started] Scheduler, GitHub Actions: a workflow template that runs
    one night on a cron.
11. [not-started] Reviewer benchmark: seeded-bug fixtures, a runner, and a
    catch-rate plus false-positive report.
12. [not-started] Docs: README, quickstart, and an architecture page that
    explains each gate and the incident that motivated it.
13. [blocked: needs a human to choose and authorise the repo] Adopt on a
    second repo and record the first week's metrics.

## Decisions log

Append-only — never edit or delete an entry. To change a prior decision, add
a new entry that supersedes it and says why. A cycle's work must not
contradict an entry here without adding one.

- 2026-11-06: Python 3.11+, standard library only for the core (`tomllib`,
  `subprocess`, `json`) — a reusable tool needs typed config and a portable
  test suite, and stdlib-only keeps installation to one step. agentlab's own
  pipeline stays bash; it is the reference, not a dependency.
- 2026-11-06: Built under `projects/nightloop/`, never by editing
  `.pipeline/` — the live pipeline must not rewrite its own gates.
- 2026-11-06: No test calls a real model; every model call sits behind the
  milestone-3 runner interface. A live smoke run is a documented human step.
- 2026-11-09: A bad freshness floor (not an `int`, a `bool`, or negative)
  raises `ValueError` from `classify_freshness`, the `check_*` functions and
  `evaluate`, instead of returning MISSING as `postcondition.sh` does
  (PC5/PC6). In bash the floor arrives as an untyped string; in Python it is a
  typed value the runner computes, so a bad one is a programming error, and
  raising stops the night (still fail-closed) and names the bug instead of
  logging it as a missing artifact. `evaluate` validates it for every
  postcondition, including `NONE` and `REVIEW_VERDICT`, which otherwise ignore
  it, so the bug shows up on the first call.
- 2026-11-09: `GateResult.outcome` is a typed enum (`Verdict`, `Freshness`, or
  `Ungated.NONE`) checked against the postcondition, and `advance` is a
  property computed from it, not a stored field. This replaces the research
  note's `outcome: str` plus `advance: bool` so that an inconsistent result
  cannot be built. The orchestrator still branches only on `advance`.
- 2026-11-10: The freshness floor is `int(now()) - FLOOR_SLACK_S` with
  `FLOOR_SLACK_S = 1` (one named constant in `nightloop/phase/execute.py`),
  read once before the phase starts. Coarse filesystem mtime clocks can lag
  `time.time()`, and without the slack a file written in the phase's first
  instant could read STALE. Cost: an artifact written up to 1s before the
  phase started counts as FRESH. Back-to-back phases cannot hit that window,
  because a model call takes longer than a second.
- 2026-11-10: `run_phase` takes the gate as a required `gate=` parameter
  (production: `nightloop.gates.evaluate`) instead of importing it, so the
  phase core imports no I/O module, and A4's spy is the real gate wrapped
  rather than a mock. The status enum is `PhaseStatus`, not the research
  note's `Outcome`, so it cannot be confused with `nightloop.gates.Outcome`.
  The runner file is split into `runner.py` (the protocol),
  `subprocess_runner.py` and `fake_runner.py`.
- 2026-11-10: In the CLI's JSON, `result` and `is_error` decide the status,
  so a missing or non-string `result` or a non-bool `is_error` makes the
  output UNREADABLE. Every other field only feeds metrics: when it is absent
  or the wrong type it is recorded as null and never fails the phase, the
  same as `metrics.sh`.
- 2026-11-10: If the runner cannot start the phase at all (`OSError`, e.g.
  `claude` is not on PATH), `run_phase` raises and writes no metrics line.
  In bash a missing CLI shows up as rc 127 and gets recorded as a failed
  phase. Here it is an environment error, and it stops the night loudly
  instead of looking like a model failure.
- 2026-11-11: The ship step is a `Shipper` port (`ship(run_id, cycle) ->
  Shipped(pr) | NotShipped(reason)`), not a model phase, because the config
  has no `maintain` phase. Milestone 4 ships only `FakeShipper`. The real
  shipper (push, `gh pr create`, the review record, the mergeable check) is
  left for a later decision about which milestone owns it. `NotShipped` is
  itself a `CycleResult` case. The research note's separate `ShipRefused`
  type was dropped because it would only have been a copy of it.
- 2026-11-11: The usage-limit text is checked on every `CLI_ERROR`, including
  rc 0 with `is_error: true`, not only on a non-zero exit as in bash. A
  reported (unconfirmed) CLI bug returns a rate-limit error with exit 0. The
  text checked is the CLI's `result` when stdout parsed as JSON, otherwise
  the raw stdout, then stderr. No other status is checked: `GATED_PASS`,
  `GATE_FAILED`, `TIMED_OUT` and `UNREADABLE` (rc 0 with non-JSON stdout)
  never stop the night, even when they quote the message. The JSON `error`
  field is not used.
- 2026-11-11: Interface changes from the research note's layer 3, each one
  so that a bad state cannot be represented or two things cannot drift:
  `Recovery` is the sum type `Clean() | Snapshotted(branch, pushed)` rather
  than `branch: str | None` plus `pushed`. The recovery branch's sequence
  number is the cycle number, unique within a night by construction, rather
  than a mutable counter like bash's `SNAP_SEQ`.
  `Workspace.clear_handoff(verdict_path)` takes the same path the review
  gate reads, so the file that gets deleted and the file that gets checked
  cannot differ. The run id is a `RunId` value type in bash's `TS` format
  (`YYYY-MM-DD_HHMMSS`), because it becomes both the metrics `run_ts` and a
  git ref component. `run_night` takes `timeout_s` as a required argument
  with no default: bash has no phase timeout, so any default would be a new
  policy. That choice is left to milestone 6's CLI.
- 2026-11-12 (owner): Milestone 4 is done as #89. The real shipper, left
  unowned by the 2026-11-11 Shipper entry, is now milestone 5, ahead of claim
  tracking and the CLI, because both need a PR to exist. Milestone 5 also
  closes the gap #89's review found: recovery treats only uncommitted
  changes as a leak, so commits left on local `main` would carry into the
  next cycle (bash's `reset_to_clean_main` has the same gap). The old
  milestones 5-12 are now 6-13. Milestone numbers in decisions-log entries
  written before this date use the old numbering: for example, "milestone
  6's CLI" in the 2026-11-11 interface entry is now milestone 7.

## Current state

Milestone 4 (cycle orchestrator) is done as #89, merged 2026-11-11.
Research note: `research/2026-11-11-nightloop-cycle-orchestrator.md`.
Milestones 1 (#78), 2 (#83) and 3 (#88) are done. `phase/` and `gates/` are
unchanged.

- `nightloop/cycle/` (new). `run_night(cfg, *, run_id, repo, verdict_path,
  timeout_s, runner, gate, workspace, shipper, now, metrics_sink) ->
  NightResult` (`night.py`) builds the three `PhaseRequest`s once, before
  any cycle, so bad inputs fail before anything runs. It runs up to
  `cfg.cycles` cycles and stops after a `UsageLimited` one. `run_cycle`
  (`cycle.py`, internal) does `clear_handoff`, then research, build and
  review through m3's `run_phase`, where the first phase that is not
  `GATED_PASS` ends the cycle. Then `ship` only on a review `GATED_PASS`,
  then `recover` exactly once, also when an exception is propagating (a
  failed recovery is raised `from` that exception).
  - `result.py`: the `CycleResult` sum type, `Shipped(pr) | NotShipped(reason)
    | PhaseFailed(phase, status) | ReviewRefused(verdict) |
    UsageLimited(phase, reset)`. Each case rejects its own illegal values.
  - `report.py`: `CycleReport` and `NightResult`, where only the last report
    may be `UsageLimited`.
  - `ports.py`: the `Workspace`/`Shipper` protocols, `Clean | Snapshotted`
    and `RecoveryError`.
  - `usage_limit.py`: the pure `usage_limit_reset`, a port of bash's.
  - `recording.py`: a `Runner` decorator that keeps the last `RawRun` for
    the limit check.
  - `metrics_tag.py`: puts `run_ts` and `cycle` first on each m3 line.
  - `naming.py`: names the recovery branch
    `cycle/<date>-unshipped-<HHMMSS>-<cycle>`.
  - `prompts.py`: one template per phase.
  - `run_id.py`: `RunId`.
  - `fake_shipper.py`: `FakeShipper`.
  - `git_workspace.py`: `GitWorkspace`, the only module that runs git.
    Recovery runs `status`, `switch -c`, `add -A`, `commit` and `push -u`;
    a failed push is `pushed=False`, not an error. It then runs `switch
    main` and `pull --ff-only`, and checks the tree is clean. A failed
    commit carries the work back to main, deletes only the empty branch it
    created, and raises. Every git call has a timeout and runs with
    `GIT_TERMINAL_PROMPT=0`. The constructor checks that `repo` is a
    checkout's top level.
- Tests: `cd projects/nightloop && python3 -I -m unittest discover -v`.
  229 tests (164 before plus 65 new), stdlib only. They pass on 3.11, 3.12
  and 3.13 in about 15s. B1-B12 are covered. The orchestrator tests run the
  real gate and the real `GitWorkspace` on a temp repo with a local bare
  `origin` (`tests/git_support.py`, which isolates the global git config).
  Failures come from real `pre-commit`/`pre-push` hooks and real branch-name
  collisions, not mocks. The B1 shipper is a stand-in that really commits,
  pushes and switches back. Deliberately breaking `cycle.py` (dropping
  `clear_handoff`, recovery on a crash, the CLI_ERROR-only scan or the
  `result`-text scan) and removing the usage-limit `break` in `night.py`
  each fail at least one test.

Known limits (m4). There is no real shipper, so nothing opens a PR yet
(milestone 5). Recovery checks only for uncommitted changes, so commits left
on local `main` ahead of `origin/main` carry into the next cycle; #89's
reviewer reproduced this, and bash has the same gap (milestone 5). A
`RecoveryError` propagates without the earlier cycles' `CycleReport`s; the
metrics lines already emitted are the only record. There is no log beyond
metrics, and `RecoveryError` messages carry git's last stderr line. A
handoff file tracked by git would dirty main every cycle, so it must be
git-ignored (agentlab's `logs/` is). Recovery commits run the repo's
hooks, as bash's do. Not ported: bash's untracked-stray handling and
`scrub_artifacts`, claim marking (m6), replenish, health, observer and
auto-merge. The rc-0 rate-limit behaviour and the real limit text under
`--output-format json` are unconfirmed. A human smoke run should check
one real limit hit against `usage_limit_reset`.
Next: milestone 5 (real shipper), then milestone 6 (claim tracking).

Milestone 3 as built (kept for reference):

- `nightloop/phase/` (new). `request.py`: `PhaseRequest` (phase name and
  `Phase`, a prompt that is non-empty, does not start with `-` and has no
  NUL, an absolute `cwd`, an int `timeout_s > 0`). `argv.py`: `build_argv`
  builds `claude -p <prompt> --model --output-format json --permission-mode
  dontAsk --agent --allowedTools <rule>...`, with the prompt always at
  argv[2] and `--allowedTools` last. It never emits `--bare` (that would skip
  m8's hook) or `--dangerously-skip-permissions`. `result.py`:
  `parse_cli_json` returns a `CliResult` or raises `CliOutputError` (rejects
  NaN, Infinity and over-long ints). `metrics.py`: `PhaseRecord` and
  `to_json_line`, using the bash field names plus `outcome`. `runner.py`: the
  `Runner` protocol and `RawRun`. `subprocess_runner.py`: the only module
  that spawns processes. Each phase runs in its own session, and on timeout
  the whole group gets SIGTERM, then SIGKILL after `TERM_GRACE_S` (10s), so a
  background child holding stdout cannot hang the runner. `fake_runner.py`:
  `FakeRunner` (scripted `RawRun`s or step functions, records the requests)
  plus `cli_stdout` / `cli_success`. `execute.py`: `run_phase(req, runner, *,
  now, gate, repo, verdict_path, metrics_sink) -> PhaseOutcome`. Status
  precedence is TIMED_OUT, then CLI_ERROR (rc != 0), then UNREADABLE, then
  CLI_ERROR (`is_error`), then GATED_PASS or GATE_FAILED. Every completed run
  emits exactly one metrics line.
- Gates (m2) unchanged: `parse_verdict`, `classify_freshness`, `check_*` in
  `gates/fs.py`, and `evaluate` in `gates/dispatch.py`. See the 2026-11-09
  decisions.
- Tests: `cd projects/nightloop && python3 -I -m unittest discover -v`. That
  is 164 tests (122 from m1 and m2, plus 42 new); stdlib only; they pass on
  3.11, 3.12 and 3.13 and take about 5s, mostly the subprocess timeouts.
  A1-A12 from the research note are covered. A12 uses Python stand-in
  executables: echo argv/cwd, exit 3, sleep past the timeout, ignore SIGTERM,
  and leave a background child holding stdout. No test runs `claude`.

Known limits. A descendant that starts its own session escapes the group
kill and can hold stdout open. The runner passes no `--settings`, no env
(such as bash's `AGENTLAB_PHASE`) and no `--disallowedTools`, so m8 must add
the hook wiring. Usage-limit detection (bash `usage_limit_reset`) is not
ported; it belongs with m4's stop-the-night logic. Records carry no `run_ts`
or `cycle`, which m4 adds. There is no file sink yet: `metrics_sink` is any
`Callable[[str], None]`. POSIX only. The CLI's JSON schema is unpublished,
so a live smoke run (a human step) should diff one real output against
`CliResult`, e.g. `run_phase(req, SubprocessRunner(), now=time.time,
gate=evaluate, repo=..., verdict_path=Path("logs/last-review.md"),
metrics_sink=print)`.
Next: milestone 4 (cycle orchestrator).
