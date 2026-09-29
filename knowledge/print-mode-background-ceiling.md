# `claude -p` background-task ceiling (600s) and keeping the override effective

- `CLAUDE_CODE_PRINT_BG_WAIT_CEILING_MS`: cap on *idle* wait for background subagents/workflows after the final turn in `-p` mode. Default 600000; resets whenever Claude takes a turn handling a background result; `0` = wait forever; needs v2.1.182+. ([docs](https://code.claude.com/docs/en/env-vars), checked 2026-09-29)
- On hit it prints `Background tasks still running after 600s; terminating.` and **exits 0**, so retry logic keyed on exit status never fires. Judge phases by artifact (see [[pipeline-run-log-shapes]], `.pipeline/postcondition.sh`).
- Gotcha: a grep for the `export` line is not proof. Launchers using `env -i` with an allowlist drop the variable (veriloom/cli #72, 2026-09-11); subshell-only exports don't propagate. Test by running the launcher against a stub `claude` that dumps its env.
- In this repo the fix is `run.sh` line ~58 (2026-09-20); no kill message in logs since. Related: [[pipeline-claim-lifecycle]].
