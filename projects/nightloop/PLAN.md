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

1. [not-started] Config: `nightloop.toml` schema and loader. Covers repo,
   run window, cycles, and per phase: agent, model, postcondition, and tool
   allowlist. Invalid config fails at load with a message naming the field.
   Pure; no I/O beyond reading the file it is handed.
2. [not-started] Gates library: the fail-closed review-verdict parse
   (PASS / FAIL / MISSING / MALFORMED) and per-phase artifact freshness
   checks, with the edge cases from agentlab's `test_gates.sh` ported as tests.
3. [not-started] Phase runner: runs one phase as
   `claude -p --agent … --permission-mode dontAsk --allowedTools … --output-format json`
   behind an interface, records one metrics JSON line per phase, and ships a
   fake runner for tests. No test calls a real model.
4. [not-started] Cycle orchestrator: runs the sequence with its gates; a failed
   cycle is snapshotted to a recovery branch and never leaks into the next.
   End-to-end tests drive it with the fake runner.
5. [not-started] Claim tracking: the backlog claim lifecycle
   (`[ ]` → `[building]` → `[done #N]`), including stranded-claim recovery.
6. [not-started] CLI: `nightloop run`, `nightloop run --dry-run` (prints the
   night's plan without calling a model), and `nightloop report` (metrics
   summary).
7. [not-started] Plugin: phase agent templates plus the publish-guard
   PreToolUse hook, packaged as a Claude Code plugin.
8. [not-started] Scheduler, macOS: generate and install a launchd job
   idempotently, honouring the run window.
9. [not-started] Scheduler, GitHub Actions: a workflow template that runs
   one night on a cron.
10. [not-started] Reviewer benchmark: seeded-bug fixtures, a runner, and a
    catch-rate plus false-positive report.
11. [not-started] Docs: README, quickstart, and an architecture page that
    explains each gate and the incident that motivated it.
12. [blocked: needs a human to choose and authorise the repo] Adopt on a
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

## Current state

Nothing exists yet. Next: milestone 1 (config schema and loader).
