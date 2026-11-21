# nightloop plugin for Claude Code

Milestone 8 of [nightloop](../PLAN.md). Research note:
[`research/2026-11-21-nightloop-plugin.md`](../../../research/2026-11-21-nightloop-plugin.md).

A Claude Code plugin named `nightloop`. It gives nightloop's three model phases
their agents, puts a guard in front of every Bash call so no phase can publish,
and adds a test wrapper that keeps passing runs out of the agent's context.

| Part | File | What it does |
|---|---|---|
| Agents | `agents/researcher.md`, `builder.md`, `reviewer.md` | One per phase: `nightloop:researcher`, `nightloop:builder`, `nightloop:reviewer`. Each body states the artifact its gate checks: a fresh note under `research/`, a change under `examples/` or `projects/`, a verdict file with one `VERDICT: PASS` or `VERDICT: FAIL` line. |
| Publish guard | `hooks/hooks.json`, `hooks/publish_guard.py` | A `PreToolUse` hook on `Bash`. Blocks `git push` and every `gh` call not on a read-only list (exit 2, reason to the model). |
| `run_silent` | `bin/run_silent` | `run_silent <label> -- <cmd> [args...]`: prints `ok: <label>` on success, or `FAIL: <label> (exit N)` and the full output on failure. Exits with the command's code. |

## Load it

For one session (interactive or `-p`):

```bash
claude --plugin-dir projects/nightloop/plugin --agent nightloop:builder
```

`claude --agent builder` also works if no other agent is named `builder`.

nightloop's own phase runner does **not** pass `--plugin-dir` yet (milestone 3's
argv is unchanged; whether it should is the owner's call). To use these agents
from `nightloop run`, the plugin has to be enabled for the phase's `claude`
process some other way, for example by installing it. Then point the config
at the agents: [`examples/plugin.nightloop.toml`](../examples/plugin.nightloop.toml)
names them by their bare names (`researcher`, `builder`, `reviewer`), because
the config's agent-name rule does not accept the colon in `nightloop:builder`.
Its models and tool lists equal the templates' frontmatter. A test checks that,
so the config and the templates cannot drift.

Plugin agents ignore `permissionMode`, `hooks`, `mcpServers` and
`initialPrompt` (Claude Code drops them for security), so the templates do not
set them. The permission mode comes from nightloop's `--permission-mode dontAsk`
flag, and the guard lives in `hooks/hooks.json`.

## The publish guard

nightloop's phases never publish. The shipper pushes and opens the PR outside
any model run, after the review passes. So the guard takes no phase and blocks
the same things for every agent:

- `git push`, including through global options (`git -C x push`), prefixes
  (`rtk`, `env A=1`, `sudo`, ...), `;`/`&&` chains, backticks, `$( )`, and
  `bash -c` nested up to 3 deep (deeper is blocked outright);
- any `gh` command except `pr list|view|status|checks|diff`, `issue
  list|view|status`, `repo view`, `run list|view` and `auth status`;
- anything it cannot read: a payload that is not a JSON object, a Bash call
  with no command, and any exception in the guard (exit 2, never 1, because a
  crashing hook would let the call through). Unparseable shell text is blocked
  only if it mentions `git ... push` or `gh`.

The text of a quoted heredoc fed to `cat`/`tee` is not checked, because it is
written to a file, not run.

**It is a backstop, not a boundary.** It matches text, so a script file,
`python3 -c`, or an alias gets past it. The phases' `--allowedTools` rules and
the shipper's own checks are the real controls.

**Cost:** while the plugin is enabled, an interactive session gets the guard
too, so you cannot `git push` through Claude Code's Bash tool. Disable the
plugin, or push from your own terminal.

It is a port of agentlab's `.claude/hooks/publish_guard.py` without the
`AGENTLAB_PHASE` requirement or the `maintain` exemption. It is a copy, not an
import, so the plugin stands alone. Both scripts are Python 3.9 stdlib only,
because `python3` on PATH may be macOS's `/usr/bin/python3`. The tests run them
under it when it is present.

## `run_silent`

`bin/` is on the Bash tool's PATH while the plugin is enabled, so agents call
`run_silent` as a bare command. The builder and reviewer templates tell them to
wrap test runs in it, for example
`run_silent tests -- python3 -m unittest discover -v`. Details:

- stdout and stderr share one pipe, so their order is kept. Output is never
  truncated: on failure the whole log is the point.
- The child's stdin is `/dev/null`, so a command that waits for input fails
  instead of hanging an unattended phase. There is no timeout of its own.
- A command that cannot start (not found, not executable) exits 127. Death by
  signal S exits 128+S. Bad usage exits 2 and runs nothing.

## Check it

```bash
cd projects/nightloop
python3 -I -m unittest discover -v          # whole suite, offline, stdlib only
python3 -I -m unittest discover -p 'test_plugin_*.py' -v   # this milestone only
```

`nightloop.plugin_check.check_plugin(Path("plugin"))` returns `[]` for this
plugin. It checks what `claude plugin validate` does not. On CLI 2.1.329
(2026-11-21), `claude plugin validate --strict` still passed a copy of this
plugin that had an ignored `permissionMode` field, a deleted hook script, and a
`run_silent` without its execute bit. `check_plugin` reports each of those, plus
the manifest rules (name, reserved prefixes, `./` paths, no `agents` key) and
the hooks file's top-level `"hooks"` wrapper.

Opt-in, no model call: `NIGHTLOOP_LIVE=1 python3 -I -m unittest discover -p
'test_plugin_live.py' -v` runs `claude plugin validate plugin/ --strict`. On
2026-11-21, with Claude Code 2.1.329, it exited 0 and listed the three agents,
the hook (exec form, `command` + `args`) and `bin: run_silent`.

## Not verified yet (human steps)

- That `claude -p --plugin-dir projects/nightloop/plugin --agent
  nightloop:builder --permission-mode dontAsk` resolves the plugin agent, and
  that the plugin's `PreToolUse` hook fires in that mode. Check it with one
  billed run that asks the agent to `git push`: it must see the guard's
  message. The docs do not say either way.
- Whether a bare `--agent builder` from a nightloop config picks up the plugin
  agent when the plugin is installed rather than loaded with `--plugin-dir`.
