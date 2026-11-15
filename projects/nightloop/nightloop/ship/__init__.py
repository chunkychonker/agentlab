"""Turns a cycle whose review passed into an open, mergeable PR.

Public API::

    from nightloop.ship import GhShipper, MARKER

``GhShipper`` implements ``nightloop.cycle.Shipper``. Production wiring is
``GhShipper(repo, Path("logs/last-review.md"), sleep=time.sleep)``, with
``gh`` on PATH and authenticated. It never merges: merging, auto-merge and
conflict resolution are out of scope.
"""

from __future__ import annotations

from .gh_shipper import GhShipper
from .review_section import MARKER

__all__ = ["GhShipper", "MARKER"]
