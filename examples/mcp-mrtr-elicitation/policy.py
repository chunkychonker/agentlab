"""Decides whether a human's elicitation answer permits a destructive action.

Pure: no I/O, no SDK session, no clock. The server's tools call these after the
framework has injected the elicitation outcome, and do the mutation themselves.

The outcome is the SDK's `ElicitationResult[T]` union, so the three ways a human
can respond are distinct types rather than flags to remember:

  AcceptedElicitation(data=T)   the form was submitted (data may still say "no")
  DeclinedElicitation           the user explicitly refused to answer
  CancelledElicitation          the user dismissed the prompt

`Decision` is an enum, so a caller cannot invent a fifth outcome.
"""

from enum import Enum

from mcp.server.mcpserver import (
    AcceptedElicitation,
    CancelledElicitation,
    DeclinedElicitation,
    ElicitationResult,
)
from pydantic import BaseModel


class Confirm(BaseModel):
    """The form shown to the human: one yes/no checkbox."""

    ok: bool


class TypedCount(BaseModel):
    """Second-step form for a bulk purge: the human types how many notes will go."""

    count: int


class Decision(Enum):
    DELETE = "delete"
    KEEP_ANSWERED_NO = "kept: answered no"
    KEEP_DECLINED = "kept: declined"
    KEEP_CANCELLED = "kept: cancelled"
    KEEP_WRONG_COUNT = "kept: typed count did not match"


def _refusal(outcome: DeclinedElicitation | CancelledElicitation) -> Decision:
    if isinstance(outcome, DeclinedElicitation):
        return Decision.KEEP_DECLINED
    return Decision.KEEP_CANCELLED


def decide(outcome: ElicitationResult[Confirm]) -> Decision:
    """Delete only on an accepted form whose `ok` is True; keep in every other case.

    Pure; cannot fail.
    """
    if isinstance(outcome, AcceptedElicitation):
        return Decision.DELETE if outcome.data.ok else Decision.KEEP_ANSWERED_NO
    return _refusal(outcome)


def decide_purge(typed: ElicitationResult[TypedCount], expected: int) -> Decision:
    """Purge only if the human typed exactly the number of notes that would be removed.

    The first-step yes/no is enforced by the SDK before this runs (the purge tool
    annotates it as plain `Confirm`, so decline/cancel aborts the call), and an
    accepted "no" never reaches this step. Pure; cannot fail.
    """
    if isinstance(typed, AcceptedElicitation):
        return Decision.DELETE if typed.data.count == expected else Decision.KEEP_WRONG_COUNT
    return _refusal(typed)
