# Claude Agent SDK (Python)

Package `claude-agent-sdk` (0.2.168 on 2026-10-15; pre-1.0, ships almost daily - pin exactly). Python >=3.10. Bundles the Claude Code CLI; `query()` drives that subprocess, so the agent loop is Claude Code's, not yours. Docs live at code.claude.com/docs/en/agent-sdk/python (old docs.claude.com URLs redirect twice).

- Custom tools: `@tool(name, desc, schema)` async fn -> `create_sdk_mcp_server` -> `ClaudeAgentOptions(mcp_servers={"calc": srv}, allowed_tools=["mcp__calc__<tool>"])`. Name is `mcp__<server>__<tool>`.
- `allowed_tools` auto-approves, it does not restrict; restrict built-ins via `tools`. `setting_sources=[]` for hermetic runs.
- Ends with `ResultMessage` (`subtype`, `is_error`, `num_turns`, `total_cost_usd`, `result`). `error_max_turns` is a subtype, not an exception.
- Contrast with the raw loop: [[tool-use-loop]]; tool decorators in the Messages SDK: [[typed-tool-registry]]; in-process MCP vs stdio servers: [[mcp-python-sdk]].
Source note: research/2026-10-17-agent-sdk-minimal-loop.md (unverified items listed there).

## Permissions and hooks (2026-11-03)
- Evaluation order: hooks, deny rules, ask rules, permission mode, allow rules, then `can_use_tool` last. `can_use_tool` never sees calls auto-approved earlier (bare `allowed_tools`, `acceptEdits`, `bypassPermissions`); enforce must-run rules in a `PreToolUse` hook (deny beats allow, holds under bypass). `allowed_tools` does not constrain `bypassPermissions`; use `disallowed_tools`.
- Python hook deny: `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": ...}}`; callback `can_use_tool` returns `PermissionResultAllow(updated_input=...)`/`PermissionResultDeny(message=...)`. Docs claim string prompts need a streaming generator + dummy hook for `can_use_tool` (unverified). Plan: [[claude-code-hooks]] for the shell form. Source: research/2026-11-03-agent-sdk-permission-gate.md.
