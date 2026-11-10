# `claude -p` as a phase: what a runner may rely on

Source: headless docs (https://code.claude.com/docs/en/headless, read 2026-11-10)
plus agentlab's `.pipeline/run.sh` `run_phase` and `.pipeline/test_metrics.sh`
fixtures. Used by the nightloop phase runner (`research/2026-11-10-nightloop-phase-runner.md`).

- Put the prompt before `--allowedTools` / `--disallowedTools`; they consume every
  following word up to the next flag. See [[claude-code-allowed-tools-rules]].
- `dontAsk` denies anything that would prompt; allowlisted and read-only actions
  still run. Success is rc 0 AND `is_error` not true: a failed-auth run can have
  `subtype: "success"` and `is_error: true`. SIGTERM gives rc 143 and no result.
- JSON output: only `result` is safe to require; treat every other field
  (`total_cost_usd`, `usage`, `permission_denials`, ...) as optional and record
  absence as null, not 0. `total_cost_usd` is a client-side estimate.
- `--bare` skips hooks passed via `--settings` and flag-loaded plugins, and ignores
  OAuth, so a phase relying on a PreToolUse guard must not use it.
- Background work can hold a run open; the 10-minute idle ceiling
  (`CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`) drops the partial result. Give the
  runner its own wall-clock timeout and report it as a distinct outcome.
- Freshness floor: take it before launching; a 1s slack covers coarse mtime
  clocks. See [[gates-port-python]].
