"""Hand-written tool loop that serves Claude's memory tool from a local Store.

See the research note this came from:
    research/2026-10-28-memory-tool-handler.md

Run it live (needs a key; two short billed sessions on Haiku):
    export ANTHROPIC_API_KEY=sk-ant-...
    python agent.py [MEMORY_DIR]

Run the offline self-test (no key, no network):
    python test_memory.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable

from commands import execute
from store import DirStore, Store

# Model id lives in one constant. Cheapest current model; see
# knowledge/anthropic-models.md. The memory tool works on all Claude 4+ models.
MODEL = "claude-haiku-4-5"
MAX_TOKENS = 2048

# The entire declaration: no input schema, no beta header. `name` must be "memory".
MEMORY_TOOL = {"type": "memory_20250818", "name": "memory"}

SESSION_1 = (
    "I'm starting a Python web-scraping project. My standing preferences: use "
    "httpx (never requests), and target Python 3.12. Please remember these for "
    "future conversations."
)
SESSION_2 = "Quick question: which HTTP library and Python version should my scraper use?"


def _tool_result(block: Any, store: Store) -> dict[str, Any]:
    if block.name != MEMORY_TOOL["name"]:
        return {
            "type": "tool_result",
            "tool_use_id": block.id,
            "content": f"Error: unknown tool {block.name!r}",
            "is_error": True,
        }
    result = execute(store, block.input)
    out: dict[str, Any] = {"type": "tool_result", "tool_use_id": block.id, "content": result.text}
    if result.is_error:
        out["is_error"] = True
    return out


def run_session(
    client: Any,
    store: Store,
    user_message: str,
    *,
    max_turns: int = 12,
    log: Callable[[str], None] | None = None,
) -> str:
    """Run one conversation to completion and return Claude's final text.

    ``client`` needs only ``.messages.create(**kwargs)`` returning an object
    with ``.stop_reason`` and ``.content``; the real ``anthropic.Anthropic``
    client and the test's fake both qualify.

    Raises RuntimeError if Claude is still calling tools after ``max_turns``
    API calls. API errors and store host faults (OSError) propagate; memory
    writes made before the failure stay on disk (that is the point of memory).
    """
    messages: list[dict[str, Any]] = [{"role": "user", "content": user_message}]
    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=MAX_TOKENS,
            tools=[MEMORY_TOOL],
            messages=messages,
        )
        if response.stop_reason != "tool_use":
            return "".join(b.text for b in response.content if b.type == "text")

        messages.append({"role": "assistant", "content": response.content})
        results = []
        for block in response.content:
            if block.type != "tool_use":
                continue
            res = _tool_result(block, store)
            if log:
                flag = " [is_error]" if res.get("is_error") else ""
                log(f"  [memory] {block.input} ->{flag}\n    " + res["content"].replace("\n", "\n    "))
            results.append(res)
        messages.append({"role": "user", "content": results})
    raise RuntimeError(f"Session did not finish within {max_turns} turns")


def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set - skipping the live run.\n"
            "Set it and re-run for a real call, or run 'python test_memory.py' "
            "for the offline self-test."
        )
        return 0

    import anthropic  # lazy: the self-test needs no dependency

    memory_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(tempfile.mkdtemp(prefix="memories-"))
    print(f"Memory directory: {memory_dir}")
    client = anthropic.Anthropic()

    for label, prompt in (("Session 1", SESSION_1), ("Session 2", SESSION_2)):
        # A fresh store object on the same directory: nothing carries over
        # between sessions except what is on disk.
        store = DirStore(memory_dir)
        print(f"\n=== {label} ===\n> {prompt}")
        answer = run_session(client, store, prompt, log=print)
        print(f"\n{answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
