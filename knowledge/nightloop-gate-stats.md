# Counting what a gate did, from the metrics lines (nightloop m7)

- A phase's metrics line carries `postcondition_state` exactly when the gate
  ran, so "gate ran" = non-null state, "gate blocked" = `outcome ==
  "GATE_FAILED"`. No new write path is needed ([[claude-p-phase-contract]],
  [[cycle-orchestration-gates]]).
- Do not count a null state as a block: CLI error, timeout and unreadable
  stdout are phase failures, not gate decisions. Report them as `not_gated`.
- `Postcondition.NONE` yields state `NONE` and can never block; report it as
  ungated so it does not pad the "ran" column.
- A skipped later phase writes no line, so nights-per-gate is distinct
  `run_ts` among that gate's lines. Always print how many nights the window
  held: "0 blocks in 2 nights" is not "never fires".
- `claude -p` has no dry-run flag (CLI reference, 2026-11-19); a dry run must
  build the same argv through the same function and never construct a runner.
- The run-window check belongs at the CLI edge and fails closed on a bad hour,
  as `.pipeline/schedule.sh` does.
