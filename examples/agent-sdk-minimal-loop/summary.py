"""Fold an Agent SDK message stream into a typed run outcome (pure, no I/O).

The SDK yields ``AssistantMessage``/``UserMessage``/``SystemMessage``/... and
finally one ``ResultMessage``. This module reduces that stream to either a
``Completed`` or a ``Failed`` -- two types, so a caller cannot read an answer
off a run that hit ``error_max_turns``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from claude_agent_sdk import AssistantMessage, Message, ResultMessage, ToolUseBlock

SUCCESS_SUBTYPE = "success"


@dataclass(frozen=True)
class Completed:
    """The agent finished and produced a final answer."""

    text: str
    tool_calls: tuple[str, ...]
    num_turns: int
    total_cost_usd: float | None


@dataclass(frozen=True)
class Failed:
    """The run ended on an error result (``error_*`` subtype, or ``is_error``)."""

    subtype: str
    errors: tuple[str, ...]
    tool_calls: tuple[str, ...]
    num_turns: int
    total_cost_usd: float | None


Outcome = Completed | Failed


class NoResultError(RuntimeError):
    """The stream held no ``ResultMessage`` (or more than one): the run did not
    terminate in a way the SDK reports, so there is no outcome to give."""


def summarize(messages: Iterable[Message]) -> Outcome:
    """Reduce one ``query()`` message stream to its outcome.

    ``tool_calls`` lists every ``ToolUseBlock`` name across all assistant
    messages, in order (names are the wire names, e.g. ``mcp__calc__calculator``).

    Raises:
        NoResultError: zero or multiple ``ResultMessage``s in the stream, or a
            ``success`` result with no ``result`` text. Never invents an
            empty-string answer.
    """
    tool_calls: list[str] = []
    results: list[ResultMessage] = []
    for message in messages:
        if isinstance(message, AssistantMessage):
            tool_calls.extend(b.name for b in message.content if isinstance(b, ToolUseBlock))
        elif isinstance(message, ResultMessage):
            results.append(message)

    if len(results) != 1:
        raise NoResultError(f"expected exactly one ResultMessage, got {len(results)}")
    result = results[0]
    calls = tuple(tool_calls)

    if result.is_error or result.subtype != SUCCESS_SUBTYPE:
        return Failed(
            subtype=result.subtype,
            errors=tuple(result.errors or ()),
            tool_calls=calls,
            num_turns=result.num_turns,
            total_cost_usd=result.total_cost_usd,
        )
    if result.result is None:
        raise NoResultError("success result carried no result text")
    return Completed(
        text=result.result,
        tool_calls=calls,
        num_turns=result.num_turns,
        total_cost_usd=result.total_cost_usd,
    )
