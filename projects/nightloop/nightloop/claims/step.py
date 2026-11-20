"""The night's claim steps: reconcile, then publish the backlog to main when something changed.

Pure: no I/O of its own. Git, ``gh`` and the file arrive as the
``ClaimSource``, ``BacklogStore`` and ``BacklogPublisher`` ports. The order
is fixed here, not left to the caller: ``publisher.ready()`` first (so a
checkout that is not ready gets no write at all), then the reconciler, then
``publisher.publish`` only if some outcome reports ``changed``.

Errors of the environment (``OSError``, ``ClaimSourceError``) propagate,
except in ``pre_night_step``, where a ``ClaimSourceError`` is ``Skipped``:
nothing has run yet that needs recovering, and claim bookkeeping is not a
reason to lose a night.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

from .outcome import ShippedOutcome, StrandedOutcome
from .ports import BacklogStore, ClaimSource, ClaimSourceError
from .publish import BacklogPublisher, ClaimStepResult, Nothing, Published, Skipped
from .reconcile import reconcile_shipped, reconcile_stranded
from .values import PrNumber


@dataclass(frozen=True)
class ClaimPorts:
    """The three capabilities every claim step uses, wired once at the entry point."""

    source: ClaimSource
    backlog: BacklogStore
    publisher: BacklogPublisher


def stranded_message(count: int) -> str:
    """The commit message for ``count`` stranded-claim edits."""
    return f"chore(backlog): reconcile {count} claim(s) stranded on unshipped branches"


def shipped_message(count: int, prs: Sequence[PrNumber]) -> str:
    """The commit message for ``count`` claims marked done for the merged PRs among ``prs``."""
    listed = ", ".join(f"#{pr}" for pr in prs)
    return f"chore(backlog): mark {count} claim(s) done for merged PR(s) {listed}"


def claim_step(ports: ClaimPorts) -> tuple[ClaimStepResult, tuple[StrandedOutcome, ...]]:
    """Mark recovery branches' claims stranded and publish that edit; run between cycles.

    Returns the step's result and one outcome per recovery branch (empty
    when skipped). ``Skipped`` when the publisher is not ready (nothing is
    read or written); ``Nothing`` when no outcome changed the backlog (no
    commit, no push); otherwise the publish's ``Published(count)`` or
    ``PublishFailed``.

    Raises ``ClaimSourceError`` if the recovery branches cannot be listed
    (before the backlog is read), ``OSError`` if git or the backlog file
    fails, and what ``BacklogFile.read`` raises for a non-UTF-8 backlog.
    """
    not_ready = ports.publisher.ready()
    if not_ready is not None:
        return not_ready, ()
    outcomes = tuple(reconcile_stranded(ports.source, ports.backlog))
    count = sum(1 for o in outcomes if o.changed)
    if count == 0:
        return Nothing(), outcomes
    return _publish(ports.publisher, stranded_message(count), count), outcomes


def pre_night_step(ports: ClaimPorts) -> tuple[ClaimStepResult, tuple[StrandedOutcome, ...]]:
    """``claim_step`` before the first cycle, where a ``ClaimSourceError`` is ``Skipped``, not raised.

    Nothing has run yet, so a source that cannot list its branches only
    costs tonight's bookkeeping. Every other failure is as in ``claim_step``.
    """
    try:
        return claim_step(ports)
    except ClaimSourceError as exc:
        return Skipped(f"cannot list recovery branches: {_one_line(str(exc))}"), ()


def shipped_step(
    ports: ClaimPorts, prs: Sequence[PrNumber]
) -> tuple[ClaimStepResult, tuple[ShippedOutcome, ...]]:
    """Try to mark each PR in ``prs`` done, and publish the edits; run once after the night.

    With no PRs this is ``Nothing`` and touches nothing, not even
    ``ready()``. A PR that is not merged yet reads ``UNREADABLE``, which is
    reported in the outcomes, is not an error, and is not a reason to
    publish. Only ``MARKED_DONE`` outcomes are published, in one commit.

    Raises ``TypeError`` if an entry is not a ``PrNumber`` (before any
    effect), and ``OSError`` if git, ``gh`` or the backlog file fails.
    """
    prs = tuple(prs)
    for pr in prs:
        if not isinstance(pr, PrNumber):
            raise TypeError(f"prs must hold PrNumber values, got {type(pr).__name__}")
    if not prs:
        return Nothing(), ()
    not_ready = ports.publisher.ready()
    if not_ready is not None:
        return not_ready, ()
    outcomes = tuple(reconcile_shipped(ports.source, ports.backlog, pr) for pr in prs)
    done = tuple(o.pr for o in outcomes if o.changed)
    if not done:
        return Nothing(), outcomes
    return _publish(ports.publisher, shipped_message(len(done), done), len(done)), outcomes


def _publish(publisher: BacklogPublisher, message: str, count: int) -> ClaimStepResult:
    failed = publisher.publish(message)
    return Published(count) if failed is None else failed


def _one_line(text: str) -> str:
    """The first non-blank line of ``text``, or a fixed note when there is none."""
    for line in text.splitlines():
        if line.strip():
            return line.strip()
    return "no detail given"
