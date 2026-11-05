# Programmatic tool calling through a hand-written loop (2026-11-05)

Backlog: no plain `[ ]` feature item remained (only auto-filed health findings, which
`examples/backlog-triage/` already classifies), so the researcher filed this topic
(same precedent as 2026-10-29 and 2026-11-03). Open PR check: only #62 (pipeline) is open; no overlap.

## Question
What does the client have to do differently in a hand-written tool loop when Claude calls
your tools *from code* (`allowed_callers` + `code_execution_20260120`), and what is the smallest
offline-testable loop that handles it correctly?

## Findings
Primary source: [Programmatic tool calling docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling) (fetched 2026-11-05; status stable, not ZDR-eligible; API version header `2026-10-21` in the examples).

- **Enable**: put `{"type":"code_execution_20260120","name":"code_execution"}` in `tools`, and add `"allowed_callers": ["code_execution_20260120"]` to each custom tool. Values: `["direct"]` (default), `["code_execution_20260120"]`, or both (docs recommend picking one). `code_execution_20260521` is interchangeable in `allowed_callers`, but response blocks always tag the caller `code_execution_20260120`. No beta header shown.
- **Models**: Sonnet 5.x, Opus 4.5+/5.x, Sonnet 4.5/4.6 etc. Haiku 4.5 accepts the tool version but does NOT support programmatic calling; check `capabilities.code_execution.supported` in the Models API.
- **Mechanics**: Claude writes Python; each allowed tool is an async function taking one dict and returning a string (the text of your `tool_result`). Code execution *pauses* whenever a tool is called and the API returns `stop_reason: "tool_use"`.
- **Response shape when paused**: content has a `server_tool_use` (`name: code_execution`, `input.code`, id `srvtoolu_...`) plus one or more `tool_use` blocks carrying `caller: {"type":"code_execution_20260120","tool_id":"srvtoolu_..."}`. Direct calls carry `caller: {"type":"direct"}`. Top-level `container: {id, expires_at}` is returned.
- **Continuation rules (the loop-relevant gotchas)**:
  1. The user message answering programmatic calls must contain ONLY `tool_result` blocks, no text, even after results (400 otherwise). Direct-call results may still be followed by text.
  2. Answer every pending programmatic call in one user message.
  3. Pass `container=<id>` back; the API rejects a continuation with pending programmatic calls and no container id.
  4. Resend the same `tools` array (code execution tool must be present to resume).
  5. `tool_result.content` must be a string or `text` blocks; images/documents rejected.
  6. Replay the assistant turn unmodified, including the `server_tool_use` block.
- **Timeouts**: pending call times out after ~4 min (`TimeoutError: ... no response after 270s` appears in `stderr` of the final `code_execution_tool_result`, with `return_code: 0`); idle containers reclaimed ~5 min; hard 30-day limit. So a slow tool is reported to the model as a stderr string, not as an API error.
- **Completion**: final response carries `code_execution_tool_result` (`stdout`, `stderr`, `return_code`) and then text; `stop_reason` `end_turn`. Distinguish pause from completion by `stop_reason` AND the `caller` on `tool_use` blocks.
- **Incompatibilities**: `strict: true` tools, `disable_parallel_tool_use`, forcing via `tool_choice` (400 if the named tool's `allowed_callers` lacks `"direct"`), MCP-connector tools, computer/browser toolsets, recursive `$ref` schemas (400 `Circular $ref detected`).
- **Security note from docs**: `allowed_callers` is guidance, not a boundary; the client must still handle a direct `tool_use` for any tool it defines. Tool results are strings fed into code, so validate/escape external data.
- **Cost**: same pricing as code execution; programmatic tool results do not count toward token usage. Docs report ~38% input-token reduction on a 75-tool benchmark, but ~8% MORE cost on tau2-bench (sequential single-call turns). Fan-out/aggregate workloads win; one-call-per-turn workloads do not.
- Not verified: whether the installed Python SDK (anthropic 2.x) types `caller` on `ToolUseBlock` / accepts `container=` as a plain string on non-beta `messages.create`. Builder should check with `inspect`/grep of the installed package (see [[anthropic-api-2026-10-21]]), and pass dicts back verbatim if typing is lacking. HN reception was not checked (hn-search MCP failed to connect this cycle).

## Build proposal
**Directory**: `examples/programmatic-tool-loop/` (not taken; sibling `tool-search-catalog/` covers deferral, `minimal-agent-loop/` covers direct calls).

**Intent**: show the client-side half of programmatic tool calling: a hand-written loop that correctly pauses/resumes on caller-tagged `tool_use` blocks. Out of scope: running the code-execution sandbox ourselves, MCP, streaming, container persistence across turns, the Agent SDK.

**Modules** (one sentence each, no "and"):
- `turn.py` (pure): `classify(content: list[dict]) -> Turn` where `Turn` is a frozen dataclass `{programmatic: tuple[ToolCall,...], direct: tuple[ToolCall,...], container_id: str|None, done: bool}`. Reads `caller.type`; any `caller.type` not `direct` or starting `code_execution_` raises `UnknownCallerError` (fail loud). Missing `caller` is treated as direct (older responses).
- `reply.py` (pure): `build_reply(turn, results: dict[str,str|ToolError]) -> dict` builds the user message. Invariant: if `turn.programmatic` is non-empty the message holds only `tool_result` blocks, in call order, one per pending id; errors become `is_error`-free plain strings prefixed `Error:` (docs: code sees the string). Raises `MissingResultError` if any pending id lacks a result; `NonTextResultError` if a result is not str.
- `loop.py` (shell): `run(client, user_prompt, tools, handlers, *, max_turns=8, tool_timeout_s=200.0) -> RunResult`. Threads `container` id from each response into the next request, resends identical `tools`, runs programmatic handlers (concurrently via ThreadPoolExecutor, since code may `asyncio.gather`), enforces `tool_timeout_s` per handler (< the ~270s server limit) by returning an `Error: timed out` string, raises `MaxTurnsError`. `RunResult` = `{final_text, stdout, turns, programmatic_calls, direct_calls}`.
- `demo_tools.py`: `query_database` over a tiny in-memory sales table (JSON-string output, documented in the description) and `tools()` returning the definitions with `allowed_callers`.
- `main.py`: reads `ANTHROPIC_API_KEY` and `MODEL` constant (`claude-sonnet-5-5`; confirm via claude-api skill / [[anthropic-models]]) at the edge; live run is optional.
- `test_*.py` + recorded fixtures (`fixtures/*.json`) replaying the doc's Step 2 / Step 5 shapes through a fake client (inject-a-fake-client trick as in `minimal-agent-loop`).

**Acceptance criteria (offline, no key, `python3 -m unittest`)**:
1. Fixture "paused with 3 programmatic calls": `classify` returns 3 programmatic, 0 direct, the container id, `done=False`.
2. `build_reply` for that turn yields one user message whose content has exactly 3 `tool_result` blocks and no other types, matching `tool_use_id`s in order.
3. A mixed turn (1 direct + 1 programmatic) produces tool_results only for programmatic when any are pending and the direct result is placed ahead of any text; test asserts no `text` block is ever emitted next to a pending programmatic result.
4. The fake client records each request: request 2 has `container == "container_xyz789"`, `tools` equal to request 1's, and the assistant turn replayed verbatim including `server_tool_use`.
5. A handler that sleeps past `tool_timeout_s` yields an `Error: timed out...` result string and the loop still completes; a handler raising is also converted to an `Error:` string (policy cited from [[tool-failure-taxonomy]]); an unknown tool name raises.
6. A `tool_use` with unknown `caller.type` raises `UnknownCallerError`.
7. Final fixture (`code_execution_tool_result` then text, `end_turn`) gives `RunResult.stdout` and `final_text`; exceeding `max_turns` raises `MaxTurnsError`.
8. README states the doc-reported cost caveat (sequential single-call workloads cost more), links this note, and notes that `allowed_callers` is not a security boundary (a test feeds a `direct` caller for a code-only tool and asserts it is still dispatched).

**Failure modes to state in docstrings**: partial results (missing id), non-text result, expired container (request 400s: propagate, do not retry), `TimeoutError` appearing only in final stderr.

## Open questions
- SDK typing of `caller`/`container` in anthropic 2.x non-beta `messages.create` (verify by inspection before coding).
- Whether `code_execution_20260120` needs a beta header on older SDK versions; docs show none with API version 2026-10-21.
- Exact docs-vs-reality of text-after-results rejection (error type/message) was not exercised live; fixture-based only unless a key is available.
