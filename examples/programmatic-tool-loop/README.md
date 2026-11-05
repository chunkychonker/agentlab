# Programmatic tool calling, in a hand-written loop

With **programmatic tool calling**, Claude doesn't call your tools one turn at a
time. It writes Python that runs in the API's code-execution container, and
*that code* calls your tools, for example three SQL queries through
`asyncio.gather`. Each time the code calls a tool, the container pauses and the
API returns `stop_reason: "tool_use"`. The response is shaped like a normal
tool call with three differences: each `tool_use` block carries a `caller`, the
response has a container id, and a stricter set of rules applies to your
reply. This example is the **client half** of that exchange: a loop that
pauses and resumes correctly.

From the research note:
[`research/2026-11-05-programmatic-tool-calling-loop.md`](../../research/2026-11-05-programmatic-tool-calling-loop.md)
(primary source: the
[programmatic tool calling docs](https://platform.claude.com/docs/en/agents-and-tools/tool-use/programmatic-tool-calling)).
For the plain direct-call loop see [`../minimal-agent-loop/`](../minimal-agent-loop/).
For the other lever on tool count see [`../tool-search-catalog/`](../tool-search-catalog/).

## What the loop has to get right

| Rule (docs) | Where it is enforced |
|---|---|
| Tell programmatic from direct calls by `caller.type` (`code_execution_20260120` vs `direct`). An unknown caller type fails loudly. | `turn.classify` raises `UnknownCallerError` |
| Answer **every** pending call in one user message | `reply.build_reply` raises `MissingResultError`. It cannot build a partial reply. |
| That message holds **only** `tool_result` blocks. Text, even after the results, is a 400. | `build_reply` has no text parameter, so it cannot emit text |
| Pass the `container` id back, or the API rejects the continuation | `loop.run` threads the most recent id. `Paused` cannot hold a programmatic call without one. |
| Resend the identical `tools` array and replay the assistant turn unchanged, `server_tool_use` included | `loop.run` (checked deep-equal in tests and over the real SDK) |
| Results are strings. For a programmatic call the string becomes the tool function's return value in Claude's code. | `NonTextResultError`. The demo tool documents its JSON output format in its description. |
| A call pending for about 270 s is abandoned by the server. The only trace is a `TimeoutError` in the final `stderr`, with `return_code` still 0. | `execute.run_calls` enforces its own deadline (`tool_timeout_s`, default 200 s, must be < 270). `RunResult.stderr` exposes the server's message. |
| `strict: true` doesn't work with programmatic callers. The code-execution tool must be present. | `toolset.validate_tools`, run before the first request |

## Design (layers 1-3)

- **Intent:** a hand-written loop that pauses and resumes correctly on
  caller-tagged `tool_use` blocks. Out of scope: running a sandbox ourselves,
  MCP, streaming, `pause_turn`, reusing a container across separate `run`
  calls, and the Agent SDK.
- **Spec:** the 8 acceptance criteria in the research note. Each test class
  `C1`...`C8` maps to one of them. `BoundaryChecks` covers the failure modes
  named in the docstrings.
- **Interfaces:**
  - `turn.classify(reply: Reply) -> Paused | Finished`. `Reply = (stop_reason, content: list[dict], container_id)`. `Paused.calls: tuple[ToolCall, ...]`, with `.programmatic` / `.direct`.
  - `reply.build_reply(paused: Paused, results: Mapping[str, str | ToolError]) -> dict`
  - `execute.run_calls(calls, handlers: Mapping[str, Callable[[dict], str]], timeout_s: float) -> dict[str, str | ToolError]`
  - `toolset.validate_tools(tools, handler_names: frozenset[str]) -> None`
  - `loop.run(send: Send, user_prompt, tools, handlers, *, max_turns=8, tool_timeout_s=200.0, log=None) -> RunResult`, where `Send = Callable[[dict], Reply]`

| File | One job |
|------|---------|
| `turn.py` | Reads one response into `Paused` (calls to run) or `Finished`. Pure. |
| `reply.py` | Builds the `tool_result`-only user message. Pure. |
| `execute.py` | Runs one turn's handlers concurrently under a shared deadline. |
| `toolset.py` | Checks the `tools` array against the documented constraints. Pure. |
| `loop.py` | Request sequencing: container threading, replay, turn limit. |
| `demo_tools.py` | The one demo tool: read-only SQLite over a synthetic sales table. |
| `main.py` | Live entry point: reads the key, holds `MODEL`, provides the SDK adapter `sdk_send`. |
| `fixtures/*.json` | Docs-shaped response bodies: paused (3 calls), mixed direct + programmatic, final. Recorded shapes, not captured traffic. |
| `test_programmatic_loop.py` | Offline self-test, stdlib only. |
| `sdk_contract_check.py` | The loop through the real `anthropic` SDK over mocked HTTP. Needs the SDK, no key. |

## Run the self-test (no API key, no network, stdlib only)

```bash
cd examples/programmatic-tool-loop
python3 -m unittest -v
```

Output from this build (Python 3.13.1, test-id suffixes trimmed):

```
test_bad_stop_and_shapes_raise ... ok
test_invalid_toolsets_fail_before_any_request ... ok
test_programmatic_call_without_container_is_refused ... ok
test_timeout_must_stay_below_the_server_limit ... ok
test_missing_caller_is_direct ... ok
test_paused_fixture_has_three_programmatic_calls_and_the_container ... ok
test_partial_extra_or_non_text_results_build_nothing ... ok
test_three_results_in_call_order_and_nothing_else ... ok
test_errors_are_error_strings_with_is_error_only_on_the_direct_call ... ok
test_mixed_turn_answers_both_with_tool_results_only ... ok
test_container_threaded_tools_resent_assistant_turn_replayed ... ok
test_the_most_recent_container_id_is_passed_on ... ok
test_raising_handler_becomes_error_string ... ok
test_slow_handler_times_out_as_error_string_and_loop_completes ... ok
test_unknown_tool_name_raises_before_any_handler_runs ... ok
test_write_attempt_is_refused_by_the_read_only_tool ... ok
test_unknown_caller_type_raises ... ok
test_final_turn_gives_stdout_and_text ... ok
test_max_turns_raises ... ok
test_server_side_timeout_surfaces_only_in_stderr ... ok
test_direct_call_to_code_only_tool_is_still_dispatched ... ok

----------------------------------------------------------------------
Ran 21 tests in 0.131s

OK
```

The loop tests run the **real** demo handler: the SQL in the fixture's
`tool_use` blocks actually executes against the in-memory table. A fake
`Send` records every request. During the build, 7 deliberate bugs were
injected, and each one made the suite fail:

- dropping the `container`
- appending a text block after the results
- treating an unknown caller as direct
- waiting for an abandoned handler
- dropping `server_tool_use` on replay
- setting `is_error` on programmatic results
- keeping the first container id instead of the latest. This one survived the
  first draft of the suite; `test_the_most_recent_container_id_is_passed_on`
  was added to catch it.

## Check the SDK boundary (needs the SDK, still no key)

```bash
cd examples/programmatic-tool-loop
pip install -r requirements.txt
python3 sdk_contract_check.py
```

Output from this build, against `anthropic==2.5.0`:

```
ok  SDK 2.5.0 sends anthropic-version 2026-10-21
ok  allowed_callers + code_execution_20260120 reach the wire unchanged, on both requests
ok  caller-tagged tool_use + server_tool_use round-trip through model_dump to the exact server JSON
ok  request 2: container passed as a plain string; reply is 3 tool_results only, in call order
ok  code_execution_tool_result parsed: RunResult carries stdout and the final text
ok  caller {"type": "direct"} also round-trips; mixed turn counted 1 direct + 1 programmatic

All 6 SDK contract checks passed.
```

This answers the research note's open question: SDK 2.5.0 types `caller` on
`ToolUseBlock`, `container` on `Message`, and accepts `container=` as a plain
string on the non-beta `messages.create`. No beta header is needed. The
round trip depends on `exclude_none=True` in `sdk_send`. With that flag
removed, the third check fails, because the SDK echoes `"caller": null` back
on `server_tool_use`.

## Run it live (needs a key)

```bash
cd examples/programmatic-tool-loop
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
python3 main.py      # "one separate query per region ... which missed target?"
```

It prints each tool call along with its caller (`via code srvtoolu_...` or
`via direct`), the code's stdout and stderr, the counts, and the answer.
Without `ANTHROPIC_API_KEY` it prints a skip note and exits 0. That path was
run for this build.

**Not run for this build:** no key was available, so every API behavior here
comes from the docs as encoded in the fixtures. Not observed live: the 400 on
text-after-results, the 400 on a missing `container`, the 270 s server
timeout text, and whether Claude actually uses `asyncio.gather` for this
prompt.

## Things to know

- **Cost cuts both ways.** Programmatic tool results don't count toward input
  tokens, and the docs report about 38% fewer input tokens on a 75-tool
  fan-out benchmark. On tau2-bench, where turns are sequential with one call
  each, they report about **8% more** cost. Use it for fan-out and aggregate
  work, not for one call per turn. Pricing is the code-execution tool's.
- **`allowed_callers` is not a security boundary.** It is guidance to the
  model. The demo tool is code-only (`["code_execution_20260120"]`), but
  `C8` feeds the loop a `direct` call to it and the loop dispatches it,
  because a client must handle any `tool_use` for any tool it defines.
  Enforce permissions inside the tool. `query_database` uses a `query_only`
  SQLite connection, so writes fail (`test_write_attempt_is_refused...`).
- **Tool results are input to code.** The string you return gets parsed by
  model-written Python. Treat external data in it as untrusted.
- **Model.** `MODEL = "claude-sonnet-5-5"`. The docs list Sonnet 5.x as
  supported, and the id is in SDK 2.5.0's model list. It was not checked
  against the `claude-api` skill, which is not installed here. The lab's
  usual cheap default, `claude-haiku-4-5`, accepts the tool version but
  **does not** support programmatic calling.

### Decisions and assumptions

- **Criterion 3 as written vs as built.** The proposal said a mixed turn
  "produces tool_results only for programmatic". Leaving the direct call
  unanswered would be a 400, since every `tool_use` needs a `tool_result`. So
  a mixed turn answers **both**, in response order, with tool_result blocks
  only. The test asserts that no text is ever emitted.
- **Error results.** Every failure becomes `"Error: <Type>: <message>"`. A
  direct call's error also gets `is_error: true`. A programmatic one does
  not, because the code receives the string. Whether the API would accept
  `is_error` on a programmatic result is untested. Leaving it off avoids the
  question.
- **Handler timeouts abandon threads.** Python cannot kill a thread, so a
  handler that times out runs to completion in the background and its result
  is discarded. Give tools with side effects their own timeouts.
- **Strict failure posture.** A `code_execution_tool_result_error` raises
  `CodeExecutionFailedError`, even if the model recovers in the same turn.
  An `encrypted_code_execution_result`, which the API produces only when code
  calls web search, raises `ProtocolError`. So do `pause_turn`, `max_tokens`,
  and `refusal`, as `UnexpectedStopError`. An expired container shows up as
  the API's 400 and is propagated, not retried.
