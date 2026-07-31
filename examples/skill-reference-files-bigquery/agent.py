"""Selective loading of a skill's reference files, written by hand against the
Anthropic Messages API.

Level 3 of the Skills progressive-disclosure model splits into two kinds of
content: **code** (scripts — executed, only stdout enters context; see
``examples/skill-script-execution``) and **instructions/resources**
(reference files — *read in full*, but only the ones a given task actually
needs). This example is the read-a-file counterpart: one tool,
``read_reference``, that Claude calls with a bare filename to pull exactly
one sibling reference file into context, leaving the others on disk unread.

Because this is a hand-built tool on the raw Messages API (no sandbox),
``read_reference`` is also responsible for its own containment: a filename
that resolves outside the skill's ``reference/`` directory — via ``../``
traversal or an absolute path — is rejected before anything is opened.
Anthropic's docs describe this boundary for the sandboxed Skills container,
not for a hand-rolled tool; see ``knowledge/agent-skills.md``.

See the research note this came from:
    research/2026-07-31-skill-reference-files.md

Run it live (needs a key):
    export ANTHROPIC_API_KEY=sk-ant-...
    python agent.py

Run the offline self-test (no key, no network):
    python test_agent.py
"""

from __future__ import annotations

import os

# Model id lives in one constant so switching tiers is a one-line change.
# Cheapest current model; any current id works. See knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"

# Root the tool at the skill's own reference/ directory. A constant, not a
# parameter the model controls — the model only ever supplies a filename.
# Resolved relative to this file, not the process's current working
# directory, so the example runs the same regardless of where it's invoked
# from.
_BASE_DIR = os.path.dirname(os.path.abspath(__file__))
SKILL_DIR = os.path.join(_BASE_DIR, "skills", "bigquery-analysis")
REFERENCE_DIR = os.path.join(SKILL_DIR, "reference")


# --------------------------------------------------------------------------- #
# The tool
# --------------------------------------------------------------------------- #


def read_reference(filename: str) -> str:
    """Read one reference file's full content, if and only if it resolves to
    a real file inside REFERENCE_DIR.

    Failure modes (all returned as a plain "Error: ..." string, never
    raised — same convention as minimal-agent-loop's calculator tool, so a
    misbehaving model gets a usable tool_result instead of a crash):
      - filename resolves (via realpath) outside REFERENCE_DIR, whether by
        ``../`` traversal or an absolute path -> "Error: ... outside the
        reference directory" (checked BEFORE existence/open, so no content
        from the target path is ever read).
      - resolved path does not exist -> "Error: no such reference file: ..."
      - resolved path is a directory, not a file -> "Error: ... is a
        directory, not a file"

    On success: returns the file's full text content, verbatim.
    """
    real_reference_dir = os.path.realpath(REFERENCE_DIR)
    candidate = os.path.join(REFERENCE_DIR, filename)
    real_candidate = os.path.realpath(candidate)

    # Containment check FIRST, before touching the filesystem for existence
    # or content, so an out-of-bounds path is never opened even in part.
    if os.path.commonpath([real_candidate, real_reference_dir]) != real_reference_dir:
        return f"Error: {filename!r} resolves outside the reference directory"

    if not os.path.exists(real_candidate):
        return f"Error: no such reference file: {filename!r}"

    if os.path.isdir(real_candidate):
        return f"Error: {filename!r} is a directory, not a file"

    with open(real_candidate, "r", encoding="utf-8") as f:
        return f.read()


# Tool schema exactly as the Messages API expects: name, description, input_schema.
READ_REFERENCE_TOOL = {
    "name": "read_reference",
    "description": (
        "Read the full content of one named reference file from this skill's "
        "reference/ directory (e.g. 'finance.md'). Only read the file(s) the "
        "current question actually needs."
    ),
    "input_schema": {
        "type": "object",
        "properties": {
            "filename": {
                "type": "string",
                "description": "Bare filename inside reference/, e.g. 'finance.md'.",
            }
        },
        "required": ["filename"],
    },
}

TOOLS = [READ_REFERENCE_TOOL]

# name -> callable registry. One tool today; a plain dict is all the abstraction
# this increment needs.
TOOL_FUNCTIONS = {
    "read_reference": lambda inp: read_reference(inp["filename"]),
}


# --------------------------------------------------------------------------- #
# The manual loop
# --------------------------------------------------------------------------- #

SYSTEM_PROMPT = (
    "You are a careful analyst. Before answering a question about revenue, "
    "sales pipeline, or product usage metrics, use the read_reference tool "
    "to look up the exact definition in the relevant reference file. Only "
    "read the reference file(s) the current question actually needs."
)


def run_agent(
    client, user_message: str, *, max_turns: int = 5, verbose: bool = False
) -> tuple[str, list[str]]:
    """Run the manual tool-use loop; return (final_text, filenames_read).

    ``filenames_read`` records, in call order, every filename for which
    ``read_reference()`` actually returned file content (i.e. not an
    "Error: ..." string) — the observable proxy for "which reference files
    actually entered context," and the thing the self-test asserts
    selectivity on.

    ``client`` only needs a ``.messages.create(...)`` method returning an
    object with ``.stop_reason`` and ``.content`` (a list of blocks). That is
    what the real Anthropic client provides, and what the test's fake client
    mimics.

    Raises RuntimeError if the loop does not finish within ``max_turns``.
    """
    messages = [{"role": "user", "content": user_message}]
    filenames_read: list[str] = []

    for _ in range(max_turns):
        response = client.messages.create(
            model=MODEL,
            max_tokens=1024,
            system=SYSTEM_PROMPT,
            tools=TOOLS,
            messages=messages,
        )

        if response.stop_reason != "tool_use":
            final_text = "".join(
                block.text for block in response.content if getattr(block, "type", None) == "text"
            )
            return final_text, filenames_read

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
                if block.name == "read_reference" and not result_text.startswith("Error:"):
                    filenames_read.append(block.input["filename"])
            if verbose:
                print(f"  [tool] {block.name}({block.input}) -> {result_text[:80]!r}")
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
    question = "What's our Q3 revenue policy?"
    print(f"> {question}")
    print(
        "(best-effort live demo, not a test — triggering is a model judgment "
        "call and is not guaranteed deterministic; expect only finance.md to "
        "be read, but don't assert on it)"
    )
    answer, filenames_read = run_agent(client, question, verbose=True)
    print(f"\nReference file(s) read: {filenames_read}")
    print(f"Final answer: {answer}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
