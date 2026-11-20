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
5. [done 93] Real shipper: a `Shipper` that pushes the cycle's branch,
   opens the PR with `gh pr create`, puts the review record in the PR body,
   and runs the mergeable check. A failed push, PR creation or check is
   `NotShipped(reason)`, never a crash or a silent success. Recovery also
   catches commits left on local `main` ahead of `origin/main`, not only
   uncommitted changes, so the shipper can never push a failed cycle's
   commits. Tests use a local bare `origin` and a fake `gh`; no test talks
   to GitHub.
6. [done 102] Claim tracking: the backlog claim lifecycle
   (`[ ]` → `[building]` → `[done #N]`), including stranded-claim recovery.
7. [done 103] CLI: `nightloop run`, `nightloop run --dry-run` (prints the
   night's plan without calling a model), and `nightloop report` (metrics
   summary). The report also prints, for each gate, how many nights it ran
   and how many it blocked, so a gate that never fires can be retired.
7b. [done 104] Claim wiring: the CLI's night runs `reconcile_stranded`
   before the cycles and `reconcile_shipped` for each shipped PR, and owns
   the step milestone 6 leaves to the caller (start on a clean main, fetch
   first, commit and push the backlog when `changed`). Split from 7 on
   2026-11-19; see the decisions log.
7c. [not-started] Pending-PR ledger: remember shipped PR numbers across
   nights and mark them done once merged. Proposed 2026-11-20 (see the
   decisions log): nightloop never merges, so 7b's same-night
   `reconcile_shipped` can only report "pending". Whether to build it is the
   owner's call.
8. [done 105] Plugin: phase agent templates plus the publish-guard
   PreToolUse hook, packaged as a Claude Code plugin. Includes a
   `run_silent` wrapper for the agents' test commands: one line on success,
   the full output on failure.
9. [done #106] Scheduler, macOS: generate and install a launchd job
   idempotently, honouring the run window.
10. [done 107] Scheduler, GitHub Actions: a workflow template that runs
    one night on a cron.
11. [not-started] Reviewer benchmark: seeded-bug fixtures and clean-diff
    fixtures, a runner, and a report that states the false-positive rate
    next to the catch rate. The runner is run whenever the reviewer
    template changes.
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
- 2026-11-15: The ship branch keeps the research note's shape,
  `cycle/<date>-<HHMMSS>-<cycle>-ship`, but both time parts come from the
  run id (new `RunId.hhmmss`), not from a clock. So `ship_branch(run_id,
  cycle)` takes no `hhmmss`, and `GhShipper` takes no `now`. With a clock,
  a retried `ship` gets a new branch, and so a second PR, unless the retry
  lands in the same second. Then the note's invariant "ship again reuses the
  open PR" would not actually hold. A run id plus a cycle is unique by
  construction.
- 2026-11-15: `GhShipper(repo, verdict_path, *, sleep, main, remote, git,
  gh, timeout_s)` takes program names and builds its own `GitRunner` and
  `Gh`, instead of the note's layer-3 `git: GitRunner, gh: Gh`. Taking the
  objects would mean giving the repo three times (shipper, runner, gh), and
  the three copies could disagree. This matches `GitWorkspace`'s
  `git: str`. `GitRunner` lives in `nightloop/git_runner.py` (shared, not
  in `cycle/` or `ship/`). `GitWorkspace` now runs git through it with the
  same behaviour, except that a non-positive `timeout_s` is now a
  `ValueError` at construction rather than a failure at the first git call.
- 2026-11-15: Recovery decisions the note left to the builder.
  (a) No `fetch` before the ahead count. `rev-list --count
  <remote>/<main>..<main>` reads the tracking ref the last pull left, so
  recovery stays offline-safe.
  (b) A failed push while main is ahead raises `RecoveryError` and leaves
  main where it is, instead of a quiet carry-over. This differs from the
  dirty-only case, where a failed push is still `Snapshotted(pushed=False)`
  as in m4.
  (c) A missing tracking ref raises (R4). That changes one m4 path: a clean
  tree whose `origin/main` ref was deleted used to be `Clean()`.
  (d) Not in the note: before any `git branch -f main`, both the shipper and
  recovery count `rev-list main ^HEAD ^<remote>/main`, the commits on main
  that neither the branch nor the remote holds. If that count is not zero,
  main is not moved (recovery raises; ship is `NotShipped`). This makes
  "never discards work" hold when a phase leaves HEAD on another branch.
  `merge-base --is-ancestor main HEAD` was rejected because it refuses
  wrongly once the remote's main has moved since the branch was made.
- 2026-11-15: Shipper choices where the note was silent or bash differs.
  A missing verdict file at ship time is `NotShipped` (bash: no section, no
  merge); any other read error is an `OSError` and propagates. The review
  record goes into the body when the PR is created and is only read back.
  There is no `gh pr edit`, so a reused PR whose body lacks the marker is
  `NotShipped`, not repaired. The mergeable poll sleeps only between reads,
  so five `UNKNOWN`s sleep 4 times (bash sleeps 5). The port takes `Ran:` and
  `Findings:` from the first such line. Bash's awk prints a repeated label
  line twice, and that quirk is not kept. `gh help environment` could not
  be checked: the publish guard blocks every `gh` call in the build phase.
  So `GH_PROMPT_DISABLED=1` is set only as a second line of defence. The
  first is passing `--title`, `--body-file -`, `--base` and `--head`
  explicitly.
- 2026-11-16 (owner): Milestones 7, 8 and 11 gain one requirement each,
  from a comparison with published self-driving agent pipelines. Milestone
  7's report counts, per gate, the nights it ran and the nights it blocked:
  a gate with no blocks over a long window is a candidate to remove.
  Milestone 8 ships a `run_silent` wrapper so a passing test run costs an
  agent one line of context. Milestone 11's fixtures include clean diffs,
  because a reviewer that fails everything has a perfect catch rate; the
  benchmark is re-run on every reviewer-template change. Sandboxing the
  phases is not added here: milestone 10's ephemeral runner gives it for
  the Actions path, and a local container would be a new milestone to
  propose before milestone 13.
- 2026-11-18: Milestone 6's interfaces differ from the research note's layer 3
  in these ways. Each change either makes a bad state unbuildable or keeps two
  copies of a value from drifting.
  (a) The branch a stranded marker names is a `StrandedBranch` value (non-empty,
  no whitespace, so `"] "` cannot occur in it), not a `str`. The PR number is
  `PrNumber` (int > 0; `PrNumber.parse` takes digits only with no leading zero).
  (b) The port's lookups return `Claim | NoClaim | Unreadable(reason)` instead of
  `Claim | None`, so "read it, nothing there" and "could not read it" stay
  distinct. If the branches cannot be listed at all, that raises
  `ClaimSourceError`, because without the list there is no item to report on.
  (c) The two reconcilers have separate status enums (`StrandedStatus`,
  `ShippedStatus`), so a stranded "item not found" or a shipped "filed" cannot
  be built.
  (d) `GitClaimSource(backlog: BacklogFile, *, main, remote, git, gh, timeout_s)`
  takes program names and the `BacklogFile`, not `git: GitRunner, gh: Gh,
  backlog_path`. This follows the 2026-11-15 `GhShipper` entry: the repo is
  given once, and the file that is edited and the path that is diffed are one
  value (`BacklogFile(repo, relative="BACKLOG.md")`). `Gh` gains one method,
  `merge_commit_of`.
  (e) Behaviour where bash differs. Rewrites keep every other byte, including
  CRLF and a missing final newline (bash rewrites every line with `\n`).
  `file_stranded` inserts after the last line of the stranded section, not at
  the end of the file (bash's append would land under a later section if one
  followed). A claim line whose text is blank is not a claim. When two lines
  carry the same item text and the target marker, both are rewritten, as in
  bash. `reconcile_stranded` reads the backlog once and writes it at most once
  per run.
- 2026-11-19: Milestone 7 ships `run`, `run --dry-run` and `report` but no claim
  wiring. That wiring is now milestone 7b. It needs a commit and push of the
  backlog on a clean, fetched main, with its own failure modes (network,
  rejected push). Building it in the same increment as the CLI would be two
  intents. Departures from the research note's layer 2-3, and choices where it
  was silent:
  (a) `run_night`'s request building moved, unchanged, into a new pure
  `phase_requests(cfg, *, repo, verdict_path, timeout_s)` in `cycle/night.py`.
  `run_night` and the dry-run's `plan_night` both call it, so D2 (plan argv ==
  run argv) holds by construction, and a test checks it as well. `run_night`'s
  behaviour and its tests are unchanged.
  (b) `Run` carries a `reason` like `Refuse`, so the dry-run can say why it
  would run.
  (c) Override: a `--ignore-window` flag only, no env var. A clock that cannot
  be read (a NaN or out-of-range time, an hour outside 0-23) refuses even with
  the flag, as the note's W1 says.
  (d) Exit codes are an `IntEnum`: 0 ok, 2 usage/config, 3 refused, 4
  usage-limited, 5 no metrics, plus 1 (new): `OSError` from the night
  (`claude`/`git`/`gh` cannot start) prints the error and exits 1 after that
  cycle's recovery. Any other exception (e.g. `RecoveryError`) propagates with
  its traceback. A `--repo` that `GitWorkspace`/`GhShipper` reject is exit 2,
  before any runner is built.
  (e) `--timeout-s` defaults to 3600, the note's figure. The owner should
  confirm it. `--config` defaults to `<repo>/nightloop.toml` and `--metrics`
  to `<repo>/logs/metrics.jsonl`. The verdict path is the named constant
  `logs/last-review.md` (GhShipper's documented wiring), with no flag.
  (f) Report: a line with no `outcome` field (bash's `metrics.sh` writes none)
  counts as blocked when its `postcondition_state` refuses to advance (`STALE`,
  `MISSING`, `FAIL`, `MALFORMED`; a test derives the set from
  `GateResult.advance`). With the note's rule alone, bash data could never
  show a block and every gate in it would read "never blocked". nightloop's
  own lines follow the note's `outcome == "GATE_FAILED"` rule exactly.
  A line is also skipped (and counted) when it is an object without a
  non-empty string `run_ts` or `phase`, or with a `postcondition_state` that is
  neither a string nor null. Whitespace-only lines are ignored, not counted. A
  file with no usable record exits 5, the same as an empty one. Rows come in
  `PHASE_ORDER`, then other phases sorted (bash sorts all of them).
  (g) Not in the note: `cli/night_text.py` prints one line per cycle after
  `run` (how it ended, what recovery did). The metrics file stays the record.
- 2026-11-20: Milestone 7b wires claims into `nightloop run`, per
  `research/2026-11-20-nightloop-claim-wiring.md`. Its four decisions, as built:
  (D1) A main that is not ready (detached HEAD, another branch, fetch failed,
  behind origin) makes the step `Skipped` and the night goes on, not an abort.
  It is also `Skipped` when main is *ahead* of origin/main (publishing would
  push unrelated local commits; recovery saves those to a branch instead) and
  when the backlog file has uncommitted or untracked changes (the commit would
  carry edits nightloop did not make). No git runs when the backlog file is
  missing.
  (D2) A `ClaimSourceError` before cycle 1 is `Skipped` (`pre_night_step`);
  after a cycle it propagates, like any other error of the environment. An
  `OSError` from any claim step is exit 1 (`ENVIRONMENT`), as in m7.
  (D3) One `run_night(..., between_cycles: Callable[[int], None])` hook,
  default a no-op (expand/contract: every existing caller is unchanged). It is
  called with 0 after the inputs are validated and before cycle 1, then with
  `n` after cycle `n`'s report, i.e. after its recovery, including a
  `UsageLimited` cycle, before the night stops. Not after a cycle that raised.
  (D4) nightloop never merges, so the post-night `shipped_step` on tonight's
  PRs reads `UNREADABLE` and prints "PR #N: pending, not marked (...)". That is
  reported, not an error and not a reason to commit. Milestone 7c (pending-PR
  ledger) is added as `not-started`; whether to build it is the owner's call.
  Departures from the note's layer 2-3, and choices where it was silent:
  (a) `claim_step` returns `(result, outcomes)`, the same shape as
  `shipped_step`, so the CLI can print per-branch lines.
  (b) The `BacklogPublisher` port is `ready() -> Skipped | None` and
  `publish(message) -> PublishFailed | None`, so the step's sum type
  (`Published | Nothing | Skipped | PublishFailed`) is built only in the pure
  `step.py`.
  (c) The three ports travel as a frozen `ClaimPorts` and `main` takes a
  `claims_factory(repo, backlog) -> ClaimPorts` keyword (default: the git
  wiring), like the other factories.
  (d) The backlog path is the existing `repo.backlog` config field, not a new
  constant.
  (e) Claim lines go to stderr as `nightloop: claims <label>: ...`, flushed as
  each step finishes. Stdout, and the m7 tests that pin it exactly, are
  unchanged. A skipped or failed claim step does not change the exit code: it
  is bookkeeping, and the next night's step retries it.
  (f) Every pathspec git call uses `--literal-pathspecs`, so a backlog path
  with glob characters names only itself.
  (g) Two existing tests change on purpose: the module lists in
  `test_claims_purity.py` and `test_cli_purity.py` gain the new modules (their
  completeness checks require it).

- 2026-11-21: The researcher takes milestone 8 while 7b is still
  `not-started` here, because 7b is already built and open as PR #104 (branch
  `cycle/2026-11-20-nightloop-claim-wiring`), awaiting merge. Researching it
  again would duplicate that PR. Milestone 8 does not depend on 7b's code. 7b
  is marked done only when #104 merges. The plugin's publish guard takes no
  phase (it blocks `git push` and non-read-only `gh` for every model phase),
  because nightloop's `Shipper` publishes outside any model phase and the
  runner passes no env; see `research/2026-11-21-nightloop-plugin.md`.
- 2026-11-21: Milestone 8 departs from the research note's layer 2-3 in these
  ways, and makes these choices where it was silent.
  (a) The templates' `model` and `tools` are checked for equality against a
  new `examples/plugin.nightloop.toml`, not against
  `examples/agentlab.nightloop.toml` as criterion 5 says. The agentlab config
  names agentlab's own agents (`agentlab-builder`, ...) and gives research two
  `mcp__hn-search__*` tools, which a generic plugin cannot assume exist. The
  new config is agentlab's values without those two tools. The drift guard
  still holds, now between the plugin and the config that uses it.
  (b) That config names the agents by their bare names (`builder`), not
  `nightloop:builder`. Research finding 4 said a config could name the scoped
  form with no code change, but m1's agent-name rule (`[a-z0-9][a-z0-9-]*`)
  rejects the colon. Widening that rule is a config change for its own
  milestone or an owner decision, not part of this one. A bare name resolves
  only when no other agent shares it.
  (c) The checker is a package, `nightloop/plugin_check/`, not one
  `plugin_check.py`. It splits into `tree.py` (the only reader),
  `snapshot.py`, `layout.py`, `frontmatter.py`, `problem.py`, `rules.py` (the
  pure decisions) and `check.py`. `from nightloop.plugin_check import
  check_plugin` works as the note wrote it. `Problem` is
  `(kind: ProblemKind, where, detail)`. Beyond the note's list it also
  reports a manifest path value of the wrong type, a hooks event that is not
  a list of groups, an agent without `name` or `description`, and an agent
  field that is neither supported nor ignored (e.g. a skill's
  `allowed-tools`). The manifest is required, which is stricter than Claude
  Code.
  (d) `run_silent`: death by signal S exits 128+S, as a shell would. The
  child's stdin is `/dev/null`, so it cannot hang waiting for input. An empty
  label or one with a line break is a usage error. Both edge scripts stay
  Python 3.9 compatible, because the hook runs under whatever `python3` is
  on PATH.
  (e) The sample verdict line is the reviewer's only. The researcher and
  builder write no verdict, so their test asserts they contain no column-0
  `VERDICT:` line.
  (f) Found while building: on CLI 2.1.329, `claude plugin validate --strict`
  passes a plugin with an ignored agent field, a missing hook script, or a
  non-executable `bin/` entry. `check_plugin` covers all three.
- 2026-11-22: Milestone 9 departs from the research note's layer 2-3 in these
  ways, and makes these choices where it was silent.
  (a) `Step` is a plain `Enum` with no data: the plist bytes and paths are
  given to `apply` once, so no step can carry a different copy. The uninstall
  table gains the two rows the note left out: plist present but not loaded
  gives `(REMOVE_PLIST,)`, and loaded with no plist gives `(BOOTOUT,)`.
  (b) The note's single edge module is split in two. `launchctl.py` is the
  only module that starts a process, and `apply.py` is the only one that
  writes files. The port gains `enable`, which runs before `bootstrap` as in
  `setup_runner.sh`, because `bootstrap` fails on a disabled job. `apply`
  takes `log_dirs` and creates them before `bootstrap`. Uninstall makes no
  backup, and it leaves any existing `.bak` file in place.
  (c) `LaunchdJob` also refuses an `argv[0]` that is not absolute, any
  control character (`plistlib` refuses them too), and `--ignore-window`
  anywhere in argv. The last makes I6 true by construction. Paths are
  `PurePosixPath`. `nightly_job` is the only builder. Logs go to
  `<repo>/logs/nightloop.launchd.{out,err}.log`. The default label is
  `dev.nightloop.<repo name>.<8 hex of sha256(repo path)>`, and `--label`
  overrides it. `--command PATH` overrides the default `sys.executable -m
  nightloop`. PATH is the installing shell's `PATH`, baked in at install.
  (d) `launchctl print`: exit 113 ("Could not find service") is
  `NOT_LOADED`. Any other non-zero exit raises; bash treats every non-zero
  exit as not loaded. Only the one-tab `state = ` line counts, because nested
  blocks carry their own `state = active`. `running` is `RUNNING`; `not
  running`, `waiting` and `spawn scheduled` are idle. Any other state raises
  rather than being guessed idle. The fixtures are real output from Darwin
  25.2.
  (e) `ExitCode` moved, unchanged, to `cli/exit_codes.py`, and `cli.main`
  re-exports it. New code: `JOB_RUNNING = 6`. A `ScheduleError` from launchd
  or a write exits 1 (`ENVIRONMENT`), as does a `status` that finds a broken
  job (slot outside the current window, a missing `argv[0]`, or a plist
  nightloop did not write). `--dry-run` reads the job's state with `launchctl
  print` and exits 0 even when the job is running.
  (f) Not in the note: `main` imports the scheduler only for `schedule`, and
  the argparse part lives in `cli/schedule_args.py`. The Homebrew Python
  3.12.14 on the build machine cannot `import plistlib` (its pyexpat needs a
  newer system libexpat). A module-level import would therefore have broken
  `nightloop run` on that interpreter too. A test checks that `run` loads
  neither `plistlib` nor `nightloop.schedule`.
  (g) `status` does not compare the installed plist with a freshly rendered
  one, because that would need the `--hour` used at install. "Already
  installed" compares the file only, not what launchd has loaded, as bash
  does.
- 2026-11-23: Milestone 10 departs from the research note's layer 2-3 in these
  ways, and makes these choices where it was silent.
  (a) `slot_for` and `FORBIDDEN_ARG` move to a new pure top-level module,
  `nightloop/schedule_slot.py`. The reason: importing
  `nightloop.schedule.job` runs that package's `__init__`, which loads
  `plistlib`, and the Homebrew 3.12 on the build machine cannot import it
  (2026-11-22 (f)). The shared `slot_for` raises a new
  `SlotError(ValueError)` with m9's exact messages. `schedule/job.py` keeps a
  `slot_for` that wraps it and re-raises `ScheduleError`, and re-exports
  `FORBIDDEN_ARG`, so m9's callers and tests are unchanged (expand only,
  nothing removed). `test_schedule_purity.py`'s parent-import allowlist
  gains `schedule_slot`.
  (b) `SpecError(field, reason)` lives in `schedule_gha/errors.py` (the note
  put it in `spec.py`), because `budget.py` raises it too and must not import
  `spec`. `check_budget` sits beside `worst_case_minutes` in `budget.py`.
  Each phase counts `timeout_s + PHASE_KILL_GRACE_S` (10, the runner's
  SIGTERM grace; a test keeps the two equal), not bare `timeout_s`.
  `JOB_SLACK_MINUTES = 30`. `MAX_JOB_MINUTES = 360`, confirmed on
  docs.github.com/en/actions/reference/limits on 2026-11-23 ("6 hours" for
  all GitHub-hosted runners).
  (c) `ValidSpec` is sealed: building it without `validate_spec` raises
  `TypeError`. `WorkflowSpec` also carries `config_path` (repo-relative,
  plain segments) and `workflow_stem` (`[a-z0-9][a-z0-9-]*`, used both as
  the file name and as the concurrency group suffix). A config outside
  `--repo` is exit 2, because the runner only sees the repo.
  (d) Not in the note: the target repo is checked out to `target/` and
  nightloop to the sibling `nightloop-src/`. Metrics go to the sibling
  `nightloop-metrics/` via `--metrics`. A nightloop checkout or metrics file
  inside the target would be untracked files in the repo, making every
  cycle's tree dirty for recovery. The credential and `GH_TOKEN` are set on
  the run step only, not the job, so the npm/pip install steps never see
  them.
  (e) The edge (`cli/gha_cmd.py`) also refuses a zone that `zoneinfo` does
  not know (exit 2). On the runner an unknown `TZ` reads as UTC with no
  error, so the window check would read the wrong hour. This machine's tz
  database could still differ from the runner's.
  (f) Pins: actions are pinned to full commit SHAs, resolved with
  `ls-remote` on 2026-11-23 (checkout v7.0.1, setup-python v7.1.0,
  setup-node v7.1.0, upload-artifact v7.0.2). The job uses Python 3.13 and
  Node 22 through the setup actions rather than relying on the image's
  versions.
  (g) A late scheduled run that `nightloop run` refuses exits 3, and the job
  shows as failed. The workflow does not turn exit 3 into success: a refusal
  stays visible, and the docs list it as expected.
  (h) `--minute` and the pins are required flags with no default.
  `--timeout-s` defaults to `main`'s `DEFAULT_TIMEOUT_S` and
  `--job-timeout-minutes` to `MAX_JOB_MINUTES`. An `OSError` from the write
  propagates, as the note says. (m9's `schedule` maps write failures to
  exit 1 instead; this generator has no launchd step to report alongside.)

## Current state

Milestone 10 (GitHub Actions scheduler) is done as PR #107 (open): built and reviewed 2026-11-23.
Research note: `research/2026-11-23-nightloop-actions-scheduler.md`. See the
2026-11-23 decisions entry for where the build departs from the note.
Milestone 7b is still not-started here, because it is open as PR #104. m9 is
merged (#106). Nothing in m10 touches claim code or the night path of `run`.

- Run it: `nightloop schedule-gha --repo <checkout> --nightloop-repo
  OWNER/NAME --nightloop-sha <40 hex> --claude-version X.Y.Z --credential
  {oauth-token,api-key} --timezone <IANA> --minute <1-59> [--hour H]
  [--config <toml in repo>] [--timeout-s N] [--job-timeout-minutes N]
  [--workflow-name STEM] [--dry-run]`. It writes
  `<repo>/.github/workflows/<stem>.yml` atomically, or says "nothing to do"
  if the file is already identical. `--dry-run` prints the YAML to stdout and
  the manual steps to stderr. Any bad input exits 2 with nothing written.
  Docs: `docs/github-actions.md` (the manual steps, the hazards, and a
  three-step human check).
- `nightloop/schedule_slot.py` (new, pure): `slot_for`, `SlotError` and
  `FORBIDDEN_ARG`, shared with launchd. `schedule/job.py` wraps `slot_for`
  and re-raises `ScheduleError`.
- `nightloop/schedule_gha/` (new; stdlib only; added to `pyproject.toml`).
  - Pure modules:
    - `errors.py`: `SpecError`.
    - `spec.py`: `Credential`, `WorkflowSpec` (every field checked against a
      strict pattern), the sealed `ValidSpec`, and `validate_spec`.
    - `budget.py`: `worst_case_minutes` and `check_budget`.
    - `render.py`: `render_workflow`, built from lines, with the action pins
      as constants.
  - Edge: `write.py`, whose `install_workflow` returns `Written |
    Unchanged`. It writes a temp file and then `os.replace`. It keeps the
    mode and leaves no temp file behind on failure.
- `nightloop/cli/` gains three modules: `gha_args.py` (argparse), `gha_cmd.py`
  (the edge, which `main` imports only for `schedule-gha`) and `gha_text.py`
  (the manual-steps text, pure). The generator imports neither `plistlib`
  nor `nightloop.schedule`.
- `examples/nightloop.workflow.yml` (new): the golden output for
  `examples/plugin.nightloop.toml` (cron `17 1 * * *` America/Chicago, 211 of
  360 minutes).
- Tests: `cd projects/nightloop && python3 -I -m unittest discover -v` runs
  636 tests: the 566 from before, all still passing, plus 70 new. 4 are
  skipped (the opt-in live tests). All pass on python3.11 and python3.13.
  On Homebrew 3.12.14, `python3.12 -I -m unittest discover -s tests -t . -p
  'test_gha_*.py'` passes all 70 new tests, and the generator's output there
  matches the golden file byte for byte, even though that interpreter cannot
  import `plistlib`. Plain `discover` from the project root still trips over
  m9's `nightloop.schedule` there, as before.
  - Criterion 1: `test_gha_render` checks that rendering twice gives the
    golden file both times. `test_gha_cli` checks that `main --dry-run`
    stdout is the golden file.
  - Criterion 2 (I1): every hour in the window is accepted and becomes the
    cron hour, and every hour outside it is refused (the end is exclusive).
    `TZ` equals `timezone:`. A subprocess test runs `cli.main._local_time`
    under `TZ` = UTC, Pacific/Honolulu and Asia/Tokyo and gets 0, 14 and 9.
  - Criterion 3 (I2, I3, I4, I6, I7), checked on indentation-aware blocks
    and steps:
    - exactly one credential, set on the run step only
    - exactly two permissions
    - `cancel-in-progress: false`
    - `fetch-depth: 0`, and a single `ref:` equal to the SHA
    - every `uses:` pinned to 40 hex
    - an exact CLI version, plus `DISABLE_AUTOUPDATER`
    - metrics outside `target/`
    - the upload is the last step, with `if: always()`
    - no `--ignore-window`
  - Criterion 4 (I5): a boundary table (exactly enough vs. one minute short,
    2 cycles at 3600 s, over 360), with messages naming both numbers.
  - Criterion 5 (I8): 19 hostile fragments, each in 3 positions, in each of
    the 6 string fields. Also a leading `-`, non-strings, and per-field shape
    cases.
  - Criterion 6 (I9, I10):
    - a second write leaves the bytes and mtime unchanged
    - a different file is replaced, keeping its mode
    - a failed `os.replace` leaves the original intact and no temp file
    - a dry run, and every refusal through `main`, leave the tree digest
      unchanged
  - Criterion 7, purity:
    - `test_gha_purity.py` keeps an allowlist: `dataclasses`, `enum`, `re`,
      `typing`, plus the parent modules `model` and `schedule_slot`. Only
      `write.py` may import `os`/`tempfile`.
    - `test_cli_purity.py` lists the three new CLI modules.
    - Subprocess probes show that `run` does not load the generator.
  - Criterion 8, mutation checks: each of these 18 breaks fails at least one
    test other than the byte-for-byte golden tests. They were run with `-B`
    and a cleared `__pycache__`.
    - The note's 7: `if: always()` dropped; `cancel-in-progress: true`;
      `--ignore-window` added; both credentials rendered; the cron hour taken
      from the window end; the budget check skipped; a floating `ref: main`.
    - 11 more: the secret moved to job level; a shallow checkout; nightloop
      checked out inside `target/`; `TZ` dropped; the write always
      rewriting; the temp file left behind; the zone check skipped; minute 0
      allowed; the kill grace ignored; the timezone pattern loosened; the
      hosted cap unchecked.
  - Criterion 9 (opt-in: `NIGHTLOOP_LIVE=1` with `actionlint` on PATH):
    passes with actionlint 1.7.12. actionlint 1.7.7 rejects the newer
    `timezone:` key.
  - Criterion 10: the 566 existing tests still pass.

Known limits (m10):
- The workflow has never run on GitHub. These are not verified:
  - that the runner's glibc honours `TZ`
  - that pushing a cycle branch works with the persisted checkout credential
    and these permissions
  - that `gh` picks up `GH_TOKEN` there
  - that the pip install works on the runner

  The three-step human check is in `docs/github-actions.md`.
- The phases' agents must resolve on the runner, for example committed under
  `.claude/agents/`. The workflow does not install the nightloop plugin, and
  the phase runner passes no `--plugin-dir` (m8's limit, still open). With
  the plugin example config as it is, the phases' agents would not resolve
  on a fresh runner.
- The 30-minute slack is a guess at setup plus shipping time. The shipper's
  git/gh calls are not bounded by the phase timeout.
- The zone check uses this machine's tz database, and the runner's could
  differ.
- The generated file bakes in the config's window and cycles, so editing
  either means regenerating. The file's header says so, but nothing
  enforces it.
- A refused late run shows as a failed job.
- A private nightloop source repo needs a token the workflow does not set up.

The m9 summary below is kept for reference.

Milestone 9 (launchd scheduler) was built 2026-11-22, reviewed (PASS), and merged as PR #106.
Research note: `research/2026-11-22-nightloop-launchd-scheduler.md`. See the
2026-11-22 decisions entry for where the build departs from the note.
Milestone 7b is still not-started here, because it is built and open as PR
#104; the researcher skipped it so as not to duplicate that PR. Nothing in m9
touches claim code or the night path of `run`.

- Run it (macOS): `nightloop schedule install [--dry-run] [--hour H]
  [--minute M] [--label L] [--command /abs/nightloop] --repo <checkout>
  [--config <toml>]`, plus `schedule uninstall [--dry-run]` and `schedule
  status`. `install` writes
  `~/Library/LaunchAgents/<label>.plist`, loads it, and prints a `sudo pmset
  repeat wakeorpoweron` line as advice; it never runs that line. A second
  `install` prints "nothing to do" and writes nothing.
- `nightloop/schedule/` (new; stdlib only, added to `pyproject.toml`).
  - Pure modules:
    - `job.py`: `LaunchdJob`, `slot_for`, `nightly_job`, `default_label`,
      `plist_path`, `domain_for`.
    - `plist.py`: `render_plist`, which uses `plistlib` with sorted keys and
      always writes both `Hour` and `Minute`, plus its inverse `parse_plist`.
    - `plan.py`: `JobState`, `Step`, `plan_install`, `plan_uninstall`,
      `mutates`.
    - `launchd_print.py`: `parse_print`.
    - `advice.py`: `wake_advice`, the slot minus 10 minutes, wrapping past
      midnight.
    - `ports.py`: the `Launchctl` protocol.
    - `errors.py`: `ScheduleError`.
  - Edges:
    - `launchctl.py`: `SubprocessLaunchctl` runs `/bin/launchctl` and
      `/usr/bin/plutil` by absolute path, with a 30s timeout per command.
    - `apply.py`: backs up the old plist once to `<label>.plist.bak`, then
      bootout, then writes a temp file, lints it and `os.replace`s it into
      place, then creates the log dirs, then enable and bootstrap. A failure
      after the bootout says UNLOADED and gives the restore command.
- `nightloop/cli/`: `exit_codes.py` (moved `ExitCode`, plus `JOB_RUNNING =
  6`), `schedule_args.py` (argparse), `schedule_cmd.py` (the edge; `Host`
  carries platform, home, uid, PATH and interpreter), and `schedule_text.py`
  (pure output text). `main` gains keyword arguments `host_factory=` and
  `launchctl_factory=`.
- Tests: `cd projects/nightloop && python3 -I -m unittest discover -v` runs
  566 tests: the 491 from before, all still passing, plus 75 new, with 3
  skipped (the opt-in live tests). They pass on 3.11 and 3.13 in about 40s.
  On this machine's Homebrew 3.12.14 every test outside the scheduler passes,
  but the 9 `test_schedule_*` modules fail to import, because that
  interpreter cannot `import plistlib` at all (a pyexpat/libexpat mismatch;
  `python3.12 -c "import plistlib"` fails on its own). That is an
  environment fault: the feature cannot work on that interpreter.
  - Criterion 1 (plan tables): `test_schedule_plan.py`. Criterion 2 (I1)
    passes twice: on `apply` with `FakeLaunchctl`, and through `main` with a
    temp home; the second install makes no mutating call and leaves the bytes,
    the mtime and a digest of the home tree unchanged. Criterion 3 (I2): all
    300 windows; an explicit hour outside the window raises, and the CLI
    exits 2 without building a `Launchctl`. Criterion 4 (I3, I4): round trip
    through `plistlib.loads` and `parse_plist`, with `& < > " '` and spaces
    in every path. Criterion 5 (I5, I6): RUNNING makes no mutating call and
    exits 6; `--ignore-window` is refused when the job is built. Criterion 6:
    a bootstrap that fails after bootout reports UNLOADED with the restore
    command and the backup path; a lint failure leaves the final path
    untouched and leaves no temp file. Criterion 7 (dry run): tree digests of
    the home and the repo are unchanged, and the only call is `state`.
    Criterion 8: `test_schedule_purity.py` is an allowlist (`plistlib`,
    `dataclasses`, `enum`, `hashlib`, `re`, `typing`, and `pathlib` for
    `PurePosixPath` only); it also checks that only `launchctl.py` imports
    `subprocess`. Criterion 9: on `linux`, all three actions exit 2 and build
    no `Launchctl`.
  - Criterion 10 (opt-in, `NIGHTLOOP_LIVE=1`): passed on Darwin 25.2. Real
    `plutil -lint` accepted a rendered job with awkward paths, and real
    `launchctl print` read a never-installed label as `NOT_LOADED` (exit
    113). Nothing was bootstrapped.
  - `SubprocessLaunchctl` is tested with stand-in executables: exact argv; a
    failure quotes the command and the last line; a timeout gives
    `ScheduleError`; a missing program gives `OSError`.
  - Criterion 11, mutation checks: each of these 12 breaks fails at least one
    test.
    - The note's five: `Minute` omitted; `Hour` taken from the window end;
      RUNNING ignored, both in the planner and in `apply`; the "differs"
      check skipped; `--ignore-window` added.
    - Seven more: nested `state` lines parsed; an unknown state guessed idle;
      lint after replace; a backup made every time; a dry run that applies;
      the platform check made after the factory is built.
  - A manual dry run against real launchd on this Mac printed the expected
    plan and plist and wrote nothing.

Known limits (m9):
- No real `install` has been run: the agentlab job was running during the
  build, and loading a second nightly job on the owner's Mac is the owner's
  call. To check it by hand: `install --hour <h>`, `launchctl print
  gui/$(id -u)/<label>`, `install` again (it should say "nothing to do"),
  then `uninstall`.
- The default command `sys.executable -m nightloop` works under launchd
  only when nightloop is installed for that interpreter (`pip install -e
  .`). The job runs in the target repo, not in nightloop's source tree.
  `install` does not check this. `status` flags an `argv[0]` that is
  missing, but not a module that cannot be imported. Moving or deleting the
  venv breaks the job; `status` reports that.
- PATH, the interpreter and the config path are baked in at install. Editing
  the config's window needs a re-install, and `status` warns when the slot
  falls outside the window.
- "Already installed" compares the file, not what launchd has loaded. An
  edit to the plist made by hand while the job is loaded is treated as
  current.
- launchd cannot wake the Mac, and with the lid closed a slot can be missed
  entirely (research finding 4). `nightloop run`'s own window check refuses
  a run that is late.
- The `launchctl print` format and exit code 113 are only known to hold on
  Darwin 25.2. Any other state or exit code raises; it is never guessed.

Below, the m8 summary is kept for reference. Milestone 8 (plugin) was built
2026-11-21 and merged as #105. See the second 2026-11-21 decisions entry for
where that build departs from its note.

- `plugin/` (new): a Claude Code plugin named `nightloop`, v0.1.0.
  - `.claude-plugin/plugin.json`: name, version, description, author. It
    sets no component paths, so the default `agents/`, `hooks/hooks.json`
    and `bin/` scans apply.
  - `agents/researcher.md`, `builder.md`, `reviewer.md`: frontmatter
    `name, description, model, tools` only. Each body states its gate's
    artifact. The builder and reviewer bodies wrap test runs in `run_silent`.
    The reviewer's fenced ```` ```verdict ```` sample is a valid verdict file.
  - `hooks/hooks.json`: `PreToolUse`, matcher `Bash`, exec form `python3`
    with args `${CLAUDE_PLUGIN_ROOT}/hooks/publish_guard.py`.
  - `hooks/publish_guard.py`: agentlab's guard ported without the phase.
    `decide(payload)` is pure; `main()` turns every exception into exit 2.
  - `bin/run_silent` (mode 755).
  - `README.md`: what loads, how to load it, the guard's limits and cost.
- `examples/plugin.nightloop.toml` (new): a config whose phases name the
  plugin's agents. It loads with `load_config`.
- `nightloop/plugin_check/` (new): `check_plugin(root) -> list[Problem]`.
  `tests/test_plugin_purity.py` checks that every module except `tree.py`
  imports only `json`, `re`, `pathlib` (`PurePosixPath`), `dataclasses`,
  `enum`, `typing` and `types`. Added to `pyproject.toml`'s packages.
- Tests: `cd projects/nightloop && python3 -I -m unittest discover -v` runs
  491 tests: the 424 from before, all still passing, plus 67 new, with 1
  skipped (the live validate test). Stdlib only, no network, no model. They
  pass on 3.11, 3.12 and 3.13 in about 40s.
  - Criterion 1: the real plugin gives `[]`, and each injected defect in a
    temp copy gives exactly its own `ProblemKind`.
  - Criterion 2: 40 recorded payloads (33 Bash commands, 7 malformed or
    non-Bash), each run through the script as a subprocess. The table is
    also run under `/usr/bin/python3` (3.9.6 here) when that is present.
  - Criterion 3: empty stdin, invalid JSON, non-UTF-8 stdin, and a `decide`
    that raises each give exit 2.
  - Criterion 4: `run_silent` run as a bare executable: one `ok:` line;
    `FAIL:` plus interleaved stdout and stderr in order; 5,000 lines kept;
    the child's exit code; 127 for a missing or non-executable command; 2 for
    each usage error; 137 for SIGKILL; no stdin passed on.
  - Criterion 5: each template's name, model and tools equal the plugin
    config's. The reviewer's sample parses as PASS with `parse_verdict` and
    gives a review record with `review_section`, and its inline FAIL form
    parses as FAIL. The guard allows each template's `run_silent` example
    command.
  - Criterion 6 (opt-in, `NIGHTLOOP_LIVE=1`): `claude plugin validate
    plugin/ --strict` exited 0 on Claude Code 2.1.329, listing the three
    agents, the hook and `bin: run_silent`.
  - Criterion 7: the purity test above.
  - Mutation checks: each of these 19 breaks fails at least one test.
    - guard: `rtk` dropped; `pr create` read-only; exit on an exception;
      nesting off by one; the quoted-heredoc exemption disabled
      (`_drop_data_heredocs` returns its input)
  - Fix round (review): the "quoted heredoc to a file" ALLOW row's body was
    `then run git push`, which the guard allows anyway because the first word
    is `then`, so the row passed even with the heredoc exemption disabled.
    The body is now exactly `git push`: the real guard allows it (exit 0), the
    mutant above blocks it (exit 2).
    - `run_silent`: output truncated; a fixed exit code; stdin passed on;
      127 changed
    - checker: ignored fields, bin mode, hook script, hooks wrapper or
      reserved prefix each unchecked; always `[]`
    - templates: a tool removed, the model changed, `PASSED`, no
      `Findings:`

Known limits (m8):
- The phase runner does not pass `--plugin-dir`, so `nightloop run` uses the
  plugin's agents only if the plugin is enabled for its `claude` process
  some other way (installed). There is no marketplace entry (m12's docs).
- Not verified live, a human step: that `-p --plugin-dir … --agent
  nightloop:builder --permission-mode dontAsk` resolves the agent and fires
  the plugin's hook. Check it with one billed run that tries `git push`.
- The guard matches text: a script file, `python3 -c` or an alias gets past
  it. While the plugin is enabled it also blocks pushes in interactive
  sessions.
- The config cannot name `nightloop:builder` (m1's agent-name rule). A bare
  name is ambiguous if another agent shares it.
- `frontmatter.py` reads top-level keys only; it is not a YAML parser.

Next: review this milestone. Then 7b merges (#104) and milestone 9 (launchd
scheduler).

The m7b summary below is kept for reference.

Milestone 7b (claim wiring) is built as of 2026-11-20, reviewed (PASS), and open as PR #104.
Research note: `research/2026-11-20-nightloop-claim-wiring.md`. See the
2026-11-20 decisions entry for D1-D4 as built and where the build departs from
the note. Milestones 1-7 are unchanged, except that `run_night` gains the
`between_cycles` keyword (default a no-op) and `main` gains `--no-claims` and
`claims_factory`.

- What a night does now (`nightloop run`, unless `--no-claims`): a claim step
  before cycle 1, one after each cycle's recovery (also after a usage-limited
  cycle), and a shipped step after the night for every `Shipped(pr)`. Each
  prints `nightloop: claims <label>: published N backlog edit(s) to main |
  nothing to change | skipped: <why> | publish FAILED: <why>` to stderr, with
  one indented line per outcome worth reading. `run --dry-run` lists the same
  labels under the cycle plan, or `claims: off (--no-claims)`, and builds no
  claim port.
- `nightloop/claims/` (additions):
  - `publish.py` (pure): `Published(count) | Nothing | Skipped(reason) |
    PublishFailed(reason)` and the `BacklogPublisher` port. Reasons are one
    non-blank line; `count` is an int ≥ 1, never a bool.
  - `step.py` (pure): `ClaimPorts`, `claim_step`, `pre_night_step` and
    `shipped_step`. The order is fixed here: `ready()`, then reconcile, then
    publish only if some outcome `changed` (shipped: only `MARKED_DONE`), all
    edits of a step in one commit.
  - `git_publisher.py` (edge): `GitBacklogPublisher`, which runs git only
    through `GitRunner`. `ready()`: the backlog file exists, HEAD is `main`,
    `fetch` works, main is neither ahead of nor behind `origin/main`, and the
    backlog has no uncommitted or untracked changes. `publish()`: `add -- <rel>`,
    `commit -m <msg> -- <rel>`, then push exactly
    `refs/heads/main:refs/heads/main`, never forced, never reset. A failed add
    or commit leaves the edit uncommitted; a failed push leaves the commit on
    local main, where m5's recovery saves it to a branch.
- `nightloop/cli/claim_text.py` (pure): step labels (`before cycle 1`,
  `after cycle N`, `after the night`), the dry-run's claim plan and each step's
  line. `main.py` wires `GitClaimSource`, `BacklogFile(repo, cfg.backlog)` and
  `GitBacklogPublisher` in `_git_claims`.
- Tests: `cd projects/nightloop && python3 -I -m unittest discover -v` runs 486
  tests: the 424 from before, all still passing, plus 62 new. Stdlib only. They
  pass on 3.11, 3.12 and 3.13 in about 43s.
  - Covered: the note's W1-W12. The publisher runs on a temp repo with a local
    bare `origin`, including an origin whose `pre-receive` hook rejects pushes,
    and a git wrapper that records argv. One end-to-end test drives `main` with
    the production claim wiring and checks that a stranded claim reaches
    `origin/main` before cycle 1 starts. The hook order is checked against a
    shared event log.
  - Mutation checks: each of these breaks fails at least one test.
    - `add -A` instead of the backlog pathspec
    - `commit -a` with no pathspec
    - skipping the fetch
    - a `reset --hard` after a failed push
    - a force push
    - ignoring main being ahead
    - ignoring a dirty backlog
    - a step only before cycle 1
    - no step after a usage-limited cycle
    - publishing a shipped step on any outcome, `UNREADABLE` included
    - reconciling before `ready()`
    - a pre-night source error propagating
    - the dry-run building the claim ports
    - `--no-claims` ignored
    - no post-night shipped step

Known limits (7b):
- No live run. Nothing has pushed to a real GitHub remote, and the live
  `gh pr view --json mergeCommit` (m6) is still unverified. Both are human
  steps, together with m5's and m6's live checks.
- Same-night PRs are never marked done (D4); the ledger is milestone 7c and
  does not exist yet. Until then a merged PR's claim stays `[building]` unless
  marked by hand or by bash.
- A failed pre-night push leaves the backlog commit on local main. If cycle 1
  ships, its ship branch is cut from that HEAD and carries the commit into the
  PR; if not, recovery saves it to an unshipped branch. Later claim steps are
  `Skipped` ("ahead") until main matches origin again.
- A failed add or commit leaves the backlog edit uncommitted; the next
  recovery snapshots it, and the next claim step is `Skipped` ("uncommitted
  changes") until then.
- Commits use the repo's git identity and run its hooks. Only `main` is ever
  published; the branch name and remote are constructor arguments with no flag.

Next: review this milestone, then the owner decides on 7c.

The m7 summary below is kept for reference. Its "no claims" limit is closed by
7b.

Milestone 7 (CLI) is built as of 2026-11-19, reviewed (PASS), and open as PR 103 (done 103).
Research note: `research/2026-11-19-nightloop-cli.md`. Claim wiring is split out
to milestone 7b. See the 2026-11-19 decisions entry for where the build departs
from the note. Milestones 1-6 are unchanged, except that `cycle/night.py`'s
request building is now the pure `phase_requests`, which `run_night` calls.

- Run it: `cd projects/nightloop && python3 -m nightloop run --dry-run --repo
  <checkout> --config examples/agentlab.nightloop.toml`, or `nightloop ...`
  after `pip install -e .` (`[project.scripts]`). `python3 -I` cannot import the
  package from the source tree; the tests can, because `discover` adds it.
- `nightloop/cli/` (new). Pure, and checked by `tests/test_cli_purity.py`:
  - `window.py`: `window_disposition(hour, window, *, ignore_window) -> Run |
    Refuse`, with `[start, end)` as in bash's `schedule_disposition`.
  - `plan.py`: `plan_night` (every phase of every cycle, from `phase_requests`
    and `build_argv`) and `render_plan` (argv `shlex.join`ed).
  - `summary.py`: `summarise(lines, nights) -> Report` and `render_report`. It
    ports `metrics_summary` (last N distinct `run_ts`; per phase `n`, `ok` and
    medians that skip nulls), plus a gate table with `ran`, `blocked`,
    `not_gated`, `ungated`, `nights_ran`, `nights_blocked` and a `never blocked
    in N night(s)` note.
  - `night_text.py`: one line per cycle for `run`'s output.

  Edges: `metrics_file.py` (`append_sink`, which creates `logs/` on the first
  line and appends one line per write; `read_lines`, where non-UTF-8 bytes
  become skipped lines) and `main.py`. `main.py` is the only module that reads
  argv and the clock and builds `SubprocessRunner`, `GitWorkspace` and
  `GhShipper`; all of them are keyword-injectable. `nightloop/__main__.py`
  calls `main`.
- On agentlab's own `logs/metrics.jsonl` (bash format, 7 nights), `report`'s
  phase table matches `metrics_summary`'s numbers exactly (row order differs).
  Its gate table says research and build never blocked in 6 and 4 nights, and
  review is `ungated`, because bash records review's verdict outside
  `postcondition_state`.
- Tests: `cd projects/nightloop && python3 -I -m unittest discover -v` runs 424
  tests: the 366 from before, all still passing, plus 58 new. Stdlib only. They
  pass on 3.11, 3.12 and 3.13 in about 35s.
  - Covered: W1-W2, D1-D2, R1-R6, E1-E2 and the exit codes. `main` is driven
    with a `FakeRunner`, a recording workspace, `FakeShipper` and the real gate
    in a temp dir that is not a repo. A tree digest shows that `--dry-run`,
    `report` and a refused `run` write nothing. Two tests run `python -m
    nightloop` as a subprocess.
  - Mutation checks: each of these breaks fails at least one test.
    - `<=` on the window end
    - `NONE` counted as ran
    - a null state counted as blocked
    - torn lines not skipped
    - dry-run building the runner
    - plan argv drifting from run argv
    - a bool rc counted as ok
    - the flag overriding a bad hour
    - bash lines never blocking
    - keeping the first N nights instead of the last

Known limits (m7):
- No claims (milestone 7b). There is no log file beyond metrics and stdout,
  and no `--json` report.
- No live run. The production factories are exercised only to the point of
  `GitWorkspace` rejecting a non-repo. A real `nightloop run` against a
  checkout with `claude` and `gh` is a human step, together with m5's and m6's
  live checks.
- `--timeout-s` 3600 is unconfirmed policy. A phase that exceeds it is
  `TIMED_OUT`, so it is not a silent failure.
- The run id and the window hour use the machine's local time, as bash does.
  Across a DST change the hour is whatever `time.localtime` says.
- `report` treats a line with no `outcome` as bash's format. A hand-written
  nightloop-shaped line without `outcome` would be read the same way.

Next: review this milestone, then milestone 7b (claim wiring).

The m6 summary below is kept for reference. Its "no CLI" limit is closed by m7,
and its claim wiring is now 7b.

Milestone 6 (claim tracking) is built and reviewed (PASS), PR #102 open, as of 2026-11-18.
Research note: `research/2026-11-18-nightloop-claim-tracking.md`. Milestones 1-5
are done and unchanged, except that `ship/gh.py` gains `Gh.merge_commit_of` and
the test fake `gh` answers `pr view --json mergeCommit`. See the 2026-11-18
decisions entry for where the build departs from the note.

- `nightloop/claims/` (new). Its pure modules port `.pipeline/backlog.sh`'s claim
  decisions; `tests/test_claims_purity.py` checks that they import no I/O module.
  - `markers.py`: the marker strings, the stranded section, `split_item_line`.
  - `values.py`: `Claim(key, item)`, `PrNumber`, `StrandedBranch`.
  - `edit.py`: `Rewritten(text) | AlreadyHandled() | ItemNotFound()`.
  - `backlog_text.py`: `count_unclaimed`, `apply_stranded`, `file_stranded`,
    `mark_done`. Items are matched with `==` on the parsed line text, never by
    a pattern or prefix.
  - `claim_diff.py`: `claim_from_diff`. The first added `[researching]` or
    `[building]` line is the claim, plus the indented added lines right after it.
  - `ports.py`: `ClaimSource`, `BacklogStore`, `UnshippedBranch`, `NoClaim`,
    `Unreadable`, `ClaimSourceError`.
  - `outcome.py`: `StrandedOutcome`/`StrandedStatus` and
    `ShippedOutcome`/`ShippedStatus`. Each outcome has a `changed` property.
  - `reconcile.py`: `reconcile_stranded(source, backlog)` returns one outcome per
    branch. If an item is not on main, it is filed instead.
    `reconcile_shipped(source, backlog, pr)` returns one outcome. Neither one
    commits, pushes, opens a PR or deletes a branch.

  The two edges are:
  - `backlog_file.py`: `BacklogFile(repo, relative)`. It reads and writes UTF-8
    with no newline translation. A write goes to a temp file in the same
    directory, keeps the file's mode, then `os.replace`. The temp file is
    removed on every failure.
  - `git_claim_source.py`: `GitClaimSource`. It runs `for-each-ref` over
    `refs/heads/cycle/*-unshipped-*` and `refs/remotes/<remote>/cycle/*-unshipped-*`,
    then for each ref `merge-base <main> <ref>` and a backlog diff. For a PR it
    runs `gh pr view N --json mergeCommit -q .mergeCommit.oid`, then
    `cat-file -e`, then `diff <mc>^1 <mc>`. A bad ref, a missing merge commit or
    a gh failure is `Unreadable`. A failed listing raises `ClaimSourceError`, and
    git or gh that cannot start raises `OSError`.
- Tests: `cd projects/nightloop && python3 -I -m unittest discover -v` runs 366
  tests: the 291 from before, all still passing, plus 75 new. Stdlib only. They
  pass on 3.11, 3.12 and 3.13 in about 35s.
  - The research note's C-count, C-literal, C-stranded, C-file, C-done and C-diff
    checks are covered, using the bash fixtures' real item text (backticks,
    brackets, `[building]` inside the text).
  - Also covered: the missing final newline, CRLF, I1 idempotence over 36
    backlogs × 3 edits, I3, and I4.
  - Edge tests run on a temp repo with a bare `origin`. They cover: a pushed
    branch (marked once; its `origin/` copy is already handled; a second run
    leaves bytes and mtime unchanged); a local-only branch never on main
    (filed with its continuation); no claim; an orphan branch (no merge base);
    a ship branch (ignored); a `--no-ff` merge and a squash merge (marked done);
    and an unmerged PR, an unknown oid and a gh error (all unreadable).
  - Mutation checks: each of these breaks fails at least one test.
    - `startswith` in place of equality
    - a merge-base diff in place of the first-parent diff for merged PRs
    - no dedupe in `file_stranded`
    - continuation lines dropped, in the diff reader or in the filer
    - the section heading added every time
    - a write when nothing changed

Known limits (m6):
- Not wired into `run_night` and no CLI; that is milestone 7. The caller must be
  on a clean main and must fetch before `reconcile_shipped`, because the source
  never fetches. It must also commit when `changed` is true.
- No test has run the live `gh pr view --json mergeCommit`. The fake encodes the
  shape `run.sh` uses (`-q .mergeCommit.oid`). Both `""` and `null` for an
  unmerged PR are treated as unreadable. This needs a human live check, together
  with milestone 5's.
- A remote's copy of a branch that is also local is reported as already handled,
  not merged into one outcome. Lines that the filer adds always end in `\n`,
  even in a CRLF file.
- Not ported: the replenishment and health-finding half of `backlog.sh`, and a
  periodic sweep of merged PRs for a mark-done that was missed.

Next: milestone 7 (CLI), which wires claims into the
night.

The milestone 5 summary below is kept for reference.

Milestone 5 (real shipper) is done as of 2026-11-15 (PR #93, review PASS).
The human live check below against real GitHub is still outstanding. Research note:
`research/2026-11-15-nightloop-real-shipper.md`. Milestones 1 (#78),
2 (#83), 3 (#88), 4 (#89) and 5 (#93) are done. `phase/` and `gates/` are unchanged.

- `nightloop/ship/` (new). `GhShipper` (`gh_shipper.py`) implements the
  `Shipper` port and runs these steps in order:
  1. Read the verdict and build the body. No `Findings:` line, a credential
     hit or no room means `NotShipped`, before any git or gh call.
  2. Switch to `cycle/<date>-<HHMMSS>-<cycle>-ship`. It is made from HEAD,
     or reused on a retry. Commit any uncommitted work there. If the branch
     holds nothing beyond `<remote>/main`, that is `NotShipped("no changes to
     ship")`. If main has unpushed commits the branch lacks, that is
     `NotShipped` too.
  3. Push exactly `refs/heads/<b>:refs/heads/<b>`, never main and never
     forced.
  4. `git branch -f main <remote>/main`.
  5. Reuse the branch's open PR (`gh pr list --head`), or else create one
     with `--title --body-file - --base --head` and take the number from
     the printed URL.
  6. Read the body back and check it for the marker.
  7. Poll `mergeable` up to 5 times, 2s apart while `UNKNOWN`. Only
     `MERGEABLE` gives `Shipped(pr)`.

  Any step failing, exiting non-zero or timing out gives `NotShipped`. The
  reason is one line and quotes only the last output line. `git`/`gh` that
  cannot start raises `OSError`.

  The pure modules are:
  - `review_section.py`: a port of `review_record.sh`'s section, secret scan
    and compose. It uses the same `<!-- agentlab-review -->` marker and
    65536 limit, and cuts only the section, never mid-character.
  - `mergeable.py`: `classify` and `poll`. Unknown values fail closed.
  - `pr_url.py`: `pr_number`.
  - `pr_list.py`: `open_pr`. More than one open PR is an error.
  - `naming.py`: `ship_branch`.
  - `pr_text.py`: the generic title, body intro and commit message.

  `gh.py` is the only module that runs `gh`. It passes every flag
  explicitly, sets `GH_PROMPT_DISABLED=1`, applies a per-call timeout, and
  sends the body on stdin.
- `nightloop/git_runner.py` (new): `GitRunner`, shared by the shipper and
  `GitWorkspace`. Every git call has a timeout and runs with
  `GIT_TERMINAL_PROMPT=0`. `nightloop/process_tail.py` (new, pure) holds
  `last_line`.
- `GitWorkspace.recover` now also counts `<remote>/main..main`, with no
  fetch. Dirty files and commits ahead go to one
  `cycle/<date>-unshipped-<HHMMSS>-<cycle>` branch, which is pushed. Main is
  then moved back with `branch -f`, and only after the push succeeds and no
  commit would be orphaned. A failed push while main is ahead, a missing
  tracking ref, or orphaned commits raise `RecoveryError` and leave main
  untouched. The dirty-only path is unchanged from m4.
- Tests: `cd projects/nightloop && python3 -I -m unittest discover -v`.
  291 tests: the 229 from before, all still passing, plus 62 new. Stdlib
  only. They pass on 3.11, 3.12 and 3.13 in about 30s; the fake `gh` and git
  wrapper are Python processes, and one test waits out a 2s gh timeout.
  - S1-S9 and R1-R6 are covered on a temp repo with a local bare `origin`.
  - The fake `gh` (`tests/ship_support.py`) is a scripted executable over a
    JSON state file. It records argv and stdin.
  - S9 asserts on the argv of a git wrapper that records each call and then
    runs the real git.
  - One night test runs `run_night` with the real `GhShipper` end to end.
  - Mutation checks: each of these breaks fails at least one test.
    - dropping the marker check
    - pushing main too
    - skipping the secret scan
    - ignoring commits ahead in recovery
    - removing either orphaned-commit guard
    - accepting any `mergeable`
    - creating a PR without listing first

Known limits (m5):
- Nothing has talked to real GitHub. The live check is a human step: run
  `GhShipper(repo, Path("logs/last-review.md"), sleep=time.sleep).ship(RunId(...), 1)`
  once against a throwaway GitHub repo, then diff real PR-create and
  `gh pr view --json mergeable` output against the fake's.
- Still unconfirmed: the `mergeable` value set, what PR creation does for a
  branch that already has a PR (the shipper lists first instead), and
  whether `GH_PROMPT_DISABLED` covers every prompt.
- The PR title is generic. It never merges, and there is no claim marking
  (m6) and no CLI (m7).
- The ship commit and recovery commits use the repo's git identity and run
  its hooks.
- A remote ship branch that diverged (pushed from elsewhere) is
  `NotShipped` on a non-fast-forward push, never forced.
- A phase that leaves HEAD on its own branch with commits that main does
  not have, on a clean tree, is still `Clean()`. Those commits stay on that
  local branch, unpushed. This gap predates m5, and m5 does not widen it.
- Earlier limits that remain: a `RecoveryError` propagates without the
  earlier cycles' `CycleReport`s; there is no log beyond metrics; handoff
  files must be git-ignored; untracked-stray handling and `scrub_artifacts`
  are not ported. The rc-0 rate-limit behaviour and the real limit text are
  unconfirmed.
Next: review this milestone and do the live check, then milestone 6 (claim
tracking).

Milestone 4 as built (kept for reference; recovery's ahead check above
supersedes its "uncommitted changes only" behaviour):

- `nightloop/cycle/`. `run_night(cfg, *, run_id, repo, verdict_path,
  timeout_s, runner, gate, workspace, shipper, now, metrics_sink) ->
  NightResult` (`night.py`) builds the three `PhaseRequest`s once, before
  any cycle, so bad inputs fail before anything runs. It runs up to
  `cfg.cycles` cycles and stops after a `UsageLimited` one. `run_cycle`
  (`cycle.py`, internal) does `clear_handoff`, then research, build and
  review through m3's `run_phase`, where the first phase that is not
  `GATED_PASS` ends the cycle. Then `ship` only on a review `GATED_PASS`,
  then `recover` exactly once, also when an exception is propagating (a
  failed recovery is raised `from` that exception). Modules: `result.py`
  (the `CycleResult` sum type), `report.py`, `ports.py`, `usage_limit.py`,
  `recording.py`, `metrics_tag.py`, `naming.py` (recovery branch name),
  `prompts.py`, `run_id.py`, `fake_shipper.py` and `git_workspace.py` (the
  only cycle module that runs git; the constructor checks `repo` is a
  checkout's top level).

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
