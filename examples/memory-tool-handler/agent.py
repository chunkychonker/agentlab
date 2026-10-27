"""A hand-written tool loop that gives Claude the client-side memory tool.

The memory tool is declared as ``{"type": "memory_20250818", "name": "memory"}``
with no schema. Claude sends ``tool_use`` blocks named ``memory``; this loop
hands each one to ``commands.handle`` against a ``Store`` it was given and
returns the ``tool_result``. Nothing is stored by Anthropic -- whatever the
store holds is the memory.

From the research note:
    research/2026-10-27-memory-tool-handler.md

Run the offline self-test (no key, no network, no dependencies):
    python3 test_memory.py

Run it live (billed; needs a key):
    pip install -r requirements.txt
    export ANTHROPIC_API_KEY=sk-ant-...
    python3 agent.py [memory_dir]
"""

from __future__ import annotations

import os
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from commands import CreateMode, Result, handle
from dir_store import DirStore
from store import Store

# Model id lives in one constant. Cheapest current model; see knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 1024
MAX_TURNS = 12

TOOL_NAME = "memory"
# The whole declaration. No input_schema, no beta header.
MEMORY_TOOL = {"type": "memory_20250818", "name": TOOL_NAME}

# Claude's own description of `create` says "creates or overwrites", so the
# live demo overwrites rather than spending a turn on an "already exists" error.
DEMO_CREATE_MODE = CreateMode.OVERWRITE

_FINAL_STOP_REASONS = frozenset({"end_turn", "stop_sequence"})


class AgentError(RuntimeError):
    """The loop could not reach a final answer."""


def _tool_result(tool_use_id: str, result: Result) -> dict[str, Any]:
    block: dict[str, Any] = {"type": "tool_result", "tool_use_id": tool_use_id, "content": result.text}
    if result.is_error:
        block["is_error"] = True
    return block


def run_session(
    client: Any,
    store: Store,
    user_message: str,
    *,
    create_mode: CreateMode,
    max_turns: int = MAX_TURNS,
    on_tool: Callable[[dict[str, Any], Result], None] | None = None,
) -> str:
    """Run one conversation to a final answer, executing memory commands on ``store``.

    ``client`` needs only ``.messages.create(**kwargs)`` returning an object with
    ``.stop_reason`` and ``.content`` -- the real ``anthropic.Anthropic`` or a fake.
    ``on_tool`` (presentation only) sees each command and its result.

    Returns:
        The concatenated text of the final assistant turn.

    Raises:
        AgentError: ``max_turns`` exhausted while Claude still wanted tools, or
            a stop reason that is neither final nor ``tool_use`` (``max_tokens``,
            ``refusal``, ``pause_turn``, ...). Never returns a partial answer.
        OSError: the store failed (propagated from ``commands.handle``).
        Any exception the client raises (network, auth) propagates unchanged.
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=[MEMORY_TOOL],
            messages=messages,
        )
        if response.stop_reason in _FINAL_STOP_REASONS:
            return "".join(b.text for b in response.content if b.type == "text")
        if response.stop_reason != "tool_use":
            raise AgentError(f"unexpected stop_reason {response.stop_reason!r}")

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            if block.name == TOOL_NAME:
                result = handle(store, block.input, create_mode)
            else:
                result = Result(f"Error: unknown tool {block.name!r}; the only tool is {TOOL_NAME!r}.", True)
            if on_tool is not None:
                on_tool(block.input, result)
            results.append(_tool_result(block.id, result))
        messages.append({"role": "user", "content": results})

    raise AgentError(f"no final answer within {max_turns} turns")


# --------------------------------------------------------------------------- #
# Live entry point
# --------------------------------------------------------------------------- #

SESSION_1 = (
    "Please remember for future conversations: my name is Ada, my favorite "
    "color is teal, and I always want answers in metric units."
)
SESSION_2 = "What's my favorite color, and which units should you use with me?"


def _print_tool(cmd: dict[str, Any], result: Result) -> None:
    flag = " [is_error]" if result.is_error else ""
    first = result.text.splitlines()[0] if result.text else ""
    print(f"  [memory] {cmd}{flag}\n           -> {first}")


def main(argv: list[str]) -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set - skipping the live run.\n"
            "Run 'python3 test_memory.py' for the offline self-test."
        )
        return 0

    import anthropic  # lazy: the self-test needs no dependency

    memory_dir = Path(argv[1]) if len(argv) > 1 else Path(tempfile.mkdtemp(prefix="memory-demo-"))
    memory_dir.mkdir(parents=True, exist_ok=True)
    print(f"memory directory: {memory_dir}")
    client = anthropic.Anthropic()

    # Two sessions, each with a fresh client-side history and a fresh DirStore
    # over the same directory: the only thing session 2 shares with session 1
    # is what is on disk.
    for label, prompt in (("session 1", SESSION_1), ("session 2", SESSION_2)):
        print(f"\n== {label} ==\n> {prompt}")
        answer = run_session(
            client, DirStore(memory_dir), prompt, create_mode=DEMO_CREATE_MODE, on_tool=_print_tool
        )
        print(f"\n{answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
