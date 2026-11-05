"""Build the user message that answers a paused turn.

Pure: no I/O, no SDK.

The docs' continuation rules this module encodes:
  1. A message answering programmatic calls holds ONLY ``tool_result`` blocks.
     A text block, even after the results, is a 400. This builder has no text
     parameter at all, so it cannot produce one, even for a direct-only turn.
  2. Every pending call is answered in that one message. Leaving a call out is
     not possible: a missing id raises before anything is built.
  3. ``tool_result.content`` is a string. For a programmatic call it becomes the
     return value of the tool function inside Claude's code.

See research/2026-11-05-programmatic-tool-calling-loop.md.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from turn import Paused

ERROR_PREFIX = "Error: "


@dataclass(frozen=True)
class ToolError:
    """A tool call that failed (raised, or ran past its deadline). ``message`` is shown to the model."""

    message: str


class MissingResultError(ValueError):
    """A pending call has no result. Building the reply would leave it unanswered."""


class UnexpectedResultError(ValueError):
    """A result was given for an id that is not pending in this turn."""


class NonTextResultError(TypeError):
    """A result is neither ``str`` nor ``ToolError``. Programmatic results must be text."""


def build_reply(paused: Paused, results: Mapping[str, str | ToolError]) -> dict:
    """The user message answering every call in ``paused``, in response order.

    A successful result is sent as-is. A ``ToolError`` becomes the string
    ``"Error: <message>"``. On a direct call it also gets ``is_error: true``
    (the standard tool-use contract). On a programmatic call it is a plain
    string, because the code receives the string and has to be able to
    inspect it.

    Raises (nothing is built on failure):
        MissingResultError: a pending call id is absent from ``results``.
        UnexpectedResultError: ``results`` has an id that is not pending.
        NonTextResultError: a result is not ``str`` / ``ToolError``.
    """
    pending = [call.id for call in paused.calls]
    missing = [tid for tid in pending if tid not in results]
    if missing:
        raise MissingResultError(f"no result for pending call(s) {missing}")
    extra = sorted(set(results) - set(pending))
    if extra:
        raise UnexpectedResultError(f"result(s) for id(s) not pending in this turn: {extra}")
    bad = [tid for tid in pending if not isinstance(results[tid], (str, ToolError))]
    if bad:
        raise NonTextResultError(f"results for {bad} are not text: "
                                 f"{[type(results[tid]).__name__ for tid in bad]}")

    return {"role": "user", "content": [_tool_result(call.id, call.is_programmatic, results[call.id])
                                        for call in paused.calls]}


def _tool_result(tool_use_id: str, programmatic: bool, result: str | ToolError) -> dict:
    if isinstance(result, str):
        return {"type": "tool_result", "tool_use_id": tool_use_id, "content": result}
    block = {"type": "tool_result", "tool_use_id": tool_use_id, "content": ERROR_PREFIX + result.message}
    if not programmatic:
        block["is_error"] = True
    return block
