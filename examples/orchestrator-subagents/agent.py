"""Orchestrator that fans out to specialist subagents, written against the
plain Anthropic Messages API - no Claude Agent SDK, no `subagents` feature.

Three Messages-API calls, each with a distinct role:
  1. Plan       - one `client.messages.parse(..., output_format=Plan)` call
                  turns the task into a short, schema-validated list of
                  {specialist, instructions} subtasks.
  2. Delegate   - one `client.messages.create(...)` call per subtask, each
                  with its own `system` prompt (the specialist persona) and
                  no shared conversation history - independent mini-agents.
  3. Synthesize - one final `client.messages.create(...)` call that combines
                  every specialist's output into one answer.

This mirrors the classic "orchestrator-workers" pattern from Anthropic's
Building Effective Agents post, but swaps the companion notebook's
hand-rolled XML/regex parsing for `client.messages.parse(output_format=...)`
- the same pydantic-schema idea `examples/typed-tool-registry/` uses for a
tool's input, applied here to a whole message's output instead.

See the research note this came from:
    research/2026-07-29-orchestrator-subagents.md

Run it live (needs a key):
    export ANTHROPIC_API_KEY=sk-ant-...
    python agent.py

Run the offline self-test (no key, no network):
    python test_agent.py
"""

from __future__ import annotations

import os

import pydantic_core
from pydantic import BaseModel, Field

# Model id lives in one constant so switching tiers is a one-line change.
# Cheapest current model; any current id works. See knowledge/anthropic-models.md.
MODEL = "claude-haiku-4-5"


# --------------------------------------------------------------------------- #
# Plan schema. `max_length=3` is a real, checkable constraint client-side
# (pydantic raises ValidationError past 3 items - see test_agent.py test 1),
# but it is NOT enforced server-side: the Anthropic SDK's schema transform
# demotes `maxItems` into a description string before the request is sent, so
# the model can still return more than 3 subtasks. `plan_task` below catches
# that ValidationError and re-raises it as RuntimeError.
# --------------------------------------------------------------------------- #


class Subtask(BaseModel):
    specialist: str
    instructions: str


class Plan(BaseModel):
    subtasks: list[Subtask] = Field(min_length=1, max_length=3)


PLAN_SYSTEM_PROMPT = (
    "You are an orchestrator that breaks a task down into at most 3 "
    "independent subtasks, each handed to a different specialist. For each "
    "subtask, name the specialist's domain (e.g. 'research', 'copywriting', "
    "'fact-checking') and give clear, self-contained instructions - the "
    "specialist will see only those instructions, not the original task or "
    "any other subtask."
)


def plan_task(client, task: str) -> Plan:
    """Turn `task` into a validated Plan via `client.messages.parse`.

    Two distinct failure modes, both raised as RuntimeError so callers only
    need to handle one exception type:

    1. The model's text doesn't validate against the `Plan` schema - most
       realistically, more than 3 subtasks, since `max_length=3` is only a
       description hint on the wire (see the module docstring above), not an
       enforced JSON-Schema constraint. `client.messages.parse` calls
       pydantic's `TypeAdapter(...).validate_json(...)` internally with no
       try/except, so this raises `pydantic_core.ValidationError` *out of*
       `client.messages.parse` itself. Caught here and re-raised as
       RuntimeError with the original error chained (`raise ... from exc`).
    2. `response.parsed_output is None` - the response had no text block at
       all (e.g. the model only returned a refusal or tool-use block). This
       is a real but different condition from (1); still raised as
       RuntimeError.
    """
    try:
        response = client.messages.parse(
            model=MODEL,
            max_tokens=512,
            system=PLAN_SYSTEM_PROMPT,
            messages=[{"role": "user", "content": task}],
            output_format=Plan,
        )
    except pydantic_core.ValidationError as exc:
        raise RuntimeError(
            f"Model's response did not validate against the Plan schema: {exc}"
        ) from exc
    if response.parsed_output is None:
        raise RuntimeError(f"Failed to parse a Plan from the model's response: {response!r}")
    return response.parsed_output


# --------------------------------------------------------------------------- #
# Delegate - each specialist call is independently instructed and
# independently invoked, with no shared conversation history.
# --------------------------------------------------------------------------- #


def run_specialist(client, subtask: Subtask) -> str:
    """Run one specialist subtask and return Claude's text answer."""
    response = client.messages.create(
        model=MODEL,
        max_tokens=512,
        system=(
            f"You are a specialist in {subtask.specialist}. Focus only on the "
            "instructions given - you have no knowledge of the broader task "
            "or any other specialist's work."
        ),
        messages=[{"role": "user", "content": subtask.instructions}],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )


# --------------------------------------------------------------------------- #
# Synthesize - one final call combines every specialist's output.
# --------------------------------------------------------------------------- #


def synthesize(client, task: str, results: list[tuple[Subtask, str]]) -> str:
    """Combine every specialist's output into one synthesized answer."""
    sections = "\n\n".join(
        f"Specialist: {subtask.specialist}\n"
        f"Instructions given: {subtask.instructions}\n"
        f"Output:\n{output}"
        for subtask, output in results
    )
    prompt = (
        f"Original task:\n{task}\n\n"
        f"Here is each specialist's output:\n\n{sections}\n\n"
        "Combine these into one clear, coherent final answer to the "
        "original task."
    )
    response = client.messages.create(
        model=MODEL,
        max_tokens=1024,
        system="You synthesize specialist outputs into one final answer.",
        messages=[{"role": "user", "content": prompt}],
    )
    return "".join(
        block.text for block in response.content if getattr(block, "type", None) == "text"
    )


# --------------------------------------------------------------------------- #
# Orchestrator - plan, then each specialist sequentially, then synthesize.
#
# Kept sequential (not ThreadPoolExecutor/asyncio.gather) for determinism and
# a simple offline test. Parallelizing the specialist calls is the natural
# next increment - see README.md.
# --------------------------------------------------------------------------- #


def run_orchestrator(client, task: str) -> str:
    """Run plan -> each specialist -> synthesize, and return the final text."""
    plan = plan_task(client, task)
    results = [(subtask, run_specialist(client, subtask)) for subtask in plan.subtasks]
    return synthesize(client, task, results)


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

    import anthropic  # imported lazily so the self-test needs no live client

    client = anthropic.Anthropic()
    task = (
        "Write a short launch announcement for a new open-source CLI tool "
        "called 'agentlab' that helps developers build and test AI agents. "
        "It should be technically accurate and appealing to developers."
    )
    print(f"> {task}\n")

    plan = plan_task(client, task)
    print("Plan:")
    for i, subtask in enumerate(plan.subtasks, start=1):
        print(f"  {i}. [{subtask.specialist}] {subtask.instructions}")

    results = []
    for subtask in plan.subtasks:
        print(f"\nRunning specialist: {subtask.specialist}...")
        output = run_specialist(client, subtask)
        print(f"  -> {output}")
        results.append((subtask, output))

    print("\nSynthesizing final answer...")
    final = synthesize(client, task, results)
    print(f"\nFinal answer:\n{final}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
