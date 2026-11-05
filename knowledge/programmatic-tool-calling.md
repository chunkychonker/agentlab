# Programmatic tool calling

Claude calls your custom tools from Python inside the code-execution container; the model is not sampled between calls. See [[tool-use-loop]] for the direct-call loop and [[tool-search]] for the other tool-count lever.

- Enable: `code_execution_20260120` tool + `allowed_callers: ["code_execution_20260120"]` on each custom tool (`["direct"]` default). Haiku 4.5 accepts the tool but does not support the feature. Source: [docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling), checked 2026-11-05.
- Pause/resume: `stop_reason: "tool_use"` + `tool_use` blocks with `caller: {type: "code_execution_20260120", tool_id: "srvtoolu_..."}`; direct ones have `caller.type == "direct"`.
- Continuation rules: reply with ONLY `tool_result` blocks (no text, else 400), all pending ids at once, pass `container` id, resend identical `tools`, string/text-only results, replay assistant turn verbatim incl. `server_tool_use`.
- Timeouts: ~270s per pending call surfaces as `TimeoutError` in the final `stderr` (still `return_code: 0`); idle container ~5 min.
- Not compatible with `strict: true`, `disable_parallel_tool_use`, forced `tool_choice`, MCP-connector tools. `allowed_callers` is guidance, not a security boundary.
- Cost: wins on fan-out/aggregate (38% input tokens on a 75-tool benchmark), loses on sequential single-call turns (~8% more on tau2-bench).
- Unverified: Python SDK 2.x typing of `caller`/`container`.
