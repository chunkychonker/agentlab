"""The backlog claim lifecycle: ``[ ]`` -> ``[building]`` -> ``[done #N]``, and stranded-claim recovery.

Public API::

    from nightloop.claims import (
        count_unclaimed, claim_from_diff, apply_stranded, file_stranded, mark_done,
        Claim, PrNumber, StrandedBranch, Rewritten, AlreadyHandled, ItemNotFound,
        reconcile_stranded, reconcile_shipped, StrandedOutcome, StrandedStatus,
        ShippedOutcome, ShippedStatus, ClaimSource, BacklogStore, UnshippedBranch,
        NoClaim, Unreadable, ClaimSourceError, BacklogFile, GitClaimSource,
        ClaimPorts, claim_step, pre_night_step, shipped_step, BacklogPublisher,
        GitBacklogPublisher, ClaimStepResult, Published, Nothing, Skipped, PublishFailed,
    )

Production wiring, with the checkout on a clean, fetched main::

    backlog = BacklogFile(repo)                  # repo / "BACKLOG.md"
    source = GitClaimSource(backlog)             # git and gh on PATH
    outcomes = reconcile_stranded(source, backlog)
    shipped = reconcile_shipped(source, backlog, PrNumber(41))

The reconcilers never commit or push. The claim steps (``step.py``, milestone
7b) do: each one asks the ``BacklogPublisher`` whether main is ready, runs a
reconciler, and publishes the backlog when an outcome's ``changed`` is true::

    ports = ClaimPorts(source, backlog, GitBacklogPublisher(backlog))
    result, outcomes = claim_step(ports)

Only ``GitBacklogPublisher`` commits or pushes. The agents make the ``[ ]`` ->
``[researching]`` -> ``[building]`` edits themselves; this package only
recognises them.
"""

from __future__ import annotations

from .backlog_file import BacklogFile
from .backlog_text import apply_stranded, count_unclaimed, file_stranded, mark_done
from .claim_diff import claim_from_diff
from .edit import AlreadyHandled, Edit, ItemNotFound, Rewritten
from .git_claim_source import GitClaimSource
from .git_publisher import GitBacklogPublisher
from .outcome import ShippedOutcome, ShippedStatus, StrandedOutcome, StrandedStatus
from .ports import BacklogStore, ClaimLookup, ClaimSource, ClaimSourceError, NoClaim, Unreadable, UnshippedBranch
from .publish import BacklogPublisher, ClaimStepResult, Nothing, Published, PublishFailed, Skipped
from .reconcile import reconcile_shipped, reconcile_stranded
from .step import ClaimPorts, claim_step, pre_night_step, shipped_step
from .values import Claim, PrNumber, StrandedBranch

__all__ = [
    "AlreadyHandled",
    "BacklogFile",
    "BacklogPublisher",
    "BacklogStore",
    "Claim",
    "ClaimLookup",
    "ClaimPorts",
    "ClaimSource",
    "ClaimSourceError",
    "ClaimStepResult",
    "Edit",
    "GitBacklogPublisher",
    "GitClaimSource",
    "ItemNotFound",
    "NoClaim",
    "Nothing",
    "PrNumber",
    "PublishFailed",
    "Published",
    "Rewritten",
    "ShippedOutcome",
    "ShippedStatus",
    "Skipped",
    "StrandedBranch",
    "StrandedOutcome",
    "StrandedStatus",
    "Unreadable",
    "UnshippedBranch",
    "apply_stranded",
    "claim_from_diff",
    "claim_step",
    "count_unclaimed",
    "file_stranded",
    "mark_done",
    "pre_night_step",
    "reconcile_shipped",
    "reconcile_stranded",
    "shipped_step",
]
