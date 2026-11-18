"""The backlog claim lifecycle: ``[ ]`` -> ``[building]`` -> ``[done #N]``, and stranded-claim recovery.

Public API::

    from nightloop.claims import (
        count_unclaimed, claim_from_diff, apply_stranded, file_stranded, mark_done,
        Claim, PrNumber, StrandedBranch, Rewritten, AlreadyHandled, ItemNotFound,
        reconcile_stranded, reconcile_shipped, StrandedOutcome, StrandedStatus,
        ShippedOutcome, ShippedStatus, ClaimSource, BacklogStore, UnshippedBranch,
        NoClaim, Unreadable, ClaimSourceError, BacklogFile, GitClaimSource,
    )

Production wiring, with the checkout on a clean, fetched main::

    backlog = BacklogFile(repo)                  # repo / "BACKLOG.md"
    source = GitClaimSource(backlog)             # git and gh on PATH
    outcomes = reconcile_stranded(source, backlog)
    shipped = reconcile_shipped(source, backlog, PrNumber(41))

Nothing here commits or pushes: when an outcome's ``changed`` is true, the
caller commits the backlog file. The agents make the ``[ ]`` ->
``[researching]`` -> ``[building]`` edits themselves; this package only
recognises them.
"""

from __future__ import annotations

from .backlog_file import BacklogFile
from .backlog_text import apply_stranded, count_unclaimed, file_stranded, mark_done
from .claim_diff import claim_from_diff
from .edit import AlreadyHandled, Edit, ItemNotFound, Rewritten
from .git_claim_source import GitClaimSource
from .outcome import ShippedOutcome, ShippedStatus, StrandedOutcome, StrandedStatus
from .ports import BacklogStore, ClaimLookup, ClaimSource, ClaimSourceError, NoClaim, Unreadable, UnshippedBranch
from .reconcile import reconcile_shipped, reconcile_stranded
from .values import Claim, PrNumber, StrandedBranch

__all__ = [
    "AlreadyHandled",
    "BacklogFile",
    "BacklogStore",
    "Claim",
    "ClaimLookup",
    "ClaimSource",
    "ClaimSourceError",
    "Edit",
    "GitClaimSource",
    "ItemNotFound",
    "NoClaim",
    "PrNumber",
    "Rewritten",
    "ShippedOutcome",
    "ShippedStatus",
    "StrandedBranch",
    "StrandedOutcome",
    "StrandedStatus",
    "Unreadable",
    "UnshippedBranch",
    "apply_stranded",
    "claim_from_diff",
    "count_unclaimed",
    "file_stranded",
    "mark_done",
    "reconcile_shipped",
    "reconcile_stranded",
]
