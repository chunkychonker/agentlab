# Minimal agent loop, on the Claude Agent SDK

This is the same one-tool calculator agent as
[`examples/minimal-agent-loop/`](../minimal-agent-loop/), ported to the
[Claude Agent SDK](https://code.claude.com/docs/en/agent-sdk/python)
(`claude-agent-sdk`, Python). The hand-written version spells out every step of
the tool-use loop. Here, `query()` does those steps for you, and this example
shows which ones it took over and what that hides.

From the research note:
[`research/2026-10-17-agent-sdk-minimal-loop.md`](../../research/2026-10-17-agent-sdk-minimal-loop.md).

## What's here

| File | What it is |
|------|-----------|
| `calc_tool.py` | Pure core: `calculate(expression)`. It walks the expression's `ast` tree, allows only numbers and `+ - * / // % **`, and raises `CalcError` on anything else. No SDK import, no I/O. |
| `summary.py` | Pure fold: `summarize(messages)` turns the SDK's message stream into `Completed` or `Failed`. Being two separate types, a run that hit `error_max_turns` cannot be read as an answer. |
| `agent.py` | The SDK layer: the `@tool` handler, `build_options()`, `run()`, and `main()`. |
| `fake_cli.py` | Used only by the tests. A scripted stand-in for the Claude Code CLI that plugs into the SDK's public `Transport` interface, so `query()` runs its real code path offline. |
| `test_agent.py` | Offline self-test with 8 checks. No key, no subprocess, no network. |
| `requirements.txt` | `claude-agent-sdk==0.2.168`, pinned exactly. |

The calculator evaluator is copied from the hand loop rather than imported from
it. Importing a sibling example's private `_eval_node` would tie the two
examples together. The one change: errors are raised as `CalcError` instead of
returned as an `"Error: ..."` string, so the tool wrapper can set `is_error`
without checking for a text prefix.

## Run the self-test (no API key needed)

The test imports the SDK's message types and drives the SDK's real `query()`,
so it needs the pinned package installed. Nothing else is required.

```bash
cd examples/agent-sdk-minimal-loop
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
.venv/bin/python test_agent.py
```

Expected output:

```
ok  tool handler returns the correct product with no is_error
ok  bad input -> is_error result, nothing executed, core raises CalcError
ok  options wire mcp__calc__calculator and build a hermetic CLI command
ok  summarize: success, error_max_turns and API error are distinct; no result raises
ok  run(): SDK dispatches tools/call to the in-process tool; answer has the product
ok  SDK forwards is_error, and rejects schema-invalid args before our handler
ok  run(): error_max_turns + CLI exit 1 becomes Failed, not an answer
ok  main exits non-zero with a clear message when ANTHROPIC_API_KEY is unset

All 8 self-tests passed.
```

Checks 5-7 use `fake_cli.ScriptedCLI`, which replaces the subprocess. It speaks
the same stream-json control protocol the bundled CLI uses:

1. It answers the SDK's `initialize`.
2. It runs the MCP `initialize` handshake against our in-process server.
3. It sends `tools/call` for `calculator`.
4. It answers using whatever the SDK returned from step 3.

So when check 5 finds `6213276` in the answer, that value came from our handler
through the SDK's own dispatch. It was not scripted. Check 3 builds the exact
argv the SDK would pass to the CLI, using `SubprocessCLITransport._build_command`.
That method is private, which is acceptable only because the SDK is pinned
exactly.

## Run it live (billed)

```bash
export ANTHROPIC_API_KEY=sk-ant-...
.venv/bin/python agent.py "What is 4839 * 1284?"
```

It prints `tool_calls`, `num_turns`, `total_cost_usd`, and the final answer.
Exit codes:

| Code | Meaning |
|------|---------|
| `0` | Answered, and `mcp__calc__calculator` was called |
| `1` | The run ended on an error result (e.g. `error_max_turns`) |
| `2` | `ANTHROPIC_API_KEY` is unset. Nothing runs and nothing is billed. |
| `3` | The model answered without calling the tool |

`CLINotFoundError` and `ProcessError` (the CLI is missing or crashed) are
raised, with a traceback.

The script refuses to run without `ANTHROPIC_API_KEY`, even if a Claude Code
login exists on the machine. The bundled CLI would accept either, and the docs
don't say which one wins when both are present. Requiring the key means you
always know which account is paying.

**Not yet run live.** No API key was available when this was built, so there is
no captured live transcript. The SDK's cost overhead compared with the hand
loop is therefore **unmeasured** (see "What it hides"). Whoever first runs it
live should paste the output here, behind a
`<!-- transcript-check: skip ... -->` directive like
[`mcp-connect-claude-code`](../mcp-connect-claude-code/README.md) uses.

## Side by side: what the SDK now owns

The line numbers refer to [`minimal-agent-loop/agent.py`](../minimal-agent-loop/agent.py).

| Concern | Hand loop | Agent SDK (this example) |
|---|---|---|
| **The loop** | `for _ in range(max_turns)` around `messages.create` (L142-143) | A single `query(prompt=..., options=...)` call. The loop runs inside the Claude Code CLI subprocess. |
| **Stop condition** | `if response.stop_reason != "tool_use"` (L151) | Handled for you. You get a final `ResultMessage` with a `subtype`. |
| **Tool schema** | Hand-written JSON Schema in `TOOLS` (L95) | `@tool("calculator", ..., {"expression": str})`. The SDK builds the schema and validates arguments against it before your handler runs (check 6). |
| **Tool dispatch** | `TOOL_FUNCTIONS` dict (L118), lookup at L165, unknown-tool error at L167 | `create_sdk_mcp_server("calc", tools=[calculator])`. The CLI calls it over in-process MCP, and the model sees it as `mcp__calc__calculator`. |
| **tool_result plumbing** | Echo the assistant turn (L159), match `tool_use_id` (L175), append a user turn (L180) | Hidden. Your handler returns `{"content": [...]}`, adding `"is_error": True` on failure. The SDK passes that on as MCP `isError` (check 6). |
| **Message history** | The `messages` list you grow yourself | Hidden in the CLI's session. |
| **Permissions** | None needed: your code runs the tool directly | `allowed_tools=[...]` **only auto-approves** tools. It does not restrict them. `tools=[]` is what removes Bash, Read, Edit and the other built-ins (check 3 asserts `--tools ""`). |
| **Turn cap** | `max_turns`, then `raise RuntimeError` (L182) | `max_turns=5`, which produces `ResultMessage.subtype == "error_max_turns"`. The CLI then exits 1, and the SDK raises `ResultError`. `run()` turns that into `Failed` (check 7). |
| **System prompt** | `SYSTEM_PROMPT` (L127) | The same string passed as `system_prompt=`. A string replaces Claude Code's default prompt. Leaving it as `None` sends `--system-prompt ""`. |
| **Ambient config** | None | Off by default **only if you ask**: `setting_sources=[]` stops `~/.claude` and this repo's `CLAUDE.md` from loading. `strict_mcp_config=True` ignores `.mcp.json`. |
| **Cost visibility** | `response.usage` on each call | `ResultMessage.total_cost_usd`, `usage`, `num_turns`, `model_usage`, reported once for the whole run. |

### What it hides

- **A process boundary.** Every `query()` starts the bundled Claude Code CLI
  (2.1.301) as a subprocess. Failures arrive as `CLINotFoundError`,
  `ProcessError`, or `ResultError`, not as HTTP errors.
- **Token overhead.** Even with our short system prompt and no built-in tools,
  the CLI adds its own framing around each request. The research note expects
  this to cost much more than the hand loop's roughly 300 tokens per call.
  That expectation is **not verified** here: there has been no live run (see
  above).
- **Retries and the exact request sent.** The hand loop shows you every
  request it makes. With the SDK you only see the messages it chooses to yield.

## Scope

One tool and one `query()` call. No streaming input, hooks, subagents,
`ClaudeSDKClient`, or sessions. Those are separate increments. Like the hand
loop, there's no limit on `**`, so an input like `9**9**9` burns CPU instead
of failing.
