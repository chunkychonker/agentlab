# Agent SDK permission gate: PreToolUse hook vs `can_use_tool`

Date: 2026-11-03. Backlog: no plain `[ ]` feature item existed (only stale health findings), so the researcher filed a new one under Coding agents, as 2026-09-28 and 2026-10-29 did. Open PRs checked: only #62 (pipeline), no overlap. `examples/agent-sdk-minimal-loop/` explicitly put hooks/permissions out of scope; `examples/pretooluse-bash-guard/` covers the *shell-hook* form only.

## Question
When you embed Claude Code via the Agent SDK, which of its two programmatic gates (a `PreToolUse` callback hook or the `can_use_tool` callback) actually sees which calls, and what is the smallest offline-testable policy that uses both correctly?

## Findings
Sources, all read 2026-11-03: [Configure permissions](https://code.claude.com/docs/en/agent-sdk/permissions), [Handle approvals and user input](https://code.claude.com/docs/en/agent-sdk/user-input), [Hooks in the SDK](https://code.claude.com/docs/en/agent-sdk/hooks), [PyPI claude-agent-sdk](https://pypi.org/project/claude-agent-sdk/) (0.2.177, released 2026-11-03; the lab's note pinned 0.2.168).

- **Evaluation order (docs, 6 steps):** 1 hooks, 2 deny rules (`disallowed_tools`, settings), 3 ask rules, 4 permission mode, 5 allow rules (`allowed_tools`), 6 `can_use_tool`. `can_use_tool` is the LAST resort: it only fires for calls nothing earlier resolved.
- **The trap:** a tool listed bare in `allowed_tools`, or any call under `acceptEdits`/`bypassPermissions`, never reaches `can_use_tool`; checks put there are "silently bypassed". Docs' fix: checks that must run on every call go in a `PreToolUse` hook. A hook `deny` applies even in `bypassPermissions` and beats a hook `allow` from another hook (precedence deny > defer > ask > allow). A hook `allow` does NOT skip deny/ask rules, and cannot approve recognized critical-path `rm -rf ~`.
- **`allowed_tools` does not constrain `bypassPermissions`** (`allowed_tools=["Read"]` + bypass still approves Bash). Use `disallowed_tools`. `disallowed_tools=["Bash"]` removes the tool definition; `["Bash(rm *)"]` denies matching calls in every mode but matches "as written" (`/bin/rm` falls through).
- **Python callback shapes:** `can_use_tool(tool_name: str, input_data: dict, context: ToolPermissionContext) -> PermissionResultAllow(updated_input=..., updated_permissions=...) | PermissionResultDeny(message=...)`. Deny `message` is shown to Claude. Pre-v2.1.207 CLI rejected an allow that omitted `updated_input`; always pass it. `context.suggestions` exists (needs SDK >=0.1.80); the signal field is reserved.
- **Python hook shape:** `async def hook(input_data, tool_use_id, context) -> dict`; PreToolUse `input_data` has `tool_name`, `tool_input`, `hook_event_name`, `session_id`, `cwd`. Deny = `{"hookSpecificOutput": {"hookEventName": "PreToolUse", "permissionDecision": "deny", "permissionDecisionReason": ...}}`; allow/no opinion = `{}`. Register `hooks={"PreToolUse": [HookMatcher(matcher="Bash|Write", hooks=[fn])]}`. Matchers match tool NAME only (regex allowed, `^mcp__`); path/command logic lives in the callback. Python's reserved-word key is `continue_`/`async_`.
- **Gotcha - streaming input:** docs' Python examples feed `prompt` as an async generator AND register a do-nothing PreToolUse hook ("Required workaround: dummy hook keeps the stream open for can_use_tool"). With a plain string prompt, `can_use_tool` is reportedly not usable. Not independently verified; builder must test against the installed SDK source.
- **Fail-closed on timeout:** a PreToolUse callback that times out blocks the call (since CLI v2.1.210; earlier it surfaced as user rejection). Callbacks that *raise* are not documented here - treat as unverified and catch inside the callback, returning a deny. `Dangerous rm` critical-path calls reach `can_use_tool` with `decisionReason` starting `Dangerous rm`.
- `dontAsk` mode turns every would-be prompt into a deny without calling the callback: the declarative way to get "allowlist only".
- Contrast with the shell-hook form recorded in [[claude-code-hooks]]: there exit 2 / JSON on stdout; here a Python return dict. Same decision vocabulary.
- Existing offline harness: `examples/agent-sdk-minimal-loop/fake_cli.py` implements the SDK `Transport` and speaks the stream-json control protocol (documented as an internal API). Whether it can be extended to script `can_use_tool` / `hook_callback` control requests is unverified.

## Build proposal
Directory: `examples/agent-sdk-permission-gate/` (free: `ls examples/` on main has no such name; closest are `agent-sdk-minimal-loop`, `pretooluse-bash-guard`).

**Intent.** Show, with a runnable policy, that a rule enforced only in `can_use_tool` is bypassed by `allowed_tools`/`acceptEdits`/`bypassPermissions`, and that the same pure policy wired into a `PreToolUse` hook is not. Out of scope: interactive y/n prompting, `AskUserQuestion`, `defer`, TypeScript, subagent inheritance, `updated_permissions` persistence.

**Files**
- `policy.py` - pure core, no SDK import: `@dataclass(frozen=True) class Verdict(allowed: bool, reason: str)`; `decide(tool_name: str, tool_input: Mapping[str, object]) -> Verdict`. Rules (named constants): deny `Write`/`Edit` to basename `.env` (case-insens) and any path under `/etc`; deny `Bash` whose command contains `rm -rf` or `git push --force`; allow everything else. Unknown/missing `file_path` for Write/Edit = deny (fail closed).
- `gates.py` - adapters: `pre_tool_use_hook(input_data, tool_use_id, context) -> dict` and `can_use_tool(tool_name, input_data, context) -> PermissionResultAllow | PermissionResultDeny`, both thin wrappers over `decide`. Hook catches any exception and returns a deny.
- `options.py` - `build_options(mode: Literal["callback-only","hook"], permission_mode: str) -> ClaudeAgentOptions`; `hook` mode registers the hook with matcher `"Bash|Write|Edit"` plus `can_use_tool`; both set `setting_sources=[]`, `max_turns`, pinned model alias.
- `agent.py` - billed live demo (optional): runs a prompt asking to write `.env` under `acceptEdits` in both modes in a temp dir, prints whether the file exists and the gate's audit log. Streaming-input prompt generator per docs.
- `test_gates.py` - offline, stdlib `unittest`, no key/CLI.
- `requirements.txt`: `claude-agent-sdk==0.2.177` (re-check on build day; ships near daily).
- `README.md` with the evaluation-order table and a bypass matrix; live transcript marked `<!-- transcript-check: skip ... -->` like `examples/mcp-connect-claude-code/README.md`.

**Acceptance criteria (offline)**
1. `decide` table test: `.env` write, `/etc/passwd` write, `rm -rf /tmp/x`, `git push --force origin main` -> denied with non-empty reason; `Read`, `ls`, `src/app.py` write -> allowed; Write with no `file_path` -> denied.
2. Hook adapter returns exactly the documented deny dict (`hookEventName`, `permissionDecision == "deny"`, reason) for a recorded PreToolUse payload, and `{}` for an allowed one; a payload that makes `decide` raise yields a deny, not an exception.
3. `can_use_tool` adapter returns `PermissionResultDeny` with the reason message, and `PermissionResultAllow(updated_input=<same dict>)` otherwise.
4. Static structural test (the lab's point): `build_options("callback-only", "bypassPermissions")` is asserted to contain NO PreToolUse hook, and a helper `shadowed_by_mode(options) -> bool` (pure, encoding the doc's step order: bypass/acceptEdits mode or bare `allowed_tools` entry => callback unreachable for that tool) returns True for it and False for `build_options("hook", ...)`, whose hook is registered. `hook` mode options must contain a `HookMatcher` for `PreToolUse` whose callback is `pre_tool_use_hook`.
5. If `fake_cli.py` can be extended cheaply (read the SDK's `_internal/query.py` control-request handling for subtypes `can_use_tool` and `hook_callback`), add one end-to-end test driving a scripted control request through `query()` to the adapter; if not, state in the README that criteria 1-4 are the offline coverage and the end-to-end claim rests on the live run.
6. Live (optional, billed): under `acceptEdits`, callback-only mode lets `.env` be written; hook mode leaves no file and the model's reply cites the deny reason. README records the real outcome - if reality contradicts the docs, report that (docs-vs-reality, per [[claude-code-mcp-connection]]).

**Failure modes to state:** CLI missing/crash (`CLINotFoundError`/`ProcessError`); string prompt silently never calling `can_use_tool`; model routes around via Bash (`echo > .env`) - the matcher covers Bash but `decide` is a substring check, not a sandbox (docs point to sandboxing for that); hook timeout blocks calls.

## Open questions
- Does `can_use_tool` really require streaming-input prompts and a dummy hook in 0.2.177, or is that stale docs? Test it, record the answer.
- What does the SDK/CLI do if a Python hook callback raises (fail open or closed)? Not documented in the pages read.
- Can `ScriptedCLI` script `can_use_tool` / `hook_callback` control requests without reimplementing the protocol?
- Whether a hook `deny` under `bypassPermissions` holds in practice (docs say yes; [[claude-code-hooks]] lists it unverified for the shell form).
