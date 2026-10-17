"""Offline self-test for the Agent SDK port. No API key, no CLI subprocess, no network.

Run:
    python test_agent.py

Needs only the pinned ``claude-agent-sdk`` installed. Covers, against the
acceptance criteria in research/2026-10-17-agent-sdk-minimal-loop.md:
  1. The tool handler, called directly: right product; forbidden input becomes
     ``is_error`` and never executes.
  2. Options: the allowed tool name matches the server/tool wiring, and the CLI
     command the SDK would run is hermetic (no built-ins, no settings files).
  3. ``summarize``: success, ``error_max_turns``, API error, and a missing result.
  4. ``run()`` end to end through the SDK's real ``query()`` with a scripted
     fake CLI transport: the SDK routes ``tools/call`` into our in-process
     server and the answer carries the tool's output.
  5. ``main`` refuses the billed run without a key.
"""

from __future__ import annotations

import contextlib
import dataclasses
import io
import os
import tempfile

import anyio
from claude_agent_sdk import AssistantMessage, ResultMessage, TextBlock, ToolUseBlock
from claude_agent_sdk._internal.transport.subprocess_cli import SubprocessCLITransport

import agent
from calc_tool import CalcError, calculate
from fake_cli import ScriptedCLI
from summary import Completed, Failed, NoResultError, summarize

PRODUCT = 4839 * 1284  # computed here, never hardcoded twice


def _call_handler(args: dict) -> dict:
    return anyio.run(agent.calculator.handler, args)


def _result(**overrides) -> ResultMessage:
    fields = dict(
        subtype="success", duration_ms=1, duration_api_ms=1, is_error=False,
        num_turns=2, session_id="s", total_cost_usd=0.0042, result="It is big.",
    )
    fields.update(overrides)
    return ResultMessage(**fields)


def _tool_use(name: str = agent.TOOL_WIRE_NAME) -> AssistantMessage:
    return AssistantMessage(
        content=[ToolUseBlock(id="toolu_1", name=name, input={"expression": "1+1"})],
        model="m",
    )


def _fake(calls: list[dict], final_subtype: str = "success") -> ScriptedCLI:
    return ScriptedCLI(agent.SERVER_NAME, agent.TOOL_NAME, agent.TOOL_WIRE_NAME, calls, final_subtype)


# --------------------------------------------------------------------------- #
# 1. The tool
# --------------------------------------------------------------------------- #

def test_handler_returns_product() -> None:
    out = _call_handler({"expression": "4839 * 1284"})
    assert out == {"content": [{"type": "text", "text": str(PRODUCT)}]}, out
    assert calculate("10 / 4") == 2.5 and calculate("6 / 3") == 2
    print("ok  tool handler returns the correct product with no is_error")


def test_handler_flags_bad_input_without_executing() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        marker = os.path.join(tmp, "pwned")
        attack = f"__import__('os').system('touch {marker}')"
        for bad in [attack, "x + 1", "1 +", "1 / 0"]:
            out = _call_handler({"expression": bad})
            assert out.get("is_error") is True, (bad, out)
            assert out["content"][0]["text"], (bad, out)
        assert not os.path.exists(marker), "forbidden input was executed"
    try:
        calculate("__import__('os')")
    except CalcError:
        pass
    else:
        raise AssertionError("calculate must raise CalcError, not return a sentinel")
    print("ok  bad input -> is_error result, nothing executed, core raises CalcError")


# --------------------------------------------------------------------------- #
# 2. Options
# --------------------------------------------------------------------------- #

def test_options_are_hermetic() -> None:
    opts = agent.build_options()
    server = opts.mcp_servers[agent.SERVER_NAME]
    assert list(opts.mcp_servers) == [agent.SERVER_NAME]
    assert server["name"] == agent.SERVER_NAME
    assert opts.allowed_tools == [f"mcp__{server['name']}__{agent.calculator.name}"]
    assert opts.max_turns == agent.MAX_TURNS

    # The seam that matters: the argv the SDK hands the CLI. Private API of the
    # pinned SDK; cli_path is set only so the command can be built offline.
    opts = dataclasses.replace(opts, cli_path="/nonexistent/claude")
    cmd = SubprocessCLITransport(prompt="x", options=opts)._build_command()
    def flag(name: str) -> str:
        return cmd[cmd.index(name) + 1]
    assert flag("--tools") == "", "built-in tools not disabled"
    assert flag("--allowedTools") == agent.TOOL_WIRE_NAME
    assert flag("--max-turns") == str(agent.MAX_TURNS)
    assert flag("--model") == agent.MODEL
    assert flag("--system-prompt") == agent.SYSTEM_PROMPT
    assert "--setting-sources=" in cmd, "filesystem settings would load"
    assert "--strict-mcp-config" in cmd
    print("ok  options wire mcp__calc__calculator and build a hermetic CLI command")


# --------------------------------------------------------------------------- #
# 3. summarize
# --------------------------------------------------------------------------- #

def test_summarize_outcomes() -> None:
    done = summarize([_tool_use(), AssistantMessage(content=[TextBlock("ok")], model="m"), _result()])
    assert done == Completed("It is big.", (agent.TOOL_WIRE_NAME,), 2, 0.0042), done

    capped = summarize([_tool_use(), _tool_use(), _result(
        subtype="error_max_turns", is_error=True, result=None, num_turns=5, errors=["cap"])])
    assert isinstance(capped, Failed) and capped.subtype == "error_max_turns", capped
    assert capped.tool_calls == (agent.TOOL_WIRE_NAME,) * 2 and capped.num_turns == 5

    api_err = summarize([_result(is_error=True, result="API Error: 529")])
    assert isinstance(api_err, Failed) and api_err.subtype == "success", api_err

    for bad in ([_tool_use()], [_result(), _result()], [_result(result=None)]):
        try:
            summarize(bad)
        except NoResultError:
            continue
        raise AssertionError(f"summarize must raise on {bad!r}")
    print("ok  summarize: success, error_max_turns and API error are distinct; no result raises")


# --------------------------------------------------------------------------- #
# 4. run() through the SDK's real query(), with a scripted CLI
# --------------------------------------------------------------------------- #

def test_run_end_to_end_offline() -> None:
    cli = _fake([{"expression": "4839 * 1284"}])
    outcome = anyio.run(agent.run, "What is 4839 * 1284?", cli)
    assert isinstance(outcome, Completed), outcome
    assert str(PRODUCT) in outcome.text, outcome.text
    assert outcome.tool_calls == (agent.TOOL_WIRE_NAME,)
    assert cli.tool_results[0]["result"]["isError"] is False
    print("ok  run(): SDK dispatches tools/call to the in-process tool; answer has the product")


def test_sdk_maps_errors_to_is_error() -> None:
    cli = _fake([{"expression": "__import__('os')"}, {"wrong_key": 1}])
    anyio.run(agent.run, "break it", cli)
    ours, schema = (r["result"] for r in cli.tool_results)
    assert ours["isError"] is True, ours  # our is_error -> MCP isError
    assert schema["isError"] is True and "validation" in schema["content"][0]["text"].lower(), schema
    print("ok  SDK forwards is_error, and rejects schema-invalid args before our handler")


def test_run_surfaces_max_turns_as_failed() -> None:
    cli = _fake([{"expression": "1+1"}], final_subtype="error_max_turns")
    outcome = anyio.run(agent.run, "loop", cli)
    assert isinstance(outcome, Failed) and outcome.subtype == "error_max_turns", outcome
    print("ok  run(): error_max_turns + CLI exit 1 becomes Failed, not an answer")


# --------------------------------------------------------------------------- #
# 5. Entry point
# --------------------------------------------------------------------------- #

def test_main_refuses_without_key() -> None:
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = agent.main(["What is 2+2?"], environ={})
    assert code == agent.EXIT_NO_KEY != 0, code
    assert agent.API_KEY_ENV in err.getvalue()
    print("ok  main exits non-zero with a clear message when ANTHROPIC_API_KEY is unset")


# --------------------------------------------------------------------------- #

def main() -> int:
    tests = [
        test_handler_returns_product,
        test_handler_flags_bad_input_without_executing,
        test_options_are_hermetic,
        test_summarize_outcomes,
        test_run_end_to_end_offline,
        test_sdk_maps_errors_to_is_error,
        test_run_surfaces_max_turns_as_failed,
        test_main_refuses_without_key,
    ]
    for t in tests:
        t()
    print(f"\nAll {len(tests)} self-tests passed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
