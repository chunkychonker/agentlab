# Connecting an MCP server to the real Claude Code host

Everything in [[mcp-python-sdk]] tests a server object against the SDK's
own in-memory `Client` — it proves the server registers tools and responds
correctly, but never proves a real Claude Code session can discover,
connect to, and actually invoke it. This note is about that second, real
connection: how Claude Code (the CLI/host, not the SDK) finds a server,
approves it, and how to script a real end-to-end check of the whole path.

Verified against the official docs on 2026-08-08:
[Connect Claude Code to tools via MCP](https://code.claude.com/docs/en/mcp),
[MCP quickstart](https://code.claude.com/docs/en/mcp-quickstart),
[CLI reference](https://code.claude.com/docs/en/cli-reference),
[headless mode](https://code.claude.com/docs/en/headless).

## Three registration scopes, two files

| Scope | File | Available to |
|---|---|---|
| `local` (default) | `~/.claude.json`, under this project's entry | only you, only this project |
| `project` | `.mcp.json` at repo root | everyone who clones the repo, after approval |
| `user` | `~/.claude.json`, top-level `mcpServers` | only you, all projects |

`claude mcp add --transport stdio <name> -- <command> [args...]` (the `--`
separator is required — everything after it is the server's own command
line, untouched). `claude mcp list` / `claude mcp get <name>` / `/mcp`
(inside a session) show connection status. Editing `.mcp.json` by hand uses
the same JSON shape: `{"mcpServers": {"<name>": {"type": "stdio",
"command": ..., "args": [...]}}}`.

## Gotcha: `.mcp.json` servers need a one-time human approval, and the documented pre-approval knob has a known reliability gap

A project-scoped `.mcp.json` entry shows `⏸ Pending approval (run claude to
approve)` until a human runs `claude` interactively in that folder and
accepts the workspace-trust dialog (required as of v2.1.196 — a cloned repo
can't silently launch a subprocess). The documented way to pre-approve for
CI/teammates is `enabledMcpjsonServers: ["<name>"]` in a settings file, but:

- [anthropics/claude-code#24657](https://github.com/anthropics/claude-code/issues/24657)
  (opened 2026-02-10, closed as duplicate 2026-02-14) reports this setting
  being silently ignored specifically when it lives in
  `.claude/settings.local.json`.
- Verified live in this repo: `agentlab/.claude/settings.local.json` has
  `"enabledMcpjsonServers": ["hn-search"]`, but the thing actually gating
  the connection is `~/.claude.json`'s per-project
  `"hasTrustDialogAccepted": true` — set once by a past interactive
  `claude` session, not by the settings file. Don't assume the settings-file
  entry alone reproduces the connection on a fresh clone/machine; the trust
  dialog has to be accepted at least once, interactively, regardless.
- Approvals in `.claude/settings.local.json` only apply *after* the trust
  dialog is accepted for that folder in the first place — so the ordering
  is: trust the folder once (interactive), *then* the settings-file
  approval list takes effect.

## Scripting a real end-to-end check without the approval workflow

For a reproducible, non-interactive test (rather than "trust once, forever"),
skip `.mcp.json` discovery entirely:

```bash
claude --bare --strict-mcp-config --mcp-config ./mcp-config.json \
  --allowedTools "mcp__<server>__<tool>" \
  --model haiku \
  --output-format stream-json --verbose \
  -p "<prompt that requires the tool>"
```

- `--mcp-config <file>` loads servers from an explicit JSON file (same
  shape as `.mcp.json`) passed on the command line — this is documented
  elsewhere as unaffected by the settings that gate auto-discovered
  servers, which is a strong (but not 100%-confirmed for the approval-prompt
  case specifically) signal it bypasses the pending-approval workflow.
- `--strict-mcp-config` makes Claude Code use *only* the servers named on
  the command line — needed so a repo's own `.mcp.json` doesn't leak into
  a supposedly-isolated test.
- `--bare` skips auto-discovery of hooks/skills/plugins/MCP/memory/CLAUDE.md
  and doesn't read OAuth/subscription credentials — requires
  `ANTHROPIC_API_KEY` in the environment instead. Documented as "the
  recommended mode for scripted and SDK calls."
- Tool names inside Claude Code are `mcp__<server>__<tool>` (bare servers)
  or `mcp__plugin_<plugin>_<server>__<tool>` (plugin-bundled servers) — use
  this exact form in `--allowedTools`, permission rules, a skill's
  `allowed-tools`, or a hook matcher.
- `-p --output-format stream-json` **requires** `--verbose` or Claude Code
  errors — this is a real, commonly-hit gotcha, not optional styling.
  Event types on the JSONL stream: `system` (subtype `init`, carries
  `mcp_servers: [{name, status}]` — check this to confirm the server
  actually connected, not just that the config parsed), `assistant`
  (tool calls are `message.content[]` entries with `type: "tool_use"`,
  `name`, `input`), `user` (tool results), `result` (final: `subtype`,
  `result` text, `is_error`, `total_cost_usd`, `session_id`).
- `--model haiku`/`sonnet`/`opus`/`fable` are valid short aliases for
  `--model`, alongside a full model ID — use the cheapest for a smoke test.

This is the first pattern in the knowledge base that requires a real,
billed API call to verify (unlike the in-memory `Client` tests in
[[mcp-python-sdk]], which are free/offline) — call this out explicitly in
anything built against it, and keep the prompt/model minimal.

## Open question

Whether `--mcp-config`-passed servers truly skip the pending-approval
prompt, or only the settings that gate *auto-discovered* servers — not
confirmed against a live run as of this writing. Verify empirically before
relying on it in an unattended/CI context; the fallback is a one-time
`claude mcp add-json ... --scope local` run non-interactively before the
scripted part.

Research note: [2026-08-08-mcp-connect-claude-code](../research/2026-08-08-mcp-connect-claude-code.md).

Related: [[mcp-python-sdk]] (the SDK-level, offline half of MCP testing this
note's real-host half complements), [[agent-skills]] (the `allowed-tools`
mechanism and Claude-Code-vs-API distinction rhymes with the
`--allowedTools`/`mcp__<server>__<tool>` naming here).
