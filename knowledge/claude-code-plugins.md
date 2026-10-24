# Claude Code plugins (manifest contract)

Source: https://code.claude.com/docs/en/plugins-reference (read 2026-10-24, CLI 2.1.303). See research/2026-10-24-claude-code-plugin-bundle.md.

- `.claude-plugin/plugin.json` optional; only `name` required (kebab-case; `claude-*`/`anthropic-*` reserved -> error). Other components live at plugin root: `skills/<n>/SKILL.md`, `.mcp.json`, `hooks/hooks.json`.
- Manifest paths: must start `./`, stay inside root, must exist (`..` and missing are validate errors). `skills`/`mcpServers`/`hooks` add or merge with defaults; `commands`/`agents` replace.
- `${CLAUDE_PLUGIN_ROOT}` resolves in MCP stdio command/args/env, hooks, skill bodies; not in Bash-tool env. State goes in `${CLAUDE_PLUGIN_DATA}`.
- `claude plugin validate <path> [--strict] [--json]` is authoritative; `claude plugin details <name>` shows inventory and token cost.
- Plugins are copied to a cache on install: outside paths break; symlink handling reported buggy in community issues (unverified). Copy real files and test for drift.
Related: [[agent-skills]], [[claude-code-mcp-connection]], [[claude-code-hooks]].

## Docs vs reality (build, 2026-10-24, CLI 2.1.303)
See examples/plugin-agentlab-bundle/README.md "Live check".
- `claude --plugin-dir <dir>` works on a mid-repo dir with no marketplace; `claude --plugin-dir <dir> plugin details <name>` shows inventory (source `<name>@inline`).
- `claude plugin validate` does NOT check that `${CLAUDE_PLUGIN_ROOT}/x` targets in MCP `args` exist (passes); the miss shows up only at runtime as `Failed to connect — CONNECTION_CLOSED`.
- A missing Python dep (`mcp` not importable by `python3`) gives the same opaque `CONNECTION_CLOSED` in `claude mcp list`; no hint about the import.
- `type: "stdio"` in plugin `.mcp.json` is accepted. A dir with no `plugin.json` validates as "components only" and passes.
- `"../x"` draws two errors (missing `./` prefix and `..` traversal), reported independently.
