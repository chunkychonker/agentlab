"""Agent loop whose one tool executes a bundled script, not an in-process function.

Demonstrates the Skills "level 3 resource: script execution" mechanism from
the research note by hand, against the raw Anthropic Messages API: when the
tool runs, only `count_words.py`'s stdout becomes the tool_result content —
its source text never enters the conversation — and the script itself never
lets an exception surface, per the "solve, don't defer" authoring contract.

This is NOT Claude Code's `${CLAUDE_SKILL_DIR}`/`allowed-tools` plumbing (that
permission-prompt-avoidance mechanism only exists inside Claude Code) and NOT
the beta Claude API Skills/code-execution container. `skills/word-counter/`
is included as a real, correctly authored Skill directory to show what a
production skill for this same script would look like; agent.py does not
parse or load SKILL.md — it wires the same script to a hand-written tool.

See the research note this came from:
    research/2026-07-30-skill-script-execution.md
Companion piece (Skill trigger mechanics, levels 1-2):
    research/2026-07-29-anatomy-of-a-skill.md

Run it live (needs a key):
    export ANTHROPIC_API_KEY=sk-ant-...
    python agent.py

Run the offline self-test (no key, no network):
    python test_agent.py
"""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

# Model id lives in one constant so switching tiers is a one-line change.
# Cheapest current model; any current id works. See knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"

# The bundled script this tool executes, resolved relative to this file (not
# the caller's cwd) — the same intent `${CLAUDE_SKILL_DIR}` serves in Claude
# Code, done in plain Python since that variable is Claude-Code-specific.
_SCRIPT_PATH = Path(__file__).parent / "skills" / "word-counter" / "scripts" / "count_words.py"


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #

def run_word_counter(path: str) -> str:
    """Execute scripts/count_words.py as a subprocess and return its stdout
    verbatim as the tool result.

    This is the load-bearing line of the whole example: `capture_output=True`
    captures the subprocess's stdout only — `count_words.py`'s ~90 lines of
    source are never read into this process, let alone sent to the model.
    The script's own exit code (0 on success, 1-3 on a documented failure) is
    deliberately not inspected here: its stdout is already a JSON object
    describing success or failure either way, which is exactly what the
    "solve, don't defer" contract buys the caller.

    Failure modes: if the subprocess itself cannot start (e.g. no Python
    interpreter at `sys.executable`), this propagates the OSError — that is
    an environment bug, not a tool-usage problem, so it is not papered over
    with a default result.
    """
    result = subprocess.run(
        [sys.executable, str(_SCRIPT_PATH), path],
        capture_output=True,
        text=True,
    )
    return result.stdout


# Tool schema exactly as the Messages API expects: name, description, input_schema.
TOOLS = [
    {
        "name": "run_word_counter",
        "description": (
            "Run the word-counter skill's script to count words, lines, and "
            "characters in a text file. This executes the script rather than "
            "reading it — always use this tool instead of counting by hand."
        ),
        "input_schema": {
            "type": "object",
            "properties": {
                "path": {
                    "type": "string",
                    "description": "Path to the text file to count.",
                }
            },
            "required": ["path"],
        },
    }
]

# name -> callable registry. One tool today; a plain dict is all the abstraction
# this increment needs.
TOOL_FUNCTIONS = {
    "run_word_counter": lambda inp: run_word_counter(inp["path"]),
}


# --------------------------------------------------------------------------- #
# The manual loop (same shape as examples/minimal-agent-loop/agent.py)
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = (
    "You are a careful assistant. Use the run_word_counter tool to count "
    "words, lines, or characters in a file rather than guessing."
)


def run_agent(client, user_message: str, *, max_turns: int = 5, verbose: bool = False) -> str:
    """Run the hand-written tool-use loop and return Claude's final text answer.

    ``client`` only needs a ``.messages.create(...)`` method returning an object
    with ``.stop_reason`` and ``.content`` (a list of blocks). That is what the
    real Anthropic client provides, and what the test's fake client mimics.

    Failure modes: raises RuntimeError if `max_turns` tool-use turns pass
    without Claude producing a final answer.
    """
    messages = [{"role": "user", "content": user_message}]

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            # Done: concatenate every text block into the final answer.
            return "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )

        # Echo the assistant's tool_use turn back verbatim, then answer each
        # tool_use block with a matching tool_result block in a single user turn.
        messages.append({"role": "assistant", "content": response.content})

        tool_results = []
        for block in response.content:
            if getattr(block, "type", None) != "tool_use":
                continue
            fn = TOOL_FUNCTIONS.get(block.name)
            if fn is None:
                result_text = f"Error: unknown tool {block.name!r}"
            else:
                result_text = fn(block.input)
            if verbose:
                print(f"  [tool] {block.name}({block.input}) -> {result_text!r}")
            tool_results.append(
                {
                    "type": "tool_result",
                    "tool_use_id": block.id,  # MUST match the tool_use id we answer.
                    "content": result_text,
                }
            )

        messages.append({"role": "user", "content": tool_results})

    raise RuntimeError(f"Agent did not finish within {max_turns} turns")


# --------------------------------------------------------------------------- #
# Live entry point
# --------------------------------------------------------------------------- #

def main() -> int:
    if not os.environ.get("ANTHROPIC_API_KEY"):
        print(
            "ANTHROPIC_API_KEY is not set - skipping the live run.\n"
            "Set it and re-run for a real call, or run 'python test_agent.py' "
            "for the offline self-test."
        )
        return 0

    import anthropic  # imported lazily so the self-test needs no dependency

    client = anthropic.Anthropic()
    sample_path = str(Path(__file__).parent / "sample.txt")
    question = f"How many words, lines, and characters are in {sample_path}?"
    print(f"> {question}")
    answer = run_agent(client, question, verbose=True)
    print(f"\nFinal answer: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
