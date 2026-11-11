# Cycle orchestration: what a night loop must guarantee

Source: agentlab `.pipeline/run.sh` (`run_cycle`, `snapshot_dirty_main`, night
loop) and `.pipeline/usage_limit.sh`, read 2026-11-11. Used by the nightloop
cycle orchestrator (`research/2026-11-11-nightloop-cycle-orchestrator.md`).

- Clear the handoff files (review verdict, PR number) before each cycle, or a
  stale PASS authorises the wrong cycle's work. A missing verdict then reads as
  MISSING. See [[gates-port-python]].
- Recover after every cycle, pass or fail: a dirty tree goes to one uniquely
  named branch (add a sequence number; two fast failures share a second), then
  back to a clean main. Never discard work. If the snapshot fails, stop the
  night rather than let the dirt leak into the next cycle.
- A usage limit is "come back later", not a defect: stop the night after
  recovery. Match on text (`hit your (session|usage|weekly) limit|usage limit
  reached`), not on exit code. A reported bug has `claude -p` return rc 0 with
  `is_error: true` and the error only in `result`, so scan every `CLI_ERROR`,
  never a passing result. Unconfirmed live. See [[claude-p-phase-contract]].
- Model the cycle outcome as a sum type, not a bool plus a reason.
