# Agent SDK permission gate: `PreToolUse` hook vs `can_use_tool`

When you embed Claude Code with the
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/permissions), it
gives you two programmatic places to put a "never do this" rule. They are
**not** interchangeable:

- A **`PreToolUse` hook** runs *first* in Claude Code's permission order. It
  runs for every call whose tool name its matcher matches, in every permission
  mode, including `bypassPermissions`.
- **`can_use_tool`** runs *last*. It only sees calls that nothing earlier
  approved or denied. Any call that `acceptEdits`, `bypassPermissions`, or a
  bare `allowed_tools` entry approves never reaches it, so a rule that lives
  only there is skipped without any error.

This example has one pure policy (`decide`). It wires that policy into both
gates and then checks, statically and over the SDK's real control protocol,
which arrangement leaves gaps.

From the research note:
[`research/2026-11-03-agent-sdk-permission-gate.md`](../../research/2026-11-03-agent-sdk-permission-gate.md).
Neighbours: [`agent-sdk-minimal-loop`](../agent-sdk-minimal-loop/) (the SDK
loop, with permissions explicitly out of scope) and
[`pretooluse-bash-guard`](../pretooluse-bash-guard/) (the same hook written as
a *shell* hook in `settings.json`).

## What's here

| File | What it is |
|------|-----------|
| `policy.py` | The pure core: `decide(tool_name, tool_input) -> Allowed \| Denied`. It denies `Write`/`Edit` to `.env` (in any case) or under `/etc`, and `Bash` commands containing `rm -rf` or `git push --force`. A missing, non-string, or relative path fails closed. It imports neither the SDK nor any I/O. |
| `gates.py` | `build_gates(audit) -> Gates(hook, can_use_tool)`: two thin adapters over `decide` that report every verdict to an injected audit sink. |
| `blind_spots.py` | Pure. `approved_before_callback(mode, allowed_tools, gated)` returns the tools whose calls can be approved before `can_use_tool` runs. `unguarded(options, gated)` returns those of them that no `PreToolUse` hook covers either. |
| `options.py` | `build_options(gates, layout, permission_mode, cwd)`. The two layouts are `CALLBACK_ONLY` and `HOOK_AND_CALLBACK`. They differ only in whether the hook is registered. |
| `agent.py` | The live demo (billed). It runs the same `.env` write once per layout under `acceptEdits` and checks the disk afterwards. |
| `fake_cli.py` | Used only by the tests. A scripted stand-in for the CLI's permission traffic, built on the SDK's `Transport` interface. |
| `test_gates.py` | The offline self-test: 13 checks. It needs no key, no subprocess, and no network. |
| `requirements.txt` | `claude-agent-sdk==0.2.177` (bundles CLI 2.1.317), pinned exactly. |

## The evaluation order, and where each gate sits

From the [permissions doc](https://code.claude.com/docs/en/agent-sdk/permissions),
read 2026-11-03:

| Step | What runs | Can approve? | This example |
|---|---|---|---|
| 1 | Hooks (`PreToolUse`) | allow (doesn't skip steps 2-3) / **deny (final)** | `HOOK_AND_CALLBACK` puts `decide` here |
| 2 | Deny rules (`disallowed_tools`, settings) | - | none |
| 3 | Ask rules (settings) | - | none (`setting_sources=[]`) |
| 4 | Permission mode | **yes**: `bypassPermissions` approves everything; `acceptEdits` approves Write/Edit plus `mkdir touch rm rmdir mv cp sed` inside cwd | the variable under test |
| 5 | Allow rules (`allowed_tools`) | **yes**: a bare `X`, `X()` or `X(*)` approves every call to `X` | `allowed_tools=[]` |
| 6 | `can_use_tool` | allow / deny | both layouts put `decide` here |

The table below is `unguarded(build_options(...), GATED_TOOLS)` for each cell,
asserted by check 7. It lists the tools whose calls can run without `decide`
ever seeing them:

| permission_mode | `CALLBACK_ONLY` | `HOOK_AND_CALLBACK` |
|---|---|---|
| `default` | none | none |
| `dontAsk` | none (would-be prompts are *denied*, the callback is never called) | none |
| `acceptEdits` | **Bash, Edit, Write** (inside cwd) | none |
| `bypassPermissions` | **Bash, Edit, Write** | none |

`plan` and `auto` are deliberately left out of the model, and passing either
raises `ValueError`. In plan mode, whether commands reach the callback depends
on whether auto mode is available to the session. In auto mode, a classifier
decides at runtime. Neither can be answered from the options alone.

## Run the self-test (no API key needed)

The test drives the SDK's real `query()`, so it needs the pinned package
installed. Nothing else is required.

```bash
cd examples/agent-sdk-permission-gate
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_gates.py
```

Expected output:

```
ok  decide: 14 hostile calls denied with reasons, 7 benign allowed
ok  a Denied verdict with an empty reason cannot be constructed
ok  hook matcher 'Bash|Edit|Write' is exactly the set of tools decide can deny
ok  hook: documented deny dict, {} on allow, malformed payloads denied not raised
ok  can_use_tool: PermissionResultDeny(message=reason), Allow echoes updated_input
ok  options: callback-only has no hook; hook layout registers ours with on_failure=block
ok  callback-only leaves Bash/Edit/Write unguarded under acceptEdits and bypass; hook layout never
ok  blind spots match the SDK's CanUseToolShadowedWarning on 8 configs; acceptEdits is the one it does not flag
ok  query(): hook registered on the wire with onFailure=block; CLI receives our deny/allow
ok  string prompt + can_use_tool alone: stdin stays open, no dummy hook needed
ok  a raising hook becomes an SDK error reply (fail open by default); ours replies deny
ok  on_failure=block: SDK refuses to connect to a CLI that would fail open
ok  main exits 2 without ANTHROPIC_API_KEY; matches_docs only for written/blocked

All 13 self-tests passed.
```

Checks 9-12 run through `fake_cli.ScriptedPermissionCLI`, which stands in for
the CLI subprocess. In each check:

1. It answers the SDK's `initialize` request and records the hook config the
   SDK registered.
2. It waits for the prompt.
3. It sends the `hook_callback` and `can_use_tool` control requests the test
   scripted.
4. It records the replies the SDK writes back.

The fake does **not** decide *whether* the real CLI would send a request,
because it doesn't model permission modes. So the offline suite proves the
wire contract and the structure, but not the bypass itself. That claim rests
on the docs (encoded in `blind_spots.py`), on the SDK's own warning agreeing
with them (check 8), and on the live run below.

## What the installed SDK says vs. the docs

I read the pinned SDK's source (`claude_agent_sdk/_internal/query.py`,
`_internal/client.py`, `types.py`) to answer the note's open questions. Each
answer is covered by a check:

- **"`can_use_tool` needs a streaming-input prompt plus a dummy hook": stale
  for 0.2.177.** The docs' Python examples still include the
  `# Required workaround: dummy hook keeps the stream open` block. But
  `Query.wait_for_result_and_end_input` keeps stdin open until the run ends
  whenever `can_use_tool`, hooks, or SDK MCP servers are present, and its
  docstring says string prompts are "fully covered". Check 10 confirms it with
  a plain string prompt and no hook. Its control case (no callback, so stdin
  closes at once) shows the fake really detects a closed stdin.
- **A Python hook that raises fails open by default.** The SDK turns the
  exception into an `error` control reply (check 11). The `HookMatcher`
  docstring says that with `on_failure` unset (or `"continue"`), the CLI
  counts a failure as *no decision*. So a buggy guard hook lets the call
  through. This example defends against that in two ways. `gates.py` catches
  malformed payloads and replies `deny`. `options.py` also sets
  `on_failure="block"`, which covers a raise from the audit sink, a `None`
  return, or a timeout. The SDK refuses to connect (`CLIConnectionError`) to a
  CLI that doesn't advertise support for it, so the setting can't silently
  stop working (check 12).
- **The SDK already warns about shadowing, except in `acceptEdits`.**
  `CanUseToolShadowedWarning` fires for `bypassPermissions` and for
  whole-tool `allowed_tools` entries. Check 8 confirms that `blind_spots.py`
  agrees with it on 8 configurations. Under `acceptEdits` the SDK says
  nothing, by design (the docs say "the `acceptEdits` mode [doesn't] trigger
  it"), yet Write/Edit inside cwd still skip the callback. That gap is what
  this example's live demo targets.

## Run it live (billed)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python agent.py
```

It runs two sessions under `permission_mode="acceptEdits"`, each in a fresh
temp directory that is also the session's `cwd`. In each session the model is
asked to Write `<cwd>/.env`. For each layout the script prints the tool calls,
whether `.env` exists afterwards, every gate decision from the audit log, and
the final text. Exit codes:

| Code | Meaning |
|------|---------|
| `0` | Matches the docs: callback-only wrote `.env` (the callback was never consulted); the hook layout left no file |
| `1` | A session ended on an error result |
| `2` | `ANTHROPIC_API_KEY` is unset. Nothing runs and nothing is billed. |
| `3` | **Contradicts the docs.** Record the output here. |
| `4` | Bad usage (the script takes no arguments) |

`CLINotFoundError` and `ProcessError` (the CLI is missing or crashed) propagate
with a traceback. The model is `claude-haiku-4-5` (`options.MODEL`).

**Not yet run live.** No API key was available when this was built. Whoever
first runs it should paste the output here, behind a
`<!-- transcript-check: skip ... -->` directive like
[`mcp-connect-claude-code`](../mcp-connect-claude-code/README.md) uses. In
particular, the hook-deny-under-`bypassPermissions` claim from the docs is
still unverified in practice. To test it, change `PERMISSION_MODE` in
`agent.py`.

## Failure modes

- **The policy is a tripwire, not a sandbox.** `decide` checks strings as they
  are written. `echo X > .env` through Bash, `rm -r -f`, a symlink into
  `/etc`, and `python -c "..."` all pass. The hook's matcher covers Bash, but
  the policy only knows two command fragments. Real containment needs the
  SDK's `sandbox` option or an OS-level jail.
- **Tools outside `GATED_TOOLS` are not checked.** `NotebookEdit` writes files
  too. Here it is removed from the model's toolset (`tools=[Read, Bash, Edit,
  Write]`) instead of being gated. Check 3 enforces that the hook matcher and
  the set of tools the policy can deny stay identical.
- **Relative paths are denied.** `decide` has no cwd: `can_use_tool` isn't
  given one, and the policy stays the same for both gates. Claude Code's
  Write/Edit take absolute paths, so this should not bite in practice. If it
  does, the model sees the deny reason.
- **The hook's timeout blocks the call** (default 60 s, `HookMatcher.timeout`).
  That's the intended fail-closed behaviour, but a slow audit sink would stall
  every gated call.
- **Allow rules from settings files are invisible** to `blind_spots.py`. That
  is why `build_options` passes `setting_sources=[]`.
- **The `can_use_tool` adapter does not catch exceptions.** The SDK hands it
  typed arguments and `decide` is total, so only the audit sink can raise. If
  it does, the SDK sends an error reply, and what the CLI does with that is
  not documented.

## Deviations from the research note's proposal

- `Verdict(allowed: bool, reason: str)` became `Allowed | Denied(reason)`.
  An allow has no reason, and `Denied("")` raises. This makes the illegal
  state unrepresentable instead of something to validate.
- `shadowed_by_mode(options) -> bool` became `approved_before_callback(...)`
  plus `unguarded(options, gated)`, both returning the *set* of exposed tools.
  A bool can't say which tools are exposed, and the per-tool answer is what
  differs between `acceptEdits` and an allow rule. The proposal's criterion 4
  holds as `unguarded(...) == set()` for the hook layout.
- `build_options(mode: "callback-only" | "hook", ...)` takes a `Layout` enum
  and the `Gates` instance. The audit sink is injected instead of being module
  state.
- The policy denies **relative** `file_path`s (the proposal listed
  `src/app.py` as allowed). See Failure modes. It also catches `rm -fr`,
  `git push -f`, case and spacing variants, `//etc`, and `/private/etc`.
- The hook matcher is `Bash|Edit|Write` (derived from the policy's tool list),
  and it adds `on_failure="block"`. That option exists in 0.2.177 and wasn't
  known to the note.
- The self-test follows the repo's `ok  ...` / `All N self-tests passed.`
  runner, not `unittest`, so the transcript check can compare it.
- Criterion 5 (an end-to-end control-protocol test) was buildable, so it was
  built (checks 9-12). It is a new fake rather than an extension of
  `agent-sdk-minimal-loop/fake_cli.py`: that fake is MCP-specific, and
  importing a sibling example would couple the two.

## Scope

Two gates and one policy. There is no interactive prompting, no
`AskUserQuestion`, no `defer`, no `updated_permissions` persistence, no
subagent inheritance, and no TypeScript.
