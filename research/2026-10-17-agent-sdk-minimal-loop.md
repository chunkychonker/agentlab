# Minimal agent loop on the Claude Agent SDK

Date: 2026-10-17. Backlog item: "The minimal agent loop again, on the Claude Agent SDK". No open PRs overlapped.

## Question
What does the Claude Agent SDK (Python) own that `examples/minimal-agent-loop/agent.py` hand-writes, and what is the smallest honest port of its one-tool calculator agent?

## Findings
Sources: [Agent SDK Python reference](https://code.claude.com/docs/en/agent-sdk/python) (read 2026-10-17; old docs.claude.com URLs 301 -> platform.claude.com -> 307 -> code.claude.com), [PyPI claude-agent-sdk](https://pypi.org/project/claude-agent-sdk/) (0.2.168, released 2026-10-15).

- Package: `pip install claude-agent-sdk`; import `claude_agent_sdk`. Python >=3.10. Pre-1.0 and releases nearly daily: pin exactly (`claude-agent-sdk==0.2.168`). The Claude Code CLI is bundled in the package (no separate install); `cli_path` overrides. So the "loop" runs in a subprocess of Claude Code, not in your process.
- Entry point: `async def query(*, prompt, options=None, transport=None) -> AsyncIterator[Message]`. New session per call. `ClaudeSDKClient` is the multi-turn variant (`connect/query/receive_response/interrupt`). Use `query()` here.
- Custom tool = in-process MCP server:
  `@tool(name, description, input_schema)` on an `async def f(args: dict) -> dict` returning `{"content":[{"type":"text","text":...}]}` (add `"is_error": True` for failures; confirm in source). Schema may be `{"a": float}` shorthand, JSON Schema dict, or TypedDict. Wrap with `create_sdk_mcp_server(name, version="1.0.0", tools=[...])` and pass `ClaudeAgentOptions(mcp_servers={"calc": server}, allowed_tools=["mcp__calc__calculator"])`. Tool names become `mcp__<server>__<tool>`.
- `allowed_tools` only auto-approves; it does NOT restrict. To stop the agent using built-in Bash/Read etc. you must also restrict `tools` (e.g. `tools=[]`) or disallow. Verify the exact semantics live; docs table says `tools: list[str] | preset | None`.
- `max_turns` caps tool round trips (hand loop's `max_turns`). `setting_sources=[]` disables loading user/project filesystem settings (default loads all three, which would leak this repo's CLAUDE.md/.mcp.json into the run) - set it for a hermetic example.
- Messages: `AssistantMessage.content` holds `TextBlock` / `ToolUseBlock(id,name,input)`; final `ResultMessage` has `subtype` ("success", "error_max_turns", "error_during_execution", "error_max_budget_usd"...), `is_error`, `num_turns`, `total_cost_usd`, `result` (final text), `usage` (top-level only), `model_usage`. Single-shot `query()` may raise `ResultError`/`ProcessError`; `CLINotFoundError` if CLI missing.
- Auth: the bundled CLI uses `ANTHROPIC_API_KEY` or an existing Claude Code login; unverified which wins when both exist.
- What it hides versus the hand loop: stop_reason handling, tool_result/tool_use_id plumbing, message-history growth, permissions, retries, and a large Claude Code system prompt + built-in tool schemas (token cost much higher than ~300-token raw loop; compare `num_turns`/`total_cost_usd` to the hand loop's usage).
- Not re-derived: model ids stay in [[anthropic-models]]; pass `model="haiku"`-style alias or `claude-haiku-4-5` via `options.model`.

## Build proposal
Directory: `examples/agent-sdk-minimal-loop/` (free; `minimal-agent-loop` exists, so name differs).

Intent: port the calculator agent to `claude-agent-sdk` and document, side by side, what the SDK owns vs hides. Out of scope: streaming, hooks, subagents, ClaudeSDKClient, sessions.

Files:
- `calc_tool.py` - pure core: `calculate(expression: str) -> str` imported from/duplicated minimally from `../minimal-agent-loop/agent.py` (prefer importing via `sys.path` insert of sibling dir; if brittle, copy the AST evaluator with a comment). No SDK import.
- `agent.py` - shell: `@tool("calculator", ..., {"expression": str})` async wrapper returning the content-dict (error -> `is_error: True`), `build_options() -> ClaudeAgentOptions` (server `calc`, `allowed_tools=["mcp__calc__calculator"]`, `tools=[]` or equivalent, `max_turns=5`, `setting_sources=[]`, model haiku), `async run(prompt) -> RunSummary(text, tool_calls: list[str], num_turns, total_cost_usd, subtype)`, and `summarize(messages) -> RunSummary` as a pure fold over messages. `main()` reads nothing from env itself beyond what SDK does; asserts `ANTHROPIC_API_KEY` or fails fast.
- `test_agent.py` - offline (no key, no CLI call):
  1. calling the tool's handler directly (`.handler` attribute on `SdkMcpTool`; verify name in source) with `{"expression":"4839 * 1284"}` returns content text `6213276`... (compute in test from Python, not hardcoded twice).
  2. bad input (`__import__('os')`) returns `is_error: True`, no raise.
  3. `build_options()` has server key `calc`, allowed tool exactly `mcp__calc__calculator`, `max_turns` set.
  4. `summarize()` over hand-built `AssistantMessage(ToolUseBlock)` + `ResultMessage` fixtures yields right tool_calls/num_turns/cost; `error_max_turns` subtype is surfaced as failure, not success.
- `requirements.txt`: `claude-agent-sdk==0.2.168`.
- `README.md`: side-by-side table (loop, tool dispatch, tool_result plumbing, permissions, turn cap, history, system prompt, cost visibility) mapping lines of `minimal-agent-loop/agent.py` to SDK options; include one captured live transcript marked `<!-- transcript-check: skip — billed live-API run, captured once during this build -->` (same directive as `examples/mcp-connect-claude-code/README.md`). Any other code blocks must be offline-verifiable.

Acceptance: `python test_agent.py` passes offline with only the pinned package installed; live `python agent.py "What is 4839 * 1284?"` (billed, optional) prints answer containing the correct product and `tool_calls == ["mcp__calc__calculator"]`; exits non-zero on `error_*` subtype or missing key; README states the cost/token overhead observed vs the hand loop.

Failure modes to state: CLI subprocess missing/crash (`CLINotFoundError`/`ProcessError`), model skipping the tool (retry once, as `run_e2e.sh` does, or just report), built-in tools leaking if `tools` not restricted.

## Open questions
- Exact attribute to call a decorated tool directly (`SdkMcpTool.handler`?) - builder must read the installed source.
- Whether `tools=[]` truly removes built-ins and whether `is_error` is honored in the returned dict.
- Auth precedence between API key and a local Claude Code login.
- Actual per-run token overhead vs hand loop (measure in the live run).
