"""Applies recovered claims to the backlog: stranded branches, and a merged PR.

Pure: no I/O of its own; git and the file arrive as the ``ClaimSource`` and
``BacklogStore`` ports. A port of the decisions in ``.pipeline/run.sh``'s
``reconcile_stranded_claims`` and ``reconcile_shipped_claim``. It edits
the backlog only. It never commits, pushes, opens a PR or deletes a branch;
the caller commits when an outcome reports ``changed``.

bash's "always return 0" becomes a typed outcome per claim. Data problems
(no merge base, an empty diff, an item not on main) are outcomes. Errors
of the environment (``OSError``, ``ClaimSourceError``) propagate.
"""

from __future__ import annotations

from .backlog_text import apply_stranded, file_stranded, mark_done
from .edit import AlreadyHandled, ItemNotFound, Rewritten
from .outcome import ShippedOutcome, ShippedStatus, StrandedOutcome, StrandedStatus
from .ports import BacklogStore, ClaimSource, NoClaim, Unreadable, UnshippedBranch
from .values import Claim, PrNumber


def reconcile_stranded(source: ClaimSource, backlog: BacklogStore) -> list[StrandedOutcome]:
    """Mark every recovery branch's claim on the backlog as ``[stranded <ref>]``, one outcome per branch.

    For each branch: an item on main reading ``- [ ] <key>`` is rewritten
    (``MARKED``); an item that is not on main is appended with its
    continuation lines (``FILED``); an item under any other marker is left
    alone (``ALREADY_HANDLED``). Branches are handled in the source's order
    against the text the earlier ones produced, so a branch listed twice
    (local and remote copy) is marked once. The backlog is read once, and
    written once, only if some outcome changed it; a second run with
    nothing new writes nothing.

    Raises what the source raises (``ClaimSourceError``, ``OSError``)
    before anything is read or written, and ``OSError`` if the backlog
    cannot be read or written; a failed write leaves the file as it was.
    """
    branches = source.unshipped_claims()
    if not branches:
        return []
    original = text = backlog.read()
    outcomes: list[StrandedOutcome] = []
    for branch in branches:
        text, outcome = _strand_one(text, branch)
        outcomes.append(outcome)
    if text != original:
        backlog.write(text)
    return outcomes


def reconcile_shipped(source: ClaimSource, backlog: BacklogStore, pr: PrNumber) -> ShippedOutcome:
    """Mark merged PR ``pr``'s claim done: ``- [building] <key>`` becomes ``- [done #<pr>] <key>``.

    ``MARKED_DONE`` when rewritten (the only case that writes);
    ``ALREADY_HANDLED`` for any other marker on that key; ``ITEM_NOT_FOUND``
    when the key is not on main; ``NO_CLAIM`` / ``UNREADABLE`` from the
    source. Raises ``TypeError`` for a non-``PrNumber``, what the source
    raises (``OSError``), and ``OSError`` if the backlog cannot be read or
    written; a failed write leaves the file as it was.
    """
    if not isinstance(pr, PrNumber):
        raise TypeError(f"pr must be a PrNumber, got {type(pr).__name__}")
    lookup = source.merged_claim(pr)
    if isinstance(lookup, Unreadable):
        return ShippedOutcome(pr, ShippedStatus.UNREADABLE, lookup.reason)
    if isinstance(lookup, NoClaim):
        return ShippedOutcome(pr, ShippedStatus.NO_CLAIM)
    if not isinstance(lookup, Claim):
        raise TypeError(f"merged_claim must return a Claim, NoClaim or Unreadable, got {type(lookup).__name__}")
    edit = mark_done(backlog.read(), lookup.key, pr)
    if isinstance(edit, Rewritten):
        backlog.write(edit.text)
        return ShippedOutcome(pr, ShippedStatus.MARKED_DONE)
    if isinstance(edit, AlreadyHandled):
        return ShippedOutcome(pr, ShippedStatus.ALREADY_HANDLED)
    return ShippedOutcome(pr, ShippedStatus.ITEM_NOT_FOUND)


def _strand_one(text: str, branch: UnshippedBranch) -> tuple[str, StrandedOutcome]:
    """``(new text, outcome)`` for one branch; the fall-through to filing lives here."""
    lookup = branch.lookup
    if isinstance(lookup, Unreadable):
        return text, StrandedOutcome(branch.ref, StrandedStatus.UNREADABLE, lookup.reason)
    if isinstance(lookup, NoClaim):
        return text, StrandedOutcome(branch.ref, StrandedStatus.NO_CLAIM)
    edit = apply_stranded(text, lookup, branch.ref)
    status = StrandedStatus.MARKED
    if isinstance(edit, ItemNotFound):
        edit = file_stranded(text, lookup, branch.ref)
        status = StrandedStatus.FILED
    if isinstance(edit, Rewritten):
        return edit.text, StrandedOutcome(branch.ref, status)
    return text, StrandedOutcome(branch.ref, StrandedStatus.ALREADY_HANDLED)
