"""What the CLI says about the night's claim steps: their labels, the dry-run plan, and each result.

Pure: no I/O. ``step_label`` names a step the same way in the plan and in a
real night's output, so the two cannot describe different steps.
"""

from __future__ import annotations

from typing import Sequence, Union

from ..claims.outcome import ShippedOutcome, ShippedStatus, StrandedOutcome, StrandedStatus
from ..claims.publish import ClaimStepResult, Nothing, Published, PublishFailed, Skipped

NIGHT_END_LABEL = "after the night"
_STRANDED_WHAT = "mark claims on recovery branches stranded"
_SHIPPED_WHAT = "mark tonight's shipped PRs done if already merged"
# Outcomes that are news; ALREADY_HANDLED and NO_CLAIM repeat every night for
# every old recovery branch and are left out.
_STRANDED_NEWS = {StrandedStatus.MARKED, StrandedStatus.FILED, StrandedStatus.UNREADABLE}

Outcome = Union[StrandedOutcome, ShippedOutcome]


def step_label(after_cycle: int) -> str:
    """``"before cycle 1"`` for 0, ``"after cycle N"`` for N >= 1.

    Raises ``TypeError`` for a non-int (or a bool) and ``ValueError`` for a
    negative number.
    """
    if isinstance(after_cycle, bool) or not isinstance(after_cycle, int):
        raise TypeError(f"after_cycle must be an int, got {type(after_cycle).__name__}")
    if after_cycle < 0:
        raise ValueError(f"after_cycle must be >= 0, got {after_cycle}")
    return "before cycle 1" if after_cycle == 0 else f"after cycle {after_cycle}"


def claim_plan(cycles: int) -> tuple[tuple[str, str], ...]:
    """``(label, what it does)`` for every claim step of a night of ``cycles`` cycles, in run order.

    A night that stops early on a usage limit runs fewer. Raises what
    ``step_label`` raises.
    """
    steps = [(step_label(n), _STRANDED_WHAT) for n in range(0, cycles + 1)]
    return (*steps, (NIGHT_END_LABEL, _SHIPPED_WHAT))


def render_claim_plan(cycles: int, backlog: str, *, enabled: bool) -> str:
    """The dry-run's claim section: one line per step, or one line saying claims are off."""
    if not enabled:
        return "claims: off (--no-claims); no backlog step runs"
    lines = [
        f"claims (backlog {backlog}): each step checks main is current after a fetch, "
        f"then commits and pushes only {backlog}, and only if it changed"
    ]
    lines += [f"  {label}: {what}" for label, what in claim_plan(cycles)]
    return "\n".join(lines)


def render_step(label: str, result: ClaimStepResult, outcomes: Sequence[Outcome]) -> str:
    """``claims <label>: <result>``, then one indented line per outcome worth reading.

    Raises ``AssertionError`` only for a result or status this module does
    not know, which would mean a sum type grew without it.
    """
    lines = [f"claims {label}: {_result_text(result)}"]
    for outcome in outcomes:
        text = _outcome_text(outcome)
        if text is not None:
            lines.append(f"  {text}")
    return "\n".join(lines)


def _result_text(result: ClaimStepResult) -> str:
    if isinstance(result, Published):
        return f"published {result.count} backlog edit(s) to main"
    if isinstance(result, Nothing):
        return "nothing to change"
    if isinstance(result, Skipped):
        return f"skipped: {result.reason}"
    if isinstance(result, PublishFailed):
        return f"publish FAILED: {result.reason}"
    raise AssertionError(f"unhandled claim step result {result!r}")


def _outcome_text(outcome: Outcome) -> str | None:
    if isinstance(outcome, StrandedOutcome):
        if outcome.status not in _STRANDED_NEWS:
            return None
        if outcome.status is StrandedStatus.UNREADABLE:
            return f"{outcome.ref}: unreadable ({outcome.reason})"
        return f"{outcome.ref}: {outcome.status.value}"
    if isinstance(outcome, ShippedOutcome):
        pr = f"PR #{outcome.pr}"
        status = outcome.status
        if status is ShippedStatus.MARKED_DONE:
            return f"{pr}: marked done"
        if status is ShippedStatus.UNREADABLE:
            return f"{pr}: pending, not marked ({outcome.reason})"
        if status is ShippedStatus.ITEM_NOT_FOUND:
            return f"{pr}: its claimed item is not on main; a human must look"
        if status is ShippedStatus.NO_CLAIM:
            return f"{pr}: claimed no item"
        if status is ShippedStatus.ALREADY_HANDLED:
            return f"{pr}: already handled"
    raise AssertionError(f"unhandled claim outcome {outcome!r}")
