"""The minimal one-tool calculator agent, on the Claude Agent SDK.

Same agent as ``examples/minimal-agent-loop/agent.py``, but the loop, tool
dispatch, tool_result plumbing, and turn cap are owned by the SDK (which runs
the bundled Claude Code CLI as a subprocess). This file only declares the tool,
the options, and the entry point.

See the research note this came from:
    research/2026-10-17-agent-sdk-minimal-loop.md

Offline self-test (no key, no network):  python test_agent.py
Live run (billed):                        python agent.py "What is 4839 * 1284?"
"""

from __future__ import annotations

import os
import sys
from collections.abc import Mapping, Sequence
from typing import Any

import anyio
from claude_agent_sdk import (
    ClaudeAgentOptions,
    Message,
    ResultError,
    ResultMessage,
    Transport,
    create_sdk_mcp_server,
    query,
    tool,
)

from calc_tool import CalcError, calculate
from summary import Completed, Failed, Outcome, summarize

# Model id in one constant; see knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"
MAX_TURNS = 5
SERVER_NAME = "calc"
TOOL_NAME = "calculator"
# The SDK exposes in-process MCP tools to the model as mcp__<server>__<tool>.
TOOL_WIRE_NAME = f"mcp__{SERVER_NAME}__{TOOL_NAME}"
API_KEY_ENV = "ANTHROPIC_API_KEY"
DEFAULT_PROMPT = "What is 4839 * 1284, and is that more than five million?"

# Same system prompt as the hand loop, so the comparison is like for like.
SYSTEM_PROMPT = (
    "You are a careful assistant. Use the calculator tool for any arithmetic "
    "rather than doing it in your head."
)

EXIT_OK = 0
EXIT_RUN_FAILED = 1
EXIT_NO_KEY = 2
EXIT_TOOL_SKIPPED = 3


@tool(
    TOOL_NAME,
    "Evaluate an arithmetic expression and return the numeric result. Supports "
    "+ - * / // % ** and parentheses over numbers. Use this for any arithmetic "
    "instead of computing it yourself.",
    {"expression": str},
)
async def calculator(args: dict[str, Any]) -> dict[str, Any]:
    """MCP tool handler: evaluate ``args["expression"]``.

    The SDK validates ``args`` against the schema before calling this, so
    ``expression`` is a str here. A ``CalcError`` becomes an ``is_error``
    result the model can read; it is never raised into the SDK.
    """
    try:
        value = calculate(args["expression"])
    except CalcError as exc:
        return {"content": [{"type": "text", "text": str(exc)}], "is_error": True}
    return {"content": [{"type": "text", "text": str(value)}]}


def build_options() -> ClaudeAgentOptions:
    """Hermetic options: only our tool, no built-ins, no filesystem settings."""
    server = create_sdk_mcp_server(SERVER_NAME, version="1.0.0", tools=[calculator])
    return ClaudeAgentOptions(
        model=MODEL,
        system_prompt=SYSTEM_PROMPT,
        mcp_servers={SERVER_NAME: server},
        strict_mcp_config=True,  # ignore any .mcp.json / user MCP servers
        tools=[],  # remove Bash/Read/Edit/...: allowed_tools alone does NOT restrict
        allowed_tools=[TOOL_WIRE_NAME],  # auto-approve our tool (no permission prompt)
        setting_sources=[],  # don't load ~/.claude or this repo's CLAUDE.md
        max_turns=MAX_TURNS,
    )


async def run(prompt: str, transport: Transport | None = None) -> Outcome:
    """Run one ``query()`` and summarize it.

    ``transport`` is for the offline test (a scripted fake CLI); ``None`` means
    the real bundled CLI subprocess.

    Raises:
        CLINotFoundError: the Claude Code CLI could not be found.
        ProcessError: the CLI crashed without reporting a result.
        NoResultError: the stream ended without exactly one ResultMessage.
        ResultError is NOT propagated when the CLI already yielded its error
        ResultMessage: that run becomes a ``Failed`` outcome instead.
    """
    messages: list[Message] = []
    try:
        async for message in query(prompt=prompt, options=build_options(), transport=transport):
            messages.append(message)
    except ResultError:
        # The CLI reports an error result, then exits non-zero; the SDK turns
        # that exit into ResultError. If we already hold the ResultMessage it
        # is fully described by summarize(); otherwise there is nothing to
        # summarize and the error must surface.
        if not any(isinstance(m, ResultMessage) for m in messages):
            raise
    return summarize(messages)


def _report(outcome: Outcome) -> int:
    """Print the outcome and map it to an exit code."""
    print(f"tool_calls: {list(outcome.tool_calls)}")
    print(f"num_turns: {outcome.num_turns}")
    print(f"total_cost_usd: {outcome.total_cost_usd}")
    if isinstance(outcome, Failed):
        print(f"FAILED: subtype={outcome.subtype} errors={list(outcome.errors)}", file=sys.stderr)
        return EXIT_RUN_FAILED
    assert isinstance(outcome, Completed)
    print(f"\nFinal answer: {outcome.text}")
    if TOOL_WIRE_NAME not in outcome.tool_calls:
        print(f"model answered without calling {TOOL_WIRE_NAME}", file=sys.stderr)
        return EXIT_TOOL_SKIPPED
    return EXIT_OK


def main(argv: Sequence[str], environ: Mapping[str, str]) -> int:
    """Entry point. Requires ``ANTHROPIC_API_KEY`` so the run is billed to an
    explicit key rather than whatever Claude Code login is on the machine.

    Exit codes: 0 ok, 1 error result, 2 no key, 3 model skipped the tool.
    CLI-level failures (``CLINotFoundError``, ``ProcessError``) propagate.
    """
    if not environ.get(API_KEY_ENV):
        print(
            f"{API_KEY_ENV} is not set; refusing the live (billed) run.\n"
            "Run 'python test_agent.py' for the offline self-test.",
            file=sys.stderr,
        )
        return EXIT_NO_KEY
    prompt = " ".join(argv) if argv else DEFAULT_PROMPT
    print(f"> {prompt}")
    return _report(anyio.run(run, prompt))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:], os.environ))
