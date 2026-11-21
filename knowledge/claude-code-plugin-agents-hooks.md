# Plugin agents and hooks: what a plugin cannot set (nightloop m8)

Source: code.claude.com plugins-reference, plugins/components, sub-agents, hooks (read 2026-11-21). See research/2026-11-21-nightloop-plugin.md. Extends [[claude-code-plugins]] and [[claude-code-hooks]].

- Plugin agents ignore `permissionMode`, `hooks`, `mcpServers`, `initialPrompt` (security). A guard hook must live in `hooks/hooks.json`; permission mode must come from the CLI flag (`--permission-mode dontAsk`, see [[claude-p-phase-contract]]).
- Supported plugin-agent fields: `name, description, model, effort, maxTurns, autoCompactWindow, tools, disallowedTools, skills, memory, background, omitClaudeMd, isolation, color`.
- Names are `<plugin>:<agent>`; `claude --agent` takes the bare name if unique. Manifest `agents` key replaces the `agents/` scan, so leave it unset.
- `bin/` files are on the Bash tool's PATH (after the user's entries); `CLAUDE_PLUGIN_ROOT` is exported to hook processes but not to the Bash tool, so an agent body uses the `${CLAUDE_PLUGIN_ROOT}` text substitution instead.
- A hooks file needs the top-level `"hooks"` wrapper; the inline manifest form does not. Exec form (`command` + `args`) skips the shell and its quoting.
- Hook payload has `agent_type` when `--agent` is used (subagent type wins). A guard that needs no phase can ignore it.
- `claude plugin validate` does not check that a hook script exists; test that path yourself.
- Unverified: plugin hooks firing under `-p` + `dontAsk`; `-p --agent <plugin>:<agent>` resolution. Needs a live run.
