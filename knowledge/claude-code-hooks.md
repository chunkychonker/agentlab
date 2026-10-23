# Claude Code hooks (PreToolUse)

Source: https://code.claude.com/docs/en/hooks (read 2026-10-23). See research/2026-10-23-pretooluse-bash-guard-hook.md.

- stdin: one JSON object with `tool_name`, `tool_input` (Bash: `command`, `description`, `timeout`, `run_in_background`), `tool_use_id`, `cwd`, `permission_mode`.
- Block with `exit 2` + stderr reason, OR `exit 0` + `{"hookSpecificOutput":{"hookEventName":"PreToolUse","permissionDecision":"deny","permissionDecisionReason":...}}`. Exit 2 overrides any JSON.
- Gotcha: `exit 1` and crashes/timeouts are non-blocking (fail open) unless `onFailure: "block"`. A policy hook must catch its own errors and deny.
- Silence (exit 0, no output) = no decision, not approval. Hook `allow` never beats deny/ask permission rules. Top-level `decision`/`reason` is deprecated for PreToolUse.
- `if` filter is best-effort; docs say use permission rules for hard enforcement.
- Unverified: deny under bypassPermissions.
Related: [[claude-code-mcp-connection]], [[agent-skills]].
