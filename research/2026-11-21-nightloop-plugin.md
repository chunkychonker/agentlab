# nightloop m8: the Claude Code plugin (phase agents, publish guard, `run_silent`)

Mode: project:nightloop, milestone 8. Date: 2026-11-21.

## Why milestone 8 and not 7b

PLAN.md still shows 7b (claim wiring) `not-started`, but PR #104
(`cycle/2026-11-20-nightloop-claim-wiring`) is open and is that work. Re-researching
it would repeat the PR #5/#6 mistake. 7b is not a predecessor of 8 in any code
sense: the plugin touches no claim code. So this cycle takes milestone 8 and adds a
decisions-log entry saying why. 7b stays "built, awaiting merge"; do not mark it done here.

## Question

What is the smallest plugin that gives nightloop's three phases (research, build,
review) their agent templates, a publish-guard `PreToolUse` hook and a `run_silent`
wrapper, that loads in Claude Code, and whose contract can be checked offline?

## Findings

All from the official docs, fetched 2026-11-21 (current; the plugin pages describe CLI 2.1.3xx features such as `engines`, v2.1.323).

1. **Layout.** `.claude-plugin/plugin.json` is optional (only `name` required, kebab-case, `claude-`/`anthropic-` prefixes rejected). Defaults: `agents/`, `hooks/hooks.json`, `.mcp.json`, `skills/`, `bin/`. Manifest component paths must start `./`, stay inside the root and exist. Note `agents` in the manifest *replaces* the `agents/` scan; don't set it. ([plugins-reference](https://code.claude.com/docs/en/plugins-reference), [components](https://code.claude.com/docs/en/plugins/components))
2. **`bin/` is on the Bash tool's PATH** while the plugin is enabled (after the user's PATH, so it cannot shadow `git`). That is the natural home for `run_silent`: agents call it as a bare command. It needs `chmod +x`. ([components, Executables](https://code.claude.com/docs/en/plugins/components))
3. **Plugin agents: supported frontmatter** is `name, description, model, effort, maxTurns, autoCompactWindow, tools, disallowedTools, skills, memory, background, omitClaudeMd, isolation, color`. **Ignored for plugin agents: `permissionMode`, `hooks`, `mcpServers`, `initialPrompt`**, for security. So the guard cannot live in an agent's frontmatter; it must be in `hooks/hooks.json`. And `permissionMode: dontAsk` cannot be set in the template; nightloop already passes `--permission-mode dontAsk` on argv (m3), so nothing is lost, but the template must not pretend to set it. ([sub-agents](https://code.claude.com/docs/en/sub-agents), [components, Agents](https://code.claude.com/docs/en/plugins/components))
4. **Names.** A plugin agent `builder` in plugin `nightloop` is `nightloop:builder`. `claude --agent builder` works if the name is unique, else use `nightloop:builder`. The m3 argv builds `--agent <cfg.agent>`, so a config can name `nightloop:builder` with no code change. Unverified live: that `-p --agent nightloop:builder` resolves with the plugin loaded.
5. **The hook payload carries `agent_type`** ("present when the session uses `--agent` or the hook fires inside a subagent"; a subagent's type wins over the session's) ([hooks](https://code.claude.com/docs/en/hooks)). agentlab's bash guard needs `AGENTLAB_PHASE` in the env, because it is set per phase by `run.sh`. nightloop's runner passes no env (PLAN m3 known limit). Two choices: (a) make the runner export `NIGHTLOOP_PHASE` (touches m3), or (b) let the guard not need a phase at all. Nightloop has no `maintain` phase (the `Shipper` pushes, outside any model), so *no* model phase may ever publish. Option (b) is simpler and fail-closed: block every `git push` and every non-read-only `gh` call, whatever the agent. Cost: a human using the plugin interactively gets the guard too; document it, and note the hook only matters while the plugin is enabled.
6. **Hook mechanics** (same as [[claude-code-hooks]], re-confirmed): exit 2 blocks and stderr goes to the model; `PreToolUse` stays blocked even if a JSON `allow` is returned; exit 1 / crash is non-blocking, so the script must catch its own errors and exit 2. Exec form (`command` + `args`, no shell) is available: `{"type":"command","command":"python3","args":["${CLAUDE_PLUGIN_ROOT}/hooks/publish_guard.py"]}` avoids the quoting trap. `CLAUDE_PLUGIN_ROOT` is exported to the hook process. It is **not** present in the Bash tool's env, so agents' bodies must use the `${CLAUDE_PLUGIN_ROOT}` text substitution (works in agent Markdown bodies) or bare `run_silent` from `bin/`.
7. **Hooks under `-p`:** docs show hooks firing in `-p` sessions in several places, but nothing states how `dontAsk` interacts with them. agentlab's live pipeline already relies on `publish_guard.py` in `-p` + dontAsk phases via settings, which is evidence, not proof, for plugin-registered hooks. Needs a live check (human step, same status as m5/m6's).
8. **Offline authority.** `claude plugin validate <dir> [--strict]` checks manifest, paths, MCP entries. It does not check that a hook script exists or runs (the 2026-10-24 note found the same gap for MCP args: [[claude-code-plugins]]). So the offline test must check the hook command's script path itself.
9. **Existing code to port, not rewrite:** `.claude/hooks/publish_guard.py` (shlex tokenizer, `rtk`/`env`/`sudo` prefixes, nested `bash -c`, heredoc handling, read-only `gh` list, fail closed on unparseable). Its only agentlab-specific parts are the `AGENTLAB_PHASE` check and the `maintain` exemption. Python 3.9 stdlib only there; nightloop is 3.11+ stdlib. Copy with those two parts removed; do not import from `.claude/` (the project must stand alone, and the live pipeline's guard must not be edited, per the 2026-11-06 decision). Add a drift test? No: a copy that is *meant* to diverge needs no drift test; the shared cases are asserted by the plugin's own test table.
10. **`run_silent` contract** (from agent-pipeline practice; PLAN m8 text): run a command, print one line on success, the full output on failure, and return the command's exit code. Specify: `run_silent <label> -- <cmd> [args...]`. Success: `ok: <label>`. Failure: `FAIL: <label> (exit N)` then the full combined output. Command not found / not executable: exit 127 with the message. Combined stdout+stderr order preserved (single pipe). Written in Python (stdlib `subprocess`) with a `#!/usr/bin/env python3` shebang, not bash, so the test is portable and a timeout is easy. Max output: do not truncate (the point is the full output on failure); say so.

Not looked at: marketplaces/publishing (out of scope, milestone 12 docs), `claude plugin eval`.

## Build proposal

Intent. Ship `projects/nightloop/plugin/` as one Claude Code plugin named `nightloop`: three phase agent templates, a deny-always publish guard hook, and a `run_silent` executable. Out of scope: changing the phase runner or its argv (no `--plugin-dir` plumbing; loading the plugin is a documented human step), the scheduler, the reviewer benchmark, a marketplace entry, and any live Claude run in the test suite. 7b is untouched.

Layout:

```
projects/nightloop/plugin/
  .claude-plugin/plugin.json       name "nightloop", version "0.1.0", description, author
  agents/researcher.md  builder.md  reviewer.md
  hooks/hooks.json                 PreToolUse, matcher "Bash", exec-form python3 publish_guard.py
  hooks/publish_guard.py           ported; see below
  bin/run_silent                   chmod +x
projects/nightloop/nightloop/plugin_check.py   pure checker (below)
projects/nightloop/tests/test_plugin_*.py
```

Behavioral spec.

- `publish_guard.decide(payload: object) -> tuple[bool, str]` is pure and takes no phase. Non-object payload → block. Non-`Bash` tool → allow. Bash with non-string command → block. Block `git push` (global options, `-C`, prefixes, nested `bash -c` up to depth 3) and every `gh` call not in the read-only list; unparseable text that mentions `git ... push` or `gh` → block; otherwise allow. `main()` reads stdin, catches *every* exception and exits 2 with a reason (exit 1 would fail open).
- Agent templates: frontmatter has only supported plugin fields (`name`, `description`, `model`, `tools`); none of `permissionMode`, `hooks`, `mcpServers`, `initialPrompt`. `tools` must equal the allowlist in `examples/agentlab.nightloop.toml` for that phase, minus `allow_unrestricted_bash` entries being narrowed: the template uses `Bash` only for the builder and reviewer exactly as the example config does; the test asserts equality so the two cannot drift. Bodies state the phase's artifact contract (research: write `research/YYYY-MM-DD-<slug>.md`; build: commit-free increment; review: write `logs/last-review.md` ending in a `VERDICT: PASS|FAIL` line, the format `nightloop.gates.parse_verdict` accepts, so the test feeds each template's own example verdict line through `parse_verdict`). Bodies tell the agent to run tests via `run_silent`.
- `plugin_check.check_plugin(root: Path) -> list[Problem]`: pure over a path read once. Problems: manifest missing/invalid JSON/no `name`/non-kebab name/reserved prefix; any manifest path not starting `./`, containing `..`, or missing; `agents` key set (it would replace the scan); agent frontmatter with an ignored field; hook command's script path (after substituting `${CLAUDE_PLUGIN_ROOT}`) missing; `bin/*` not executable; `hooks.json` lacking the top-level `"hooks"` wrapper (a file with only the event map fails to load, per the manifest reference).
- `run_silent`: as in finding 10. Exit code equals the child's. `--` required; missing label or command → exit 2 with usage.

Acceptance criteria (the self-test, `cd projects/nightloop && python3 -I -m unittest discover -v`, stdlib only, no network, no model):

1. `check_plugin(plugin/)` returns `[]`; and each defect above, injected into a temp copy, yields exactly the matching Problem (one test per defect, so a checker that always returns `[]` fails).
2. Guard table: ≥25 recorded payloads. Blocked: `git push`, `git push origin main`, `git -C x push`, `rtk git push`, `env A=1 git push`, `bash -c 'git push'`, nested 3 deep, `gh pr create`, `gh pr merge`, `gh api -X POST …`, `echo hi; git push`, `` `git push` ``, unparseable `echo 'x; git push`. Allowed: `git status`, `git commit -m "push the button"`, `git log --oneline`, `gh pr view 3`, `gh pr list`, `run_silent tests -- python3 -m unittest`, `cat <<'EOF'` heredoc mentioning `git push`, non-Bash tool, and an unparseable command that mentions neither. Non-dict payload and missing command block. Run the script as a subprocess with these on stdin and assert exit code 0 or 2 and that the reason is on stderr, not stdout.
3. `main()` fails closed: empty stdin, invalid JSON, and a monkeypatched `decide` that raises each exit 2.
4. `run_silent`: success prints exactly one line `ok: <label>` and exits 0; failing child prints `FAIL: <label> (exit 3)` and the child's full stdout+stderr (a 5,000-line output is not truncated) and exits 3; missing binary exits 127; no `--` exits 2. Run as a subprocess.
5. Each agent template's `tools` equals the example config's allowlist for its phase; each template's sample verdict line parses as PASS/FAIL via `parse_verdict`; no template contains an ignored frontmatter field.
6. Optional, skipped when `claude` is not on PATH or `NIGHTLOOP_LIVE` is unset (never in the default run): `claude plugin validate plugin/ --strict` exits 0. Record the result and CLI version in the README. A billed live `-p --plugin-dir … --agent nightloop:builder` run that tries `git push` and must see the block is a documented human step, not a test.
7. Purity: `plugin_check.py` imports no I/O module beyond `pathlib`/`json`/`re` (follow `tests/test_gates_purity.py`'s pattern); `publish_guard.py` is exempt (it is an edge script).

Failure domain: the guard fails closed, so a bug there blocks agents' Bash publish attempts, never publishes. A broken `run_silent` is a visible failure with the child's exit code. Nothing here runs unless a user enables the plugin.

Docs the builder must write: `plugin/README.md` (what loads, how to load: `claude --plugin-dir projects/nightloop/plugin` or install; `--agent nightloop:builder`; the guard is a backstop not a boundary, text matching can be dodged by a script file or `python3 -c`; the interactive-session cost).

## Open questions

- Does `claude -p --agent nightloop:builder --plugin-dir …` resolve the plugin agent, and does the plugin `PreToolUse` hook fire under `-p --permission-mode dontAsk`? Docs do not say; needs one live human run (as with m5/m6).
- Is `agent_type` set in the payload for a `-p --agent` run in the exact scoped form (`nightloop:builder`) or bare? Unneeded by the deny-always design, but a later per-phase policy would depend on it.
- `tools` in plugin agents: listed as supported in the components page, though sub-agents page left it implicit. The test asserts the template text only, not host behaviour.
- Whether `m3`'s argv should gain `--plugin-dir` is a decision for the owner; this proposal leaves argv unchanged.
- The fetched docs contain no instructions to the agent; nothing to ignore.
